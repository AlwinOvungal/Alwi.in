"""[Example from the print-3mf skill; self-contained — the library in ../scripts/sdf3mf.py has the same helpers.]
Usage: python3 THIS_FILE.py OUT.3mf

Christmas tree ornament v2 -> single-colour 3MF.

X = width, Y = height (trunk bottom 0 .. loop top HEIGHT), Z = depth (flat back on z=0, print lying down).
Each tier is its own puffy skirt: thickest along its scalloped bottom rim, thinner toward its top,
so every tier overhangs the one below with a crisp crease (like the reference render).
"""
import sys, zipfile
import numpy as np
from scipy.ndimage import distance_transform_edt, gaussian_filter
from skimage.measure import marching_cubes
import trimesh

OUT = sys.argv[1]
RES = 0.08
HEIGHT = 40.0
HOLE_D, LOOP_WALL, LOOP_T = 4.0, 1.7, 3.6
EDGE_WALL = 0.7              # short vertical wall at the flat back (bed contact)

xs = (np.arange(-15, 15 + RES, RES) + 0.0137).astype(np.float32)
ys = (np.arange(-1, 41.5 + RES, RES) + 0.0231).astype(np.float32)
zs = (np.arange(-1, 13 + RES, RES) + 0.0173).astype(np.float32)
Xr, Yr = np.meshgrid(xs, ys, indexing='ij')


def smin(a, b, k):
    h = np.clip(0.5 + 0.5 * (b - a) / k, 0, 1)
    return b + (a - b) * h - k * h * (1 - h)


def polygon(X, Y, pts):
    pts = np.asarray(pts, float)
    d = (X - pts[0, 0]) ** 2 + (Y - pts[0, 1]) ** 2
    s = np.ones_like(X)
    for i in range(len(pts)):
        vi, vj = pts[i], pts[i - 1]
        ex, ey = vj - vi
        wx, wy = X - vi[0], Y - vi[1]
        t = np.clip((wx * ex + wy * ey) / (ex * ex + ey * ey), 0, 1)
        d = np.minimum(d, (wx - ex * t) ** 2 + (wy - ey * t) ** 2)
        c1 = Y >= vi[1]; c2 = Y < vj[1]; c3 = ex * wy > ey * wx
        s = np.where((c1 & c2 & c3) | (~c1 & ~c2 & ~c3), -s, s)
    return s * np.sqrt(d)


def star5(X, Y, cx, cy, r, rf):
    k1 = np.array([0.809016994375, -0.587785252292]); k2 = np.array([-k1[0], k1[1]])
    px, py = np.abs(X - cx), Y - cy
    for k in (k1, k2):
        dd = np.maximum(k[0] * px + k[1] * py, 0) * 2
        px, py = px - dd * k[0], py - dd * k[1]
    px = np.abs(px); py = py - r
    bax, bay = rf * -k1[1], rf * k1[0] - 1
    h = np.clip((px * bax + py * bay) / (bax * bax + bay * bay), 0, r)
    return np.hypot(px - bax * h, py - bay * h) * np.sign(py * bax - px * bay)


# ---------- layout in "picture millimetres" (measured from the reference), scaled by K ----------
STAR_C, STAR_R = 31.6, 6.0
LOOP_OVERLAP = 1.6
ring_r = HOLE_D / 2 + LOOP_WALL
K = (HEIGHT - 2 * ring_r + LOOP_OVERLAP) / (STAR_C + STAR_R)
X, Y = Xr / K, Yr / K          # design-space coordinates


def tier_outline(yb, yt, hw, top_hw, lobes):
    h = yt - yb
    rc = h * 0.27                               # curled outer ends
    rm = h * 0.34                               # scallops along the rim (soft)
    d = polygon(X, Y, [(-hw + 0.3, yb + rc), (hw - 0.3, yb + rc), (top_hw, yt), (-top_hw, yt)])
    d = smin(d, np.hypot(X, Y - (yt - top_hw * 0.6)) - top_hw * 1.05, 1.5)
    ends = hw - rc
    d = smin(d, np.hypot(X + ends, Y - (yb + rc)) - rc, 1.2)
    d = smin(d, np.hypot(X - ends, Y - (yb + rc)) - rc, 1.2)
    for x in np.linspace(-ends, ends, lobes + 2)[1:-1]:
        d = smin(d, np.hypot(X - x, Y - (yb + rm + 0.25)) - rm, 1.8)
    return d * K


parts = []
# trunk: rounded column
trunk = polygon(X, Y, [(-4.3, 0.0), (4.3, 0.0), (4.1, 8.0), (-4.1, 8.0)]) * K
parts.append(dict(d=trunk, D0=6.2, D1=6.2, R=3.6, y0=0, y1=1, xprofile=4.2 * K))
# tiers, bottom to top: (rim y, top y, half width, top half width, rim lobes, rim depth)
for yb, yt, hw, thw, lobes, D in [(2.9, 14.5, 12.6, 4.6, 2, 10.6),
                                  (11.0, 21.8, 10.9, 3.6, 1, 9.8),
                                  (18.7, 28.6, 8.5, 2.6, 1, 9.0)]:
    parts.append(dict(d=tier_outline(yb, yt, hw, thw, lobes), D0=D, D1=D * 0.55, R=3.4,
                      y0=yb * K, y1=yt * K))
# chunky star with rounded points
star = (star5(X, Y, 0, STAR_C, STAR_R - 1.15, 0.56) - 1.15) * K
parts.append(dict(d=star, D0=7.0, D1=7.0, R=2.4, y0=0, y1=1))
# hanging loop (built directly in mm so the hole is exactly HOLE_D)
LOOP_C = HEIGHT - ring_r
ring = np.abs(np.hypot(Xr, Yr - LOOP_C) - (HOLE_D / 2 + LOOP_WALL / 2)) - LOOP_WALL / 2
parts.append(dict(d=ring, D0=LOOP_T, D1=LOOP_T, R=1.5, y0=0, y1=1))

# ---------- front height-field: later parts sit on top (hard max => crisp creases) ----------
H = np.zeros_like(Xr)
sil = np.full_like(Xr, 1e9)
for p in parts:
    mask = p['d'] <= 0
    if 'xprofile' in p:   # round column: thickness depends on x only (no pyramid ridges)
        dist = np.clip(p['xprofile'] - np.abs(Xr), 0, None)
    else:                 # smooth the medial-axis ridges of the distance field
        dist = gaussian_filter(distance_transform_edt(mask) * RES, 1.1 / RES)
    t = np.clip(dist / p['R'], 0, 1)
    f = np.clip((Yr - p['y0']) / (p['y1'] - p['y0']), 0, 1)
    depth = p['D0'] + (p['D1'] - p['D0']) * f ** 0.8
    h = EDGE_WALL + (depth - EDGE_WALL) * np.sqrt(1 - (1 - t) ** 2)
    H = np.where(mask, np.maximum(H, h), H)
    sil = np.minimum(sil, p['d'])
H = gaussian_filter(H, 0.12 / RES)           # anti-alias only, keeps creases
H = np.maximum(H, EDGE_WALL)
hole = np.hypot(Xr, Yr - LOOP_C) - HOLE_D / 2
sil = np.maximum(sil, -hole)

Z = zs[None, None, :]
body = np.maximum(np.maximum(Z - H[..., None], -Z), sil[..., None])

v, f, _, _ = marching_cubes(body, 0.0, spacing=(RES, RES, RES))
v = np.round(v + np.array([xs[0], ys[0], zs[0]]), 4)
m = trimesh.Trimesh(v, f[:, ::-1], process=True)
for _ in range(5):
    m.update_faces(m.nondegenerate_faces(height=1e-6)); m.update_faces(m.unique_faces())
    m.remove_unreferenced_vertices(); m.merge_vertices()
trimesh.repair.fill_holes(m); trimesh.repair.fix_normals(m)
print('K %.3f' % K, 'watertight', m.is_watertight, 'vol %.2f cm3' % (m.volume / 1000), 'tris', len(m.faces),
      'size', (m.bounds[1] - m.bounds[0]).round(2).tolist())
m.export(OUT.replace('.3mf', '.stl'))

c = (m.bounds[0] + m.bounds[1]) / 2
vv = m.vertices - np.array([c[0], c[1], 0])
vs = '\n'.join(f'<vertex x="{a:.4f}" y="{b:.4f}" z="{d:.4f}"/>' for a, b, d in vv)
ts = '\n'.join(f'<triangle v1="{a}" v2="{b}" v3="{d}"/>' for a, b, d in m.faces)
model = f'''<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
<metadata name="Title">Christmas Tree Ornament</metadata>
<resources>
<basematerials id="1"><base name="Grass Green" displaycolor="#3DA34AFF"/></basematerials>
<object id="2" name="Christmas Tree Ornament" type="model" pid="1" pindex="0"><mesh><vertices>
{vs}
</vertices><triangles>
{ts}
</triangles></mesh></object>
</resources>
<build><item objectid="2" transform="1 0 0 0 1 0 0 0 1 128 128 0"/></build>
</model>'''
ct = '''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>'''
rels = '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>'''
with zipfile.ZipFile(OUT, 'w', zipfile.ZIP_DEFLATED) as z:
    z.writestr('[Content_Types].xml', ct); z.writestr('_rels/.rels', rels); z.writestr('3D/3dmodel.model', model)
print('wrote', OUT)
