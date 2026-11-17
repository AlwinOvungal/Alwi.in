"""[Example from the print-3mf skill; self-contained — the library in ../scripts/sdf3mf.py has the same helpers.]
Usage: python3 THIS_FILE.py OUT.3mf

Figma-logo fridge magnet -> multi-part 3MF (white plate + 5 colour inlays).

X = width (35), Y = height (50), Z = thickness (5). Printed face-up; magnet pocket on the bottom.
"""
import sys, zipfile
import numpy as np
from skimage.measure import marching_cubes
import trimesh

OUT = sys.argv[1]
RES = 0.12
W, H, T = 35.0, 50.0, 5.0
B = 2.5                     # white border around the logo
U = 15.0                    # logo cell size
INLAY = 1.6                 # colour layer thickness (top of part)
TOP_ROUND, INLAY_ROUND = 1.0, 0.7
FILLET = 3.0                # concave fillet on the outline
MAG_D, MAG_DEPTH = 8.3, 1.5  # 8 mm magnet + clearance, 1.5 mm deep
MAG_XY = (17.5, 25.0)

xs = (np.arange(-1.0, W + 1 + RES, RES) + 0.0137).astype(np.float32)
ys = (np.arange(-1.0, H + 1 + RES, RES) + 0.0231).astype(np.float32)
zs = (np.arange(-1.0, T + 1 + RES, RES) + 0.0173).astype(np.float32)
X2, Y2 = np.meshgrid(xs, ys, indexing='ij')


def circle(cx, cy, r):
    return np.hypot(X2 - cx, Y2 - cy) - r


def box(x0, y0, x1, y1):
    qx = np.maximum(x0 - X2, X2 - x1); qy = np.maximum(y0 - Y2, Y2 - y1)
    return np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0)


def smin(a, b, k):
    h = np.clip(0.5 + 0.5 * (b - a) / k, 0, 1)
    return b + (a - b) * h - k * h * (1 - h)


def cell(col, row):  # row 0 = top
    x0 = B + col * U; y0 = H - B - (row + 1) * U
    return x0, y0, x0 + U, y0 + U, x0 + U / 2, y0 + U / 2


def shape(col, row, rect):
    x0, y0, x1, y1, cx, cy = cell(col, row)
    d = circle(cx, cy, U / 2)
    if rect == 'right': d = np.minimum(d, box(cx, y0, x1, y1))
    if rect == 'left': d = np.minimum(d, box(x0, y0, cx, y1))
    if rect == 'topright': d = np.minimum(d, box(cx, cy, x1, y1))
    return d


shapes = {
    'Orange': (shape(0, 0, 'right'), 'F24E1E'),
    'Pink': (shape(1, 0, 'left'), 'FF7262'),
    'Purple': (shape(0, 1, 'right'), 'A259FF'),
    'Blue': (shape(1, 1, None), '1ABCFE'),
    'Green': (shape(0, 2, 'topright'), '0ACF83'),
}
logo = np.minimum.reduce([d for d, _ in shapes.values()])

# Outline = logo grown by B, with concave corners filleted (morphological closing, radius FILLET).
from scipy.ndimage import distance_transform_edt, map_coordinates
HR = 0.025
hx = np.arange(xs[0] - 6, xs[-1] + 6, HR); hy = np.arange(ys[0] - 6, ys[-1] + 6, HR)
_X2, _Y2 = X2, Y2
X2, Y2 = np.meshgrid(hx, hy, indexing='ij')
logo_hi = np.minimum.reduce([shape(0, 0, 'right'), shape(1, 0, 'left'), shape(0, 1, 'right'),
                             shape(1, 1, None), shape(0, 2, 'topright')])
X2, Y2 = _X2, _Y2
dil = logo_hi <= B + FILLET
sd = (distance_transform_edt(~dil) - distance_transform_edt(dil)) * HR
outline_hi = sd + FILLET
ix = (X2 - hx[0]) / HR; iy = (Y2 - hy[0]) / HR
outline = map_coordinates(outline_hi, [ix, iy], order=1, mode='nearest').astype(np.float32)

Z = zs[None, None, :]


def round_intersect(a, b, r):
    return np.sqrt(np.maximum(a + r, 0) ** 2 + np.maximum(b + r, 0) ** 2) + np.minimum(np.maximum(a, b) + r, 0) - r


plate = np.maximum(round_intersect(outline[..., None], Z - T, TOP_ROUND), -Z)
mag = np.maximum(np.hypot(X2 - MAG_XY[0], Y2 - MAG_XY[1])[..., None] - MAG_D / 2, Z - MAG_DEPTH)
plate = np.maximum(plate, -mag)
logo_prism = np.maximum(logo[..., None], (T - INLAY) - Z)       # where colour goes (unrounded)
white = np.maximum(plate, -logo_prism)

fields = {'White plate': (white, 'F4F2EE')}
for n, (d, c) in shapes.items():
    inl = round_intersect(d[..., None], Z - T, INLAY_ROUND)
    fields[n] = (np.maximum(inl, (T - INLAY) - Z), c)


def mesh(field):
    v, f, _, _ = marching_cubes(field, 0.0, spacing=(RES, RES, RES))
    v = np.round(v + np.array([xs[0], ys[0], zs[0]]), 4)
    m = trimesh.Trimesh(v, f[:, ::-1], process=True)
    m.merge_vertices()
    for _ in range(5):
        m.update_faces(m.nondegenerate_faces(height=1e-6))
        m.update_faces(m.unique_faces())
        m.remove_unreferenced_vertices()
        m.merge_vertices()
    trimesh.repair.fill_holes(m)
    trimesh.repair.fix_normals(m)
    return m


parts = {}
for n, (fld, c) in fields.items():
    m = mesh(fld)
    parts[n] = (m, c)
    print(f'{n:12s} watertight={m.is_watertight} vol={m.volume/1000:.2f}cm3 tris={len(m.faces)} bounds={m.bounds.round(2).tolist()}')
    m.export(OUT.replace('.3mf', '_' + n.split()[0].lower() + '.stl'))

shift = -np.array([W / 2, H / 2, 0])
mats = ''.join(f'<base name="{n}" displaycolor="#{c}FF"/>' for n, (_, c) in parts.items())
objs, comps = [], []
for i, (n, (m, c)) in enumerate(parts.items()):
    oid = i + 2
    vs = '\n'.join(f'<vertex x="{a:.4f}" y="{b:.4f}" z="{d:.4f}"/>' for a, b, d in m.vertices + shift)
    ts = '\n'.join(f'<triangle v1="{a}" v2="{b}" v3="{d}"/>' for a, b, d in m.faces)
    objs.append(f'<object id="{oid}" name="{n}" type="model" pid="1" pindex="{i}"><mesh><vertices>\n{vs}\n</vertices><triangles>\n{ts}\n</triangles></mesh></object>')
    comps.append(f'<component objectid="{oid}"/>')
model = f'''<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
<metadata name="Title">Figma Logo Magnet</metadata>
<resources>
<basematerials id="1">{mats}</basematerials>
{''.join(objs)}
<object id="20" name="Figma Logo Magnet" type="model"><components>{''.join(comps)}</components></object>
</resources>
<build><item objectid="20" transform="1 0 0 0 1 0 0 0 1 128 128 0"/></build>
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
