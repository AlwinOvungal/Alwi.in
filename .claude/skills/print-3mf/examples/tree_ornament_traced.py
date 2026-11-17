"""Christmas tree ornament traced from a reference image (print-3mf skill) — the best tree example.
Crop box and crease rows (x0, x1, y0, y1, tiers, STAR_TOP) are specific to that design sheet: re-measure
them with the green-mask row-width printout described in SKILL.md for a new picture.

Outline traced from the reference's flat back view; per-layer puffy pillows (trunk, 3 tiers, star)
with depths measured from the side view; analytic hanging loop with an exact 4 mm hole.
Flat back on the bed (z=0), no supports. Usage: python3 tree3.py REFERENCE.webp OUT.3mf
"""
import sys
sys.path.insert(0, '/home/user/Alwi.in/.claude/skills/print-3mf/scripts')
from sdf3mf import *
from PIL import Image
from scipy.ndimage import binary_fill_holes, label, map_coordinates, distance_transform_edt, gaussian_filter

REF, OUT = sys.argv[1], sys.argv[2]
HEIGHT, HOLE_D, LOOP_WALL, LOOP_T, LOOP_OVERLAP = 40.0, 4.0, 1.7, 3.5, 1.6
EDGE_WALL = 0.7

# ---- 1. trace the flat back-view silhouette (image px, y down) -----------------------------
im = np.asarray(Image.open(REF).convert('RGB')).astype(int)
x0, x1, y0, y1 = 760, 1080, 100, 600
sub = im[y0:y1, x0:x1]
green = (sub[..., 1] > sub[..., 0] + 25) & (sub[..., 1] > sub[..., 2] + 25)
lab, n = label(green); sizes = np.bincount(lab.ravel()); sizes[0] = 0
sil_px = binary_fill_holes(lab == sizes.argmax())
STAR_TOP = 190 - y0                  # rows above this are the drawn (too small) loop -> rebuilt below
sil_px[:STAR_TOP] = False
rows = np.where(sil_px.any(1))[0]; BOTTOM = rows.max() + 1
cols = np.where(sil_px.any(0))[0]; CX = (cols.min() + cols.max()) / 2

# scale: tree body (star top .. trunk bottom) + loop = HEIGHT
ring_r = HOLE_D / 2 + LOOP_WALL
S = (HEIGHT - 2 * ring_r + LOOP_OVERLAP) / (BOTTOM - rows.min())      # mm per px
print(f'scale {S:.4f} mm/px, body {(BOTTOM - rows.min()) * S:.1f} mm, width {(cols.max() - cols.min()) * S:.1f} mm')

# signed distance of the traced outline in mm (px grid), smoothed to remove pixel steps
sd_px = (distance_transform_edt(~sil_px) - distance_transform_edt(sil_px)) * S
sd_px = gaussian_filter(sd_px, 1.2)

g = Grid((-14, 14), (-1, 41), (-1, 13), res=0.08)
X, Y, Z = g.X2, g.Y2, g.Z
PX = CX + X / S                       # mm -> image px (x)
PY = BOTTOM - Y / S                   # mm -> image px (y down; trunk bottom at y=0)
sil = map_coordinates(sd_px, [PY, PX], order=1, mode='constant', cval=5.0).astype(np.float32)
inside = sil <= 0


def mm_y(py):                         # image row -> model y (mm)
    return (BOTTOM - (py - y0)) * S


# ---- 2. layers: rim lines (image rows, measured creases) and side-view depths (mm) -----------
def scallop_rim(yc_img, width_px, amp_px=14, bumps=3):
    """Lower edge of a tier: gently scalloped line, returned as model-y as a function of X."""
    period = width_px * S / bumps
    c = np.sqrt(np.cos(np.pi * X / period) ** 2 + 0.03) / np.sqrt(1.03)   # lobes; notch slightly rounded
    return mm_y(yc_img) - amp_px * S * np.sqrt(np.clip(1 - (1 - c) ** 2, 0, 1))   # lobes hang down


# (rim row, row of crease above, rim depth, depth at the crease above, rim width px)
tiers = [(522, 433, 11.2, 7.2, 263),
         (434, 350, 10.4, 6.4, 245),
         (350, 242, 9.0, 4.2, 190)]
H = np.zeros_like(X)

# trunk: round column, x-only profile so it doesn't look pyramidal
tw = 4.0
trunk = inside & (Y < mm_y(500)) & (np.abs(X) <= tw)
H = np.maximum(H, np.where(trunk, EDGE_WALL + (6.1 - EDGE_WALL) *
                           np.sqrt(np.clip(1 - (np.abs(X) / tw) ** 2, 0, 1)), 0))

for rim_row, top_row, d_rim, d_top, wpx in tiers:
    rim = scallop_rim(rim_row, wpx)
    top = mm_y(top_row) + 1.5                     # continue a little under the tier above
    mask = inside & (Y >= rim) & (Y <= top)
    f = np.clip((Y - mm_y(rim_row)) / (mm_y(top_row) - mm_y(rim_row)), 0, 1)
    depth = d_rim + (d_top - d_rim) * f ** 0.9
    # pillow on the tier's own outline (silhouette sides + rim edge)
    H = np.maximum(H, pillow_height(mask, g.res, depth, radius=3.0, edge_wall=EDGE_WALL, smooth=0.8))

STAR_W = 124 * S                                  # measured star width
STAR_R = STAR_W / 2 / 0.951
star_top_y = mm_y(rows.min() + y0)
star_d = star5(X, Y, 0, star_top_y - STAR_R, STAR_R - 1.2, 0.56) - 1.2
star = star_d <= 0
H = np.maximum(H, pillow_height(star, g.res, 7.2, radius=1.5, edge_wall=3.0, smooth=0.25))
inside = inside | star

# ---- 3. hanging loop, true mm --------------------------------------------------------------
star_top = mm_y(rows.min() + y0)
LOOP_C = star_top - LOOP_OVERLAP + ring_r
ring = ring2(X, Y, 0, LOOP_C, HOLE_D / 2, LOOP_WALL)
loop_mask = ring <= 0
H = np.maximum(H, pillow_height(loop_mask, g.res, LOOP_T, radius=1.2, edge_wall=EDGE_WALL, smooth=0.0))
sil = np.where(Y > mm_y(262), star_d, sil)          # star region: analytic outline
sil = np.minimum(sil, star_d)
outline = np.minimum(sil, ring)
outline = np.maximum(outline, -(np.hypot(X, Y - LOOP_C) - HOLE_D / 2))      # keep the hole open

body = relief_body(H, outline, Z, antialias=0.16, res=g.res, edge_wall=EDGE_WALL)
m = mesh_from_field(body, g)
report('Tree', m)
m.export(OUT.replace('.3mf', '.stl'))
write_3mf(OUT, [('Christmas Tree Ornament', m, '3DA34A')], title='Christmas Tree Ornament')
verify_3mf(OUT)
print('hole centre y = %.2f, hole d = %.2f mm, loop top = %.2f' % (LOOP_C, HOLE_D, LOOP_C + ring_r))
