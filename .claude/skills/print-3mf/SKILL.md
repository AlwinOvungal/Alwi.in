---
name: print-3mf
description: Create 3D-printable 3MF (and STL) files locally from a picture, design sheet or description — fridge magnets, ornaments, keychains, figurines, logo inlays — including multi-colour parts, magnet pockets and hanging loops, with a watertight check and a rendered preview. Use whenever the user asks for a 3MF/STL/3D-print file of an object. Runs entirely in Python here; no external service or account.
---

# Make 3D-printable 3MF files

Build the model as **signed distance fields (SDFs)** in NumPy, mesh each colour part with
marching cubes, verify it, render a preview, and write a multi-part 3MF that Bambu Studio,
OrcaSlicer and PrusaSlicer open as one object with one part per filament.

Everything lives in this skill folder:

| File | What it is |
|---|---|
| `scripts/sdf3mf.py` | Library: `Grid`, 2D/3D primitives, booleans, `offset_outline`, `pillow_height`/`relief_body`, `mesh_from_field`, `write_3mf`, `verify_3mf`, `report` |
| `scripts/render_preview.py` | Smooth-shaded PNG preview (front, side, back, 3/4) of STL/3MF parts — always look at it |
| `examples/star_magnet_lib_demo.py` | Shortest complete example using the library (2-colour magnet + pocket) |
| `examples/logo_inlay_magnet.py` | Flat logo magnet: backing plate + 5 colour inlays, exact outer size, filleted outline |
| `examples/traffic_cone_magnet.py` | Upright 3D figure from 3D primitives (round cone, round box), raised stripes as a 2nd colour |
| `examples/tree_ornament_traced.py` | **Best relief example**: outline traced from the reference picture, per-layer pillows with depths measured from the side view, scalloped tier rims, analytic star + loop |
| `examples/tree_ornament_relief.py` | Older all-analytic version of the same tree (shapes built from polygons/circles) |

## Setup (once per container)

```bash
pip install -q numpy scipy scikit-image trimesh pillow
```

Write model scripts and outputs to the session scratchpad (or wherever the user asks), not
into the repository. Start each model script with:

```python
import sys; sys.path.insert(0, '/home/user/Alwi.in/.claude/skills/print-3mf/scripts')
from sdf3mf import *
```

## Workflow

**When the user gives a picture, trace it instead of guessing shapes.** Measure the reference
in pixels first (this gets proportions right on the first try):

```python
im = np.asarray(Image.open(REF).convert('RGB')).astype(int)
mask = (im[..., 1] > im[..., 0] + 25) & (im[..., 1] > im[..., 2] + 25)   # adapt to the object colour
for y in range(y0, y1, 12):                      # row widths -> creases are local minima
    xs = np.where(mask[y, x0:x1])[0]; print(y, xs.min(), xs.max(), xs.max() - xs.min())
```

Use a flat/back-view silhouette as the outline (signed EDT of the mask, `gaussian_filter` ~1 px,
`map_coordinates` onto the grid — see `tree_ornament_traced.py`), the side view's row widths as
the per-layer depths, and rebuild small functional features (holes, loops, stars) analytically
in true mm. Scale so the stated total height is met *including* the rebuilt features.

1. **Read the reference.** Pull dimensions from the user's labels; when labels contradict the
   drawing (e.g. "30 mm deep" on a visibly thin side view), measure the drawing in pixels,
   keep the most trustworthy number (usually total height), and tell the user what you chose.
   Note colours, which faces are flat, holes, loops and magnet pockets.
2. **Choose the print orientation first** — it decides the geometry:
   - Flat pieces (magnets, ornaments, keychains): flat back on the bed (z = 0), detail faces up.
     Needs no supports. A "puffy both sides" look needs supports or two glued halves — offer that
     as an option rather than doing it silently.
   - Upright figures: base on the bed; avoid overhangs flatter than ~45°.
   - Colours stacked by height (stripes on an upright cone) can be printed on a single nozzle
     with colour-change pauses — give the user the change heights. Colours side by side in the
     same layers (logo inlays) need an AMS/multi-material printer — say so.
3. **Model in SDFs** on a `Grid` with ≥ 1 mm margin around the part (0.08–0.15 mm resolution;
   finer for small parts). Recipes:
   - *Flat plate + inlays:* 2D shapes → `offset_outline(shape, grid, border, fillet)` for the
     backing outline → `extrude(..., top_round=...)`. Inlays are `extrude(shape, Z, T-inlay, T,
     top_round=0.7)`; cut the plate with the **unrounded** inlay prism so parts never overlap.
   - *Upright 3D:* `round_cone`, `round_box`, `round_intersect` for rounded edges; bands/stripes
     as `round_intersect(body - bulge, |z - zc| - half_h, r)`; flat back via `smax(body, -y, k)`.
   - *Puffy sculpted relief:* one 2D mask per layer (trunk, each tier, star…) →
     `pillow_height` per layer (depth may vary with height so lower edges overhang) → combine with
     `np.maximum` (hard max keeps crisp creases) → `relief_body`.
   - *Colour split:* define each colour region as a field, then `colour_i = max(body, region_i)`
     and the base colour `= max(body, -union(regions))` — surfaces coincide exactly.
4. **Mesh and check:** `m = mesh_from_field(field, grid)`, `report(name, m)` → must print
   `watertight=True`, positive volume, and the intended size. Export STL per part too if useful.
5. **Write and verify:** `write_3mf(path, [(name, mesh, 'RRGGBB'), ...], title=...)` then
   `verify_3mf(path)`.
6. **Look at it:** `python3 scripts/render_preview.py preview.png model.3mf` (`--upright` for
   standing models) and Read the PNG. Compare against the reference view by view; iterate on
   proportions before delivering. Send the 3MF + preview with SendUserFile.
7. **Hand over:** size, parts/colours, orientation, supports, colour-change heights or AMS need,
   hole sizes as built, an honest weight (volume × 1.24 g/cm³ × infill factor; reference
   sheets often overstate it), and that it has not been sliced/printed yet.

## Standard features

| Feature | Default | Note |
|---|---|---|
| Magnet pocket for an N×D mm disc magnet | Ø N + 0.3, depth D + 0.1 | Use the user's exact number if they give one; opening on the bed side prints cleanly |
| Hanging loop | 4 mm hole, 1.7–2 mm wall, ~3.5 mm thick | Build the loop in true mm (don't rescale it with the body) so the hole stays exact |
| Minimum wall / feature | ≥ 1.2 mm (3 lines of 0.4 mm nozzle) | Keep ≥ 1 mm material behind a magnet pocket |
| Inlay thickness | 1.2–1.6 mm | Enough layers for opaque colour |
| Edge rounding | 0.7–1.0 mm on top edges | Leave the bed edge sharp (or tiny) for adhesion |

## Pitfalls already solved — don't relearn them

- **Non-watertight meshes** come from samples landing exactly on a cutting plane (z = 0) or the
  model touching the grid edge: `Grid` adds sub-voxel offsets; keep ≥ 1 mm margin.
- **`smin` grows outlines** (a 35 × 50 mm border came out 50.75 mm). Use `offset_outline`
  (exact distance-transform closing) for borders/backings that must hit a dimension.
- **Distance-transform input must have margin**: if the dilated mask touches the array edge, the
  EDT is wrong (negative volume). `offset_outline` pads automatically.
- **Pillow reliefs show ridge lines** along the medial axis of the distance field → blur the
  distance (`smooth≈1 mm` in `pillow_height`). Round columns (trunks) look pyramidal → use an
  x-only profile (`dist = half_width - |x|`).
- **Separate blobs instead of one shape**: when unioning circles/lobes into a silhouette, make the
  core polygon wide enough to reach the lobes, then blend with `smin` k≈1–3 mm.
- **Spiky vs chunky stars**: `star5(..., r - k, rf) - k` with k≈1.2 and rf≈0.55 gives rounded
  but distinct points.
- **3MF reload shows a few broken faces** even though the mesh was watertight: the centring shift
  must stay on the 1e-4 mm vertex lattice (fixed in `write_3mf`); always run `verify_3mf`.
- **Sharp cusps in a height-field crease** (e.g. notches between rim scallops meeting another layer)
  give a handful of non-manifold faces → round the notch (`sqrt(cos² + 0.03)`) and use
  `antialias≈0.16` in `relief_body`.
- **Scallop direction**: lobes must hang *down* (rim lowest at lobe centres, small notches between);
  the inverted profile reads as straight creases.
- **A small raised feature disappears into the layer below** (star arms swallowed by the top tier):
  pillow heights fall to `edge_wall` at narrow arms. Give that feature a high `edge_wall`
  (≈3 mm) and small radius, and make the layer under it thinner there.
- **Upper layers should overhang lower ones**: make each tier thickest at its rim and thinner
  (~60 %) at its top, with a small pillow radius (≈3 mm) so the rim curls over the tier below.
- **Labels on AI-made design sheets are often wrong** (20 mm wide / 30 mm deep on a visibly
  23 × 11 mm drawing; 18–22 g for a 4 g part). Trust the stated height + the drawing; say so.
- **Big files**: 0.08–0.1 mm grids give 0.5–1 M triangles (5–30 MB 3MF). Fine for slicers; use
  0.12–0.15 mm for large parts.
- **Trademarks**: logo replicas are fine for the user's personal use; mention they shouldn't be
  sold.
