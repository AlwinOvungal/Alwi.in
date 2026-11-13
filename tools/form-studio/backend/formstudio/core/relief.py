"""Magnets, keychains and ornaments built straight from the approved picture.

A flat object's shape is already in its picture: the outline, where each colour sits, its creases.
Building it from an AI 3D mesh threw that away — a mesh is slow to make (minutes), its depth is a
guess, and its artifacts (flat diamond facets, background slabs) turned into geometry and colour
boundaries. Here the picture *is* the model:

  outline   the cut-out's silhouette, exact (vertical walls, holes kept)
  height    a back plate + a rounded dome + a soft pillow per colour region (a small groove where
            two colours meet, so each region reads and prints as its own part) + the picture's line
            detail (creases, tiers) from its lightness, kept small so shading never becomes bumps
  colours   the picture's paints (logo.regions), one part per colour, straight shared walls

Everything happens on a height grid, so it takes seconds and the result matches the picture.
"""
from __future__ import annotations

import math
from typing import Any

import manifold3d as mf
import numpy as np
from PIL import Image
from scipy import ndimage
from skimage import measure

GRID_MM = 0.2  # height-grid spacing: finer than a 0.4 mm nozzle line
OUTLINE_MM = 0.08  # outline tracing resolution


def plate_mm(thickness: float) -> float:
    return max(2.0, min(0.3 * thickness, 4.0))


def fit(mask: np.ndarray, height_mm: float) -> tuple[tuple[int, int, int, int], float]:
    """The object's box in the picture and the print scale (mm per picture pixel): the box's
    height becomes `height_mm` (the Object height slider)."""
    ys, xs = np.nonzero(mask)
    if not len(ys):
        raise ValueError("the picture has no object in it")
    box = (int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1)
    return box, height_mm / max(box[1] - box[0], 1)


def _resample(a: np.ndarray, box: tuple[int, int, int, int], shape: tuple[int, int], resample) -> np.ndarray:
    y0, y1, x0, x1 = box
    crop = a[y0:y1, x0:x1]
    if crop.dtype == bool:
        crop = crop.astype(np.uint8) * 255
    return np.asarray(Image.fromarray(crop).resize((shape[1], shape[0]), resample))


def outline(mask: np.ndarray, box: tuple[int, int, int, int], mm_px: float, min_feature_mm: float
            ) -> tuple[np.ndarray, float]:
    """The silhouette at OUTLINE_MM, softened a touch (pixel steps) and with parts thinner than the
    printer can make removed. Returns (mask on the outline grid, image row 0 at the top; area removed)."""
    w_mm, h_mm = (box[3] - box[2]) * mm_px, (box[1] - box[0]) * mm_px
    shape = (max(8, round(h_mm / OUTLINE_MM)), max(8, round(w_mm / OUTLINE_MM)))
    soft = _resample(mask, box, shape, Image.BILINEAR).astype(np.float32) / 255
    soft = ndimage.gaussian_filter(soft, 0.12 / OUTLINE_MM)
    m = soft > 0.5
    before = m.sum()
    r = int(round(min_feature_mm / 2 / OUTLINE_MM))
    if r >= 1:
        disk = np.hypot(*np.mgrid[-r:r + 1, -r:r + 1]) <= r
        opened = ndimage.binary_opening(np.pad(m, r + 1), structure=disk)[r + 1:-(r + 1), r + 1:-(r + 1)]
        if opened.sum() >= 0.6 * before:  # don't let a tiny "minimum" eat the object
            m = opened
    lab, n = ndimage.label(m)
    if n > 1:  # one printable piece: the biggest, and any piece at least a fifth of it
        sizes = ndimage.sum(m, lab, range(1, n + 1))
        m = np.isin(lab, 1 + np.nonzero(sizes >= 0.2 * sizes.max())[0])
    return m, float(1 - m.sum() / max(before, 1))


def prism(m: np.ndarray, top_mm: float) -> mf.Manifold:
    """The outline grid (row 0 at the top) as a straight-walled solid from z=-0.5 to top_mm; the
    object's box is centred on the origin, x right, y up."""
    pad = np.pad(m, 1).astype(np.float32)
    h, w = m.shape
    polys = []
    for c in measure.find_contours(pad, 0.5):
        c = measure.approximate_polygon(c, 0.4)
        if len(c) < 4:
            continue
        ys = (h / 2 - (c[:, 0] - 1) - 0.5) * OUTLINE_MM
        xs = ((c[:, 1] - 1) + 0.5 - w / 2) * OUTLINE_MM
        polys.append(np.stack([xs, ys], 1)[:-1])
    if not polys:
        raise ValueError("the outline is empty")
    cs = mf.CrossSection(polys, mf.FillRule.EvenOdd)
    return cs.extrude(top_mm + 0.5).translate((0, 0, -0.5))


def heightmap(rgb: np.ndarray, mask: np.ndarray, labels: np.ndarray | None, box: tuple[int, int, int, int],
              mm_px: float, thickness: float, finish: dict[str, Any] | None = None) -> np.ndarray:
    """Height (mm) on a GRID_MM grid over the object's box (row 0 at the top). Outside the outline it
    continues the nearest inside height, so the outline prism alone decides the walls."""
    finish = finish or {}
    w_mm, h_mm = (box[3] - box[2]) * mm_px, (box[1] - box[0]) * mm_px
    shape = (max(4, round(h_mm / GRID_MM)), max(4, round(w_mm / GRID_MM)))
    m = _resample(mask, box, shape, Image.BILINEAR) > 127
    if not m.any():
        m[:] = True
    plate = plate_mm(thickness)
    avail = max(thickness - plate, 0.4)

    # the dome: a rounded edge (bevel) rising into a gentle swell, like a pressed clay tile
    d = ndimage.distance_transform_edt(np.pad(m, 1))[1:-1, 1:-1] * GRID_MM
    dmax = max(float(d.max()), 1e-6)
    bevel = max(min(avail * 1.4, 0.3 * dmax), GRID_MM)
    dome = 0.62 * np.sqrt(np.clip(d / bevel, 0, 1)) + 0.38 * np.clip(d / dmax, 0, 1) ** 0.8
    # a distance field has creases along the shape's midline; smooth them (inside only, so the
    # bevel still meets the outline)
    dome = ndimage.gaussian_filter(dome, 0.8 / GRID_MM) * np.clip(d / bevel, 0, 1) + dome * (1 - np.clip(d / bevel, 0, 1))
    dome = ndimage.gaussian_filter(dome, 0.35 / GRID_MM)  # pixel steps in the distance field

    # a soft pillow per colour region: each colour swells on its own, so a small groove separates
    # neighbours (the eye, the beak, the belly read as parts); small regions sit a little proud
    pillow = np.zeros(shape, np.float32)
    if labels is not None and labels.max() >= 1:
        lg = _resample((labels + 1).astype(np.uint8), box, shape, Image.NEAREST).astype(np.int32) - 1
        lg[~m] = -1
        groove = min(0.5, 0.18 * avail)
        total = m.sum()
        for i in range(int(lg.max()) + 1):
            reg = lg == i
            if not reg.any():
                continue
            # the outer edge is the dome's job: only boundaries with another colour get the groove
            other = m & ~reg
            near = ndimage.distance_transform_edt(np.pad(~other, 1, constant_values=True))[1:-1, 1:-1] * GRID_MM
            pillow[reg] = groove * (np.sqrt(np.clip(near[reg] / 1.0, 0, 1)) - 1)
            if reg.sum() < 0.12 * total:  # eyes, beaks, buttons: raised a little
                pillow[reg] += min(0.4, 0.12 * avail)

    # line detail from lightness: high-pass, so creases and tiers show but broad shading doesn't. It
    # goes on after the finish's softening, which is for the shape, not the picture's lines.
    lum = _resample(rgb.mean(axis=2).astype(np.uint8), box, shape, Image.BILINEAR).astype(np.float32)
    hp = lum - ndimage.gaussian_filter(lum, 1.5 / GRID_MM)
    amp = min(1.0, 0.2 * avail) * float(finish.get("detail", 1.0))
    detail = np.clip(hp / 30.0, -1, 1) * amp
    detail = ndimage.gaussian_filter(detail, 0.3 / GRID_MM)  # the photo's grain is not detail
    detail *= np.clip(d / 0.8, 0, 1)  # not on the bevel: the outline stays clean

    shape_rise = np.clip(dome * avail * 0.8 + pillow, 0, None)
    soften = float(finish.get("soften_mm", 0) or 0)
    if soften > 0:
        shape_rise = ndimage.gaussian_filter(shape_rise, soften / GRID_MM * 0.5)
    rise = np.clip(shape_rise + detail, 0, None)
    rise *= avail / max(float(rise[m].max()), 1e-6)  # the highest point reaches the full thickness
    hgt = plate + rise

    # finishes act on the surface, never the outline
    tex = float(finish.get("clay_texture_mm", 0) or 0)
    if tex > 0:  # hand-made clay: a fine, irregular grain
        grain = float(finish.get("clay_grain_mm", 3.0) or 3.0)
        noise = np.random.default_rng(7).standard_normal(shape).astype(np.float32)
        noise = ndimage.gaussian_filter(noise, grain / GRID_MM / 4)
        noise /= max(float(np.abs(noise).max()), 1e-6)
        hgt = hgt + noise * tex * np.clip(d / 1.0, 0, 1)
    step = float(finish.get("terrace_mm", 0) or 0)
    if step > 0:  # voxel / blocky looks: flat terraces
        hgt = np.maximum(np.round(hgt / step) * step, plate)
    hgt = np.clip(hgt, plate, thickness)
    # outside the outline: carry the nearest inside height so walls come only from the prism
    if (~m).any():
        _, (iy, ix) = ndimage.distance_transform_edt(~m, return_indices=True)
        hgt = hgt[iy, ix]
    return hgt.astype(np.float32)


def heightfield(hgt: np.ndarray, w_mm: float, h_mm: float) -> mf.Manifold:
    """A closed solid over the object's box: flat bottom at z=0, top surface at the grid heights
    (vertices at cell centres, stretched to the box's edges), centred on the origin."""
    ny, nx = hgt.shape
    xs = np.linspace(-w_mm / 2, w_mm / 2, nx)
    ys = np.linspace(h_mm / 2, -h_mm / 2, ny)  # row 0 is the top of the picture
    X, Y = np.meshgrid(xs, ys)
    top = np.stack([X, Y, hgt], -1).reshape(-1, 3)
    bot = np.stack([X, Y, np.zeros_like(hgt)], -1).reshape(-1, 3)
    verts = np.concatenate([top, bot]).astype(np.float32)
    n = nx * ny
    idx = np.arange(n).reshape(ny, nx)
    a, b, c, d = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, :-1].ravel(), idx[1:, 1:].ravel()
    # rows go down in y, so (a, c, b) winds counter-clockwise seen from above
    tris = [np.stack([a, c, b], 1), np.stack([b, c, d], 1),  # top
            np.stack([a, b, c], 1) + n, np.stack([b, d, c], 1) + n]  # bottom, facing down
    ring = np.concatenate([idx[0, :], idx[1:, -1], idx[-1, -2::-1], idx[-2:0:-1, 0]])  # clockwise from above
    nxt = np.roll(ring, -1)
    tris += [np.stack([ring, nxt, ring + n], 1), np.stack([nxt, nxt + n, ring + n], 1)]
    tri = np.concatenate(tris).astype(np.uint32)
    solid = mf.Manifold(mf.Mesh(vert_properties=verts, tri_verts=tri))
    if solid.volume() <= 0:  # wound the other way: flip every triangle
        solid = mf.Manifold(mf.Mesh(vert_properties=verts, tri_verts=tri[:, ::-1].copy()))
    return solid


def build(rgb: np.ndarray, mask: np.ndarray, labels: np.ndarray | None, height_mm: float,
          thickness: float, min_feature_mm: float, finish: dict[str, Any] | None = None
          ) -> tuple[mf.Manifold, dict[str, Any]]:
    """The relief body lying on its back (z up from the bed, picture top towards +y), centred."""
    box, mm_px = fit(mask, height_mm)
    w_mm, h_mm = (box[3] - box[2]) * mm_px, (box[1] - box[0]) * mm_px
    m_out, removed = outline(mask, box, mm_px, min_feature_mm)
    hgt = heightmap(rgb, mask, labels, box, mm_px, thickness, finish)
    low = float((finish or {}).get("lowpoly_mm", 0) or 0)
    if low > GRID_MM:  # faceted look: a coarse grid of flat triangles; the outline stays exact
        ny, nx = hgt.shape
        cny, cnx = max(3, round(ny * GRID_MM / low)), max(3, round(nx * GRID_MM / low))
        hgt = np.asarray(Image.fromarray(hgt).resize((cnx, cny), Image.BOX))
    solid = heightfield(hgt, w_mm, h_mm) ^ prism(m_out, thickness + 1)
    if solid.is_empty():
        raise ValueError("the relief came out empty")
    parts = solid.decompose()
    if len(parts) > 1:
        solid = max(parts, key=lambda p: p.volume())
    info = {"relief": True, "relief_source": "picture", "plate_mm": round(plate_mm(thickness), 1),
            "relief_mm": round(thickness - plate_mm(thickness), 1), "outline": "picture",
            "outline_removed_pct": round(removed * 100, 1), "mm_per_px": mm_px,
            "_outline": m_out, "_box_mm": (w_mm, h_mm)}
    return solid, info


def fidelity(body: mf.Manifold, m_out: np.ndarray, w_mm: float, h_mm: float, mask: np.ndarray,
             box: tuple[int, int, int, int]) -> dict[str, float]:
    """How closely the print's outline follows the picture's: IoU of the body's footprint (sliced
    just above the bed) with the picture's silhouette, both on the outline grid."""
    from PIL import ImageDraw

    h, w = m_out.shape
    img = Image.new("L", (w, h), 0)
    dr = ImageDraw.Draw(img)
    polys = body.slice(0.3).to_polygons()
    # even-odd fill: XOR each ring
    acc = np.zeros((h, w), bool)
    for p in polys:
        p = np.asarray(p)
        if len(p) < 3:
            continue
        one = Image.new("L", (w, h), 0)
        px = [((x + w_mm / 2) / OUTLINE_MM, (h_mm / 2 - y) / OUTLINE_MM) for x, y in p]
        ImageDraw.Draw(one).polygon(px, fill=255)
        acc ^= np.asarray(one) > 0
    del dr, img
    pic = _resample(mask, box, (h, w), Image.BILINEAR) > 127
    iou = (acc & pic).sum() / max((acc | pic).sum(), 1)
    return {"outline_iou": round(float(iou), 4)}


def _grid_xy(m: np.ndarray, row: float, col: float) -> tuple[float, float]:
    h, w = m.shape
    return (col + 0.5 - w / 2) * OUTLINE_MM, (h / 2 - row - 0.5) * OUTLINE_MM


def pocket_spot(m_out: np.ndarray, radius_mm: float, wall_mm: float = 1.5) -> tuple[float, float, float]:
    """Where a magnet pocket fits best: the point deepest inside the outline (the widest part of the
    shape), so the most material surrounds it. Returns (x, y, room): room is the distance to the edge."""
    d = ndimage.distance_transform_edt(np.pad(m_out, 1))[1:-1, 1:-1]
    # prefer the middle among equally deep points: a pocket off to one side looks and grips worse
    h, w = m_out.shape
    yy, xx = np.mgrid[:h, :w]
    centre = np.hypot(yy - h / 2, xx - w / 2)
    score = d - 1e-3 * centre
    r, c = np.unravel_index(int(np.argmax(score)), score.shape)
    x, y = _grid_xy(m_out, r, c)
    return x, y, float(d[r, c] * OUTLINE_MM)


def picture_loop(m_out: np.ndarray, min_hole_mm: float = 2.5, wall_mm: float = 1.2) -> dict | None:
    """A hanging/attachment hole the picture already has near its top (keychains, ornaments). Then the
    print uses it instead of adding a second loop. None if there isn't a usable one."""
    holes = ndimage.binary_fill_holes(m_out) & ~m_out
    lab, n = ndimage.label(holes)
    if not n:
        return None
    ys, _ = np.nonzero(m_out)
    top, height = ys.min(), ys.max() - ys.min() + 1
    best = None
    for i in range(1, n + 1):
        hole = lab == i
        area = hole.sum() * OUTLINE_MM ** 2
        diam = 2 * math.sqrt(area / math.pi)
        hy, hx = np.nonzero(hole)
        if diam < min_hole_mm or hy.mean() > top + 0.3 * height:
            continue
        # the ring of material around the hole must be thick enough to hold a key ring or ribbon
        ring = ndimage.binary_dilation(hole, iterations=max(1, int(wall_mm / OUTLINE_MM))) & ~hole
        if (ring & m_out).sum() < 0.95 * ring.sum():
            continue
        if best is None or diam > best["diameter_mm"]:
            x, y = _grid_xy(m_out, hy.mean(), hx.mean())
            best = {"x": round(x, 2), "y": round(y, 2), "diameter_mm": round(diam, 1)}
    return best
