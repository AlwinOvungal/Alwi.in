"""[Example from the print-3mf skill; self-contained — the library in ../scripts/sdf3mf.py has the same helpers.]
Usage: python3 THIS_FILE.py OUT.3mf

Traffic-cone fridge magnet -> multi-part 3MF (orange body + white stripes).

Coordinates (mm): X = width, Y = depth (Y=0 is the flat back), Z = up.
Printed standing on its base; the magnet pocket is on the flat back.
"""
import sys, zipfile
import numpy as np
from skimage.measure import marching_cubes
import trimesh

OUT = sys.argv[1]
RES = 0.2  # voxel size, mm

# ---------------- parameters ----------------
SLAB_W, SLAB_D, SLAB_H, SLAB_R = 39.0, 23.0, 8.5, 3.0   # base block
CONE_A = np.array([0.0, 5.2, 7.0]); CONE_R1 = 14.8          # bottom sphere of round cone
CONE_B = np.array([0.0, 1.9, 40.0]); CONE_R2 = 5.0          # top sphere (top at z=45)
BAND_T, BAND_ROUND = 1.6, 1.6                               # stripe bulge / edge rounding
BANDS = [(9.2, 18.6), (25.8, 34.4)]                         # z ranges of white stripes
BACK_PLATE = 2.4                                            # orange back thickness behind stripes
MAG_D, MAG_DEPTH, MAG_Z = 8.3, 2.1, 17.0                    # 8x2 mm magnet + clearance
BACK_ROUND = 0.8                                            # soften edges on back face


def round_cone(p, a, b, r1, r2):
    ba = b - a; l2 = ba @ ba; rr = r1 - r2; a2 = l2 - rr * rr; il2 = 1.0 / l2
    pa = p - a; y = pa @ ba; z = y - l2
    q = pa * l2 - y[..., None] * ba
    x2 = np.einsum('...i,...i', q, q); y2 = y * y * l2; z2 = z * z * l2
    k = np.sign(rr) * rr * rr * x2
    d3 = (np.sqrt(x2 * a2 * il2) + y * rr) * il2 - r1
    d2 = np.sqrt(x2 + y2) * il2 - r1
    d1 = np.sqrt(x2 + z2) * il2 - r2
    return np.where(np.sign(z) * a2 * z2 > k, d1, np.where(np.sign(y) * a2 * y2 < k, d2, d3))


def round_box(p, c, half, r):
    q = np.abs(p - c) - (half - r)
    return np.linalg.norm(np.maximum(q, 0), axis=-1) + np.minimum(q.max(-1), 0) - r


def round_intersect(a, b, r):
    return np.sqrt(np.maximum(a + r, 0) ** 2 + np.maximum(b + r, 0) ** 2) + np.minimum(np.maximum(a, b) + r, 0) - r


def smax(a, b, k):
    h = np.clip(0.5 - 0.5 * (b - a) / k, 0, 1)
    return b + (a - b) * h + k * h * (1 - h)


xs = np.arange(-22, 22 + RES, RES) + 0.0137
ys = np.arange(-1.0, 26 + RES, RES) + 0.0731
zs = np.arange(-1.0, 47 + RES, RES) + 0.0573
X, Y, Z = np.meshgrid(xs, ys, zs, indexing='ij')
P = np.stack([X, Y, Z], -1)

cone = round_cone(P, CONE_A, CONE_B, CONE_R1, CONE_R2)
# base: rounded box extended below z=0 and behind y=0 so the cuts leave flat bottom/back
slab = round_box(P, np.array([0, (SLAB_D - 4) / 2, (SLAB_H - 4) / 2]),
                 np.array([SLAB_W / 2, (SLAB_D + 4) / 2, (SLAB_H + 4) / 2]), SLAB_R)
bands = np.full_like(X, 1e9)
for z0, z1 in BANDS:
    zc, hh = (z0 + z1) / 2, (z1 - z0) / 2
    bands = np.minimum(bands, round_intersect(cone - BAND_T, np.abs(Z - zc) - hh, BAND_ROUND))

body = np.minimum(np.minimum(cone, slab), bands)
body = smax(body, -Y, BACK_ROUND)          # flat back at y=0, softened edge
body = np.maximum(body, -Z)                # flat bottom at z=0
# magnet pocket (cylinder along Y on the back face)
mag = np.maximum(np.hypot(X, Z - MAG_Z) - MAG_D / 2, Y - MAG_DEPTH)
body = np.maximum(body, -mag)

white_region = np.maximum(bands, BACK_PLATE - Y)   # stripes, but not on the back plate
white = np.maximum(body, white_region)
orange = np.maximum(body, -white_region)


def mesh(field):
    v, f, _, _ = marching_cubes(field, 0.0, spacing=(RES, RES, RES))
    v += np.array([xs[0], ys[0], zs[0]])
    v = np.round(v, 4)
    m = trimesh.Trimesh(v, f[:, ::-1], process=True)
    m.merge_vertices()
    for _ in range(5):
        m.update_faces(m.nondegenerate_faces(height=1e-6))
        m.update_faces(m.unique_faces())
        m.remove_unreferenced_vertices()
        m.merge_vertices()
    trimesh.repair.fill_holes(m)
    trimesh.repair.fix_normals(m)
    if m.volume < 0:
        m.invert()
    return m


parts = {'Orange body': (mesh(orange), 'FF6A13'), 'White stripes': (mesh(white), 'F5F3EE')}
for n, (m, _) in parts.items():
    print(n, 'watertight', m.is_watertight, 'volume %.1f cm3' % (m.volume / 1000), 'tris', len(m.faces), 'bounds', m.bounds.round(2).tolist())
    m.export(OUT.replace('.3mf', '_' + n.split()[0].lower() + '.stl'))

# ---------------- write 3MF (one object made of two parts) ----------------
# Shift so model sits centred on the plate origin with z=0 as bed.
allb = np.vstack([m.bounds for m, _ in parts.values()])
shift = -np.array([(allb[:, 0].min() + allb[:, 0].max()) / 2, (allb[:, 1].min() + allb[:, 1].max()) / 2, 0])
objs, comps = [], []
mats = ''.join(f'<base name="{n}" displaycolor="#{c}FF"/>' for n, (_, c) in parts.items())
for i, (n, (m, c)) in enumerate(parts.items()):
    oid = i + 2
    v = m.vertices + shift  # shift is integer-free; keep 4dp
    vs = '\n'.join(f'<vertex x="{a:.4f}" y="{b:.4f}" z="{d:.4f}"/>' for a, b, d in v)
    ts = '\n'.join(f'<triangle v1="{a}" v2="{b}" v3="{d}"/>' for a, b, d in m.faces)
    objs.append(f'<object id="{oid}" name="{n}" type="model" pid="1" pindex="{i}"><mesh><vertices>\n{vs}\n</vertices><triangles>\n{ts}\n</triangles></mesh></object>')
    comps.append(f'<component objectid="{oid}"/>')
model = f'''<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
<metadata name="Title">Traffic Cone Magnet</metadata>
<resources>
<basematerials id="1">{mats}</basematerials>
{''.join(objs)}
<object id="10" name="Traffic Cone Magnet" type="model"><components>{''.join(comps)}</components></object>
</resources>
<build><item objectid="10" transform="1 0 0 0 1 0 0 0 1 128 128 0"/></build>
</model>'''
ct = '''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>'''
rels = '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>'''
with zipfile.ZipFile(OUT, 'w', zipfile.ZIP_DEFLATED) as z:
    z.writestr('[Content_Types].xml', ct)
    z.writestr('_rels/.rels', rels)
    z.writestr('3D/3dmodel.model', model)
print('wrote', OUT)
