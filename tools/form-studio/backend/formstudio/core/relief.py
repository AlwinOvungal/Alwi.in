"""Magnets, keychains and ornaments built straight from the approved picture.

A flat object's shape is already in its picture: the outline, where each colour sits, its creases.
Building it from an AI 3D mesh threw that away — a mesh is slow to make (minutes), its depth is a
guess, and its artifacts (flat diamond facets, background slabs) turned into geometry and colour
boundaries. Here the picture *is* the model:

  outline   the cut-out's silhouette, exact and smooth (sub-pixel, corners rounded; holes kept)
  height    a back plate + a gentle swell + the picture's raised and recessed features (from a depth
            model when it's downloaded, else read from the picture's creases and shading), at least
            0.5 mm proud or deep, smoothed without losing the steps between features
  edges     soft / bevelled / crisp outline edges, and feature edges rounded separately
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
DEPTH_REPO = "depth-anything/Depth-Anything-V2-Small-hf"
DEPTH_FILES = ("config.json", "preprocessor_config.json", "model.safetensors")
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


def _chaikin(c: np.ndarray, passes: int = 2) -> np.ndarray:
    """Corner-cutting: a closed polygon with its corners rounded off (no pixel staircase)."""
    for _ in range(passes):
        nxt = np.roll(c, -1, axis=0)
        c = np.stack([0.75 * c + 0.25 * nxt, 0.25 * c + 0.75 * nxt], 1).reshape(-1, 2)
    return c


def prism(m: np.ndarray, top_mm: float) -> mf.Manifold:
    """The outline grid (row 0 at the top) as a straight-walled solid from z=-0.5 to top_mm; the
    object's box is centred on the origin, x right, y up. The contour is traced sub-pixel on a softened
    field and its corners rounded, so the walls are smooth: no staircase, no vertical striations."""
    field = ndimage.gaussian_filter(np.pad(m, 2).astype(np.float32), 1.0)
    h, w = m.shape
    polys = []
    for c in measure.find_contours(field, 0.5):
        if len(c) < 8:
            continue
        c = measure.approximate_polygon(c, 0.03 / OUTLINE_MM)
        if len(c) < 4:
            continue
        c = _chaikin(c[:-1], 2)
        ys = (h / 2 - (c[:, 0] - 2) - 0.5) * OUTLINE_MM
        xs = ((c[:, 1] - 2) + 0.5 - w / 2) * OUTLINE_MM
        polys.append(np.stack([xs, ys], 1))
    if not polys:
        raise ValueError("the outline is empty")
    cs = mf.CrossSection(polys, mf.FillRule.EvenOdd)
    return cs.extrude(top_mm + 0.5).translate((0, 0, -0.5))


EDGE_STYLES = ("auto", "soft", "bevel", "crisp")
RENORM_CAP = 1.6  # kept features regain depth after simplifying, but faint shading never becomes a shape
MIN_FEATURE_DEPTH = 0.5  # mm: a raised or recessed feature at least this proud of its surroundings (2-3 layers)


def _light_dir(lum: np.ndarray, m: np.ndarray) -> np.ndarray:
    """Where the picture's light comes from (unit vector in (row, col)), from the broad shading: a
    plane fitted to the smoothed lightness inside the object rises towards the light."""
    ys, xs = np.nonzero(m)
    if len(ys) < 10:
        return np.array([-0.7, -0.7])
    v = lum[ys, xs]
    A = np.stack([ys - ys.mean(), xs - xs.mean(), np.ones(len(ys))], 1)
    (gy, gx, _), *_ = np.linalg.lstsq(A, v, rcond=None)
    g = np.array([gy, gx], float)
    n = np.linalg.norm(g)
    return g / n if n > 1e-6 else np.array([-0.7, -0.7])  # studio default: top-left


def feature_regions(lum: np.ndarray, m: np.ndarray, labels: np.ndarray | None = None,
                    min_feature_mm: float = 1.2, max_features: int = 40) -> tuple[np.ndarray, list[int]]:
    """The picture's features (face panel, eyes, beak, wings…) when no depth model is available.

    Features are outlined by creases: the dark, thin shadow lines a raised or recessed shape makes
    against its surroundings (and colour boundaries). The creases split the object into regions; each
    region is raised (+1) or recessed (-1), read from its shading: a raised shape's rim facing the light
    is bright and the far rim dark, a recess is the other way round. The body (largest region) is 0.
    Returns (region grid, -1 outside; sign per region)."""
    from skimage import morphology, segmentation

    r = max(2, int(round(0.7 / GRID_MM)))
    smooth = ndimage.gaussian_filter(lum, 0.6)
    # a feature's outline is a thin shadow on one side and a thin highlight on the other: take both, so
    # the outline closes all the way round
    fp = morphology.disk(r)
    crease = morphology.black_tophat(smooth, fp) + 0.8 * morphology.white_tophat(smooth, fp)
    inner = m & (ndimage.distance_transform_edt(m) * GRID_MM > 0.8)
    vals = crease[inner]
    if vals.size == 0:
        return np.where(m, 0, -1), [0]
    thr = max(float(np.percentile(vals, 90)), float(vals.mean() + 1.5 * vals.std()), 5.0)
    lines = (crease > thr) & inner
    colour_edge = np.zeros_like(m)
    if labels is not None:  # where two colours meet is always a boundary between features
        colour_edge[:, 1:] |= labels[:, 1:] != labels[:, :-1]
        colour_edge[1:, :] |= labels[1:, :] != labels[:-1, :]
        colour_edge &= inner
        lines |= colour_edge
    # watershed on the crease strength: regions grow from crease-free areas and meet on the creases, so
    # gaps in a line don't merge two features; boundaries that are mostly not a crease are then merged
    from skimage.feature import peak_local_max

    elev = ndimage.gaussian_filter(np.where(colour_edge, 2.0 * thr, np.where(lines, crease, crease * 0.5)), 1.0)
    # one seed per feature: the points farthest from any crease (or the outline). Where a crease line has
    # a gap, the distance field has a saddle there, so the two sides still get their own seeds.
    dl = ndimage.gaussian_filter(ndimage.distance_transform_edt(m & ~lines), 1.0)
    peaks = peak_local_max(dl, min_distance=max(2, int(0.6 / GRID_MM)), threshold_abs=0.35 / GRID_MM,
                           labels=m.astype(int), exclude_border=False)
    # seeds: every area fully closed off by creases (an eye, a beak), plus, inside big areas, the distance
    # peaks (so a feature whose outline has a gap still gets its own seed)
    comps, nc = ndimage.label(m & ~ndimage.binary_dilation(lines, iterations=1))
    markers = np.zeros(m.shape, np.int32)
    n = 0
    big_area = (6.0 / GRID_MM) ** 2
    small_seed = (0.6 / GRID_MM) ** 2
    csizes = ndimage.sum(np.ones(m.shape), comps, range(1, nc + 1)) if nc else []
    for c, sz in enumerate(csizes, start=1):
        if small_seed <= sz < big_area:
            n += 1
            markers[comps == c] = n
    for py, px in peaks:
        c = comps[py, px]
        if c == 0 or csizes[c - 1] >= big_area:
            if markers[py, px] == 0:
                n += 1
                markers[py, px] = n
    if n <= 1:
        return np.where(m, 0, -1), [0]
    reg = segmentation.watershed(elev, markers, mask=m)  # regions meet on the creases
    # merge neighbours whose shared boundary is weak (shading noise, not a crease)
    parent = list(range(n + 1))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    pairs: dict[tuple[int, int], list[float]] = {}
    for dy, dx in ((0, 1), (1, 0)):
        a_ = reg[: reg.shape[0] - dy, : reg.shape[1] - dx]
        b_ = reg[dy:, dx:]
        e_ = np.maximum(elev[: reg.shape[0] - dy, : reg.shape[1] - dx], elev[dy:, dx:])
        sel = (a_ != b_) & (a_ > 0) & (b_ > 0)
        for x, y, e in zip(a_[sel], b_[sel], e_[sel]):
            pairs.setdefault((min(x, y), max(x, y)), []).append(float(e))
    strength = {k: float(np.percentile(v, 75)) for k, v in pairs.items()}
    strong = {k for k, v in strength.items() if v >= 0.5 * thr and len(pairs[k]) >= 3}
    members = {i: {i} for i in range(1, n + 1)}
    # weakest first; two groups never merge if a real crease runs anywhere between them (no bridging
    # through a sliver that touches both an eye and the face)
    for (x, y) in sorted(pairs, key=lambda k: strength[k]):
        if (x, y) in strong:
            continue
        gx, gy = find(x), find(y)
        if gx == gy:
            continue
        if any((min(a, b), max(a, b)) in strong for a in members[gx] for b in members[gy]):
            continue
        parent[gy] = gx
        members[gx] |= members.pop(gy)
    reg = np.where(reg > 0, np.vectorize(find)(reg), 0)
    min_area = (min_feature_mm / GRID_MM) ** 2  # smaller than this across: simplified into its surroundings
    ids = [int(i) for i in np.unique(reg) if i > 0]
    small = [i for i in ids if (reg == i).sum() < min_area]
    if small:
        seeds = np.where(np.isin(reg, small), 0, reg)
        reg = segmentation.expand_labels(seeds, distance=max(m.shape)) * m
        ids = [i for i in ids if i not in small]
    if len(ids) <= 1:
        return np.where(m, 0, -1), [0]
    body = max(ids, key=lambda i: (reg == i).sum())
    light = _light_dir(ndimage.gaussian_filter(lum, 2.0 / GRID_MM), m)
    out = np.full(m.shape, -1, np.int32)
    signs: list[int] = []
    for i in ids:
        R = reg == i
        out[R] = len(signs)
        if i == body:
            signs.append(0)
            continue
        dist = ndimage.distance_transform_edt(R)
        rim = R & (dist * GRID_MM <= 1.0)
        gy, gx = np.gradient(-ndimage.gaussian_filter(dist, 1.0))  # outward normal
        nrm = np.hypot(gy, gx) + 1e-6
        facing = (gy * light[0] + gx * light[1]) / nrm
        lit, shade = rim & (facing > 0.3), rim & (facing < -0.3)
        around = ndimage.binary_dilation(R, iterations=3) & ~R & m
        darker = bool(around.any()) and lum[R].mean() < lum[around].mean() - 10
        # no shading cue (flat artwork): shapes stand proud, except small dark ones (eyes, holes) sink in
        dark_hole = darker and lum[R].mean() < 90
        if lit.sum() < 3 or shade.sum() < 3:
            signs.append(-1 if dark_hole else 1)
            continue
        delta = float(lum[lit].mean() - lum[shade].mean())
        signs.append((-1 if dark_hole else 1) if abs(delta) < 3 else (1 if delta > 0 else -1))
    # balanced detail: keep the most telling features (big and contrasty), fold the rest into the body
    feats = [k for k, sg in enumerate(signs) if sg]
    if len(feats) > max_features:
        score = {k: float((out == k).sum()) * (1 + abs(float(lum[out == k].mean()) - float(lum[m].mean())) / 50)
                 for k in feats}
        for k in sorted(feats, key=lambda k: score[k])[: len(feats) - max_features]:
            signs[k] = 0
    return out, signs


def _simplify_features(feat: np.ndarray, m: np.ndarray, amp: float, min_mm: float, max_n: int
                       ) -> tuple[np.ndarray, dict[str, int]]:
    """Fold small bumps and dents (smaller than min_mm across) into their surroundings and keep at most
    max_n distinct features (the biggest, strongest), so the print stays clean, quick and sturdy.

    A grey-scale opening (then closing) with a disk min_mm wide removes every bump (dent) narrower than
    the disk while leaving bigger shapes and their edges as they were: bolts and lettering go, the cab
    and the wheels stay."""
    r = max(1, int(round(min_mm / GRID_MM / 2)))
    disk = np.hypot(*np.mgrid[-r:r + 1, -r:r + 1]) <= r
    from skimage.morphology import reconstruction

    before = feat
    # opening / closing *by reconstruction*: bumps and dents smaller than the disk vanish, everything
    # bigger keeps its exact shape (a plain opening would leave disc-shaped marks)
    f = feat.astype(np.float64)
    opened = reconstruction(np.minimum(ndimage.grey_opening(f, footprint=disk), f), f, method="dilation")
    feat = reconstruction(np.maximum(ndimage.grey_closing(opened, footprint=disk), opened), opened, method="erosion")
    removed = (np.abs(before - feat) > 0.25 * amp) & m
    n_small = int(ndimage.label(removed)[1])
    strong = (np.abs(feat) > 0.35 * amp) & m
    lab, n = ndimage.label(strong)
    if not n:
        return feat.astype(np.float32), {"kept": 0, "small": n_small}
    idx = np.arange(1, n + 1)
    area = ndimage.sum(np.ones(m.shape), lab, idx)
    power = ndimage.sum(np.abs(feat), lab, idx)
    small = area < (min_mm / GRID_MM) ** 2
    order = np.argsort(-power)
    keep = np.zeros(n + 1, bool)
    kept = 0
    for i in order:
        if not small[i] and kept < max_n:
            keep[i + 1] = True
            kept += 1
    drop = (lab > 0) & ~keep[lab]
    if drop.any():  # fade them out smoothly instead of leaving a hole
        w = ndimage.gaussian_filter(ndimage.binary_dilation(drop, iterations=2).astype(np.float32), 1.0)
        feat = feat * (1 - np.clip(w * 1.5, 0, 1))
    return feat.astype(np.float32), {"kept": int(kept), "small": int(n - kept) + n_small}


def _plateau(R: np.ndarray, rim_mm: float) -> np.ndarray:
    """1 inside a region, rounding down to 0 at its boundary over rim_mm (a soft-topped step)."""
    d = ndimage.distance_transform_edt(np.pad(R, 1))[1:-1, 1:-1] * GRID_MM
    t = np.clip(d / max(rim_mm, GRID_MM), 0, 1)
    return (np.sqrt(1 - (1 - t) ** 2) * R).astype(np.float32)


def edge_profile(d: np.ndarray, style: str, width_mm: float) -> np.ndarray:
    """0..1 by distance from the outline: soft = quarter-round, bevel = straight chamfer, crisp = 1."""
    if style == "crisp" or width_mm <= GRID_MM:
        return np.ones_like(d)
    t = np.clip(d / width_mm, 0, 1)
    if style == "bevel":
        return t
    return np.sqrt(1 - (1 - t) ** 2)


def suggest_edges(rgb: np.ndarray, mask: np.ndarray, mm_px: float) -> dict[str, Any]:
    """The edge treatment that matches the picture. A rounded edge turns away from the light on one side
    and towards it on the other, so just inside the outline the shading varies much more than on the
    flat-ish body, over a band as wide as the rounding. A flat cut-out stays even right to the edge."""
    lum = rgb.mean(axis=2).astype(np.float32)
    d = ndimage.distance_transform_edt(mask) * mm_px
    deep = mask & (d >= 3.0) & (d <= 6.0)
    edge_band = mask & (d >= 0.05) & (d < 0.3)
    if deep.sum() < 50 or edge_band.sum() < 20:
        return {"style": "soft", "outer_mm": 1.5}
    deep_sd, deep_med = float(lum[deep].std()), float(np.median(lum[deep]))
    edge_sd, edge_med = float(lum[edge_band].std()), float(np.median(lum[edge_band]))
    contrast = max(edge_sd - deep_sd, abs(edge_med - deep_med))
    if contrast < 6:  # even right up to the edge: a flat cut-out
        return {"style": "crisp", "outer_mm": 0.0}
    width = 0.25
    for w in np.arange(0.25, 3.0, 0.25):
        band = mask & (d >= w) & (d < w + 0.25)
        if not band.any():
            break
        c = max(float(lum[band].std()) - deep_sd, abs(float(np.median(lum[band])) - deep_med))
        if c > 0.25 * contrast:
            width = float(w + 0.25)
        else:
            break
    if width >= 0.75:
        return {"style": "soft", "outer_mm": round(float(np.clip(width * 1.4, 1.0, 3.0)), 1)}
    if width >= 0.5:
        return {"style": "bevel", "outer_mm": round(width, 1)}
    return {"style": "crisp", "outer_mm": 0.0}


def heightmap(rgb: np.ndarray, mask: np.ndarray, labels: np.ndarray | None, box: tuple[int, int, int, int],
              mm_px: float, thickness: float, finish: dict[str, Any] | None = None,
              depth: np.ndarray | None = None, edges: dict[str, Any] | None = None,
              stats: dict[str, Any] | None = None, outline_grid: np.ndarray | None = None,
              detail: dict[str, Any] | None = None) -> np.ndarray:
    """Height (mm) on a GRID_MM grid over the object's box (row 0 at the top). Outside the outline it
    continues the nearest inside height, so the outline prism alone decides the walls.

    body      a gentle swell (the depth model's broad shape when there is one, else from the outline)
    features  the picture's raised and recessed shapes (depth-model detail, else shading-read regions),
              at least MIN_FEATURE_DEPTH proud or deep so they show in print
    edges     the outline's profile (soft / bevel / crisp, `outer_mm`) and, separately, the rounding of
              feature edges (`detail_mm`), so a soft body never smears the eyes
    Smoothing removes noise and ripples but keeps the steps between features."""
    from skimage.restoration import denoise_tv_chambolle

    finish = finish or {}
    edges = edges or {"style": "soft", "outer_mm": 1.5, "detail_mm": 0.4}
    stats = stats if stats is not None else {}
    w_mm, h_mm = (box[3] - box[2]) * mm_px, (box[1] - box[0]) * mm_px
    shape = (max(4, round(h_mm / GRID_MM)), max(4, round(w_mm / GRID_MM)))
    m = _resample(mask, box, shape, Image.BILINEAR) > 127
    if not m.any():
        m[:] = True
    plate = plate_mm(thickness)
    avail = max(thickness - plate, 0.4)
    d = ndimage.distance_transform_edt(np.pad(m, 1))[1:-1, 1:-1] * GRID_MM
    dmax = max(float(d.max()), 1e-6)
    lum = _resample(rgb.mean(axis=2).astype(np.uint8), box, shape, Image.BILINEAR).astype(np.float32)

    # body: a gentle, even swell (the distance field's midline crease smoothed away)
    dome = ndimage.gaussian_filter(np.clip(d / dmax, 0, 1) ** 0.6, 1.2 / GRID_MM)
    lvl = detail or {"min_feature_mm": 2.0, "detail_sigma_mm": 0.5, "max_features": 16, "depth": 1.0}
    feat_amp = float(min(max(MIN_FEATURE_DEPTH, 0.18 * avail), 0.3 * avail + MIN_FEATURE_DEPTH))
    feat_amp = max(MIN_FEATURE_DEPTH, feat_amp * float(lvl.get("depth", 1.0)))
    if depth is not None:
        dg = _resample(depth.astype(np.float32), box, shape, Image.BILINEAR).astype(np.float32)
        lo, hi = np.percentile(dg[m], [2, 98])
        dn = np.clip((dg - lo) / max(hi - lo, 1e-6), 0, 1)
        dn = np.where(m, dn, float(dn[m].mean()))
        base = ndimage.gaussian_filter(dn, 2.5 / GRID_MM)
        bspan = max(float(np.ptp(base[m])), 1e-6)
        body = 0.5 * dome + 0.5 * np.tanh(2.0 * (base - float(base[m].min())) / bspan)
        # print-friendly detail: band-limit (bolts, lettering, tread ripples go), keep the steps
        detail = ndimage.gaussian_filter(dn - base, float(lvl["detail_sigma_mm"]) / GRID_MM)
        detail = denoise_tv_chambolle(detail, weight=0.01)
        ref = float(np.percentile(np.abs(detail[m]), 95)) or 1e-6
        feat = (np.clip(detail / ref, -1.5, 1.5) * feat_amp).astype(np.float32)
        feat, simp = _simplify_features(feat, m, feat_amp, float(lvl["min_feature_mm"]), int(lvl["max_features"]))
        # the features that remain are the important ones: give them the full depth again
        top = float(np.percentile(np.abs(feat[m]), 97))
        if top > 1e-6:
            feat = np.clip(feat * min(feat_amp / top, RENORM_CAP), -1.5 * feat_amp, 1.5 * feat_amp).astype(np.float32)
        stats["simplified"] = simp
        stats["source"] = "depth"
    else:
        lg = None
        if labels is not None and labels.max() >= 1:
            lg = _resample((labels + 1).astype(np.uint8), box, shape, Image.NEAREST).astype(np.int32) - 1
        reg, signs = feature_regions(lum, m, lg, float(lvl["min_feature_mm"]), int(lvl["max_features"]))
        feat = np.zeros(shape, np.float32)
        for k, sgn in enumerate(signs):
            if sgn:
                feat += sgn * feat_amp * _plateau(reg == k, 0.6)
        body = dome
        stats["source"] = "shading"
        stats["regions"] = (reg, signs)
        n_reg = int(reg.max()) + 1 if reg.size else 0
        stats["simplified"] = {"kept": sum(1 for x in signs if x), "small": max(0, n_reg - len(signs))}
    stats["feature_depth_mm"] = round(feat_amp, 2)

    # feature edges: rounded by detail_mm, independent of the body's softness
    detail_mm = float(edges.get("detail_mm", 0.4) or 0)
    if detail_mm > 0:
        feat = ndimage.gaussian_filter(feat, detail_mm / GRID_MM / 2)
    soften = float(finish.get("soften_mm", 0) or 0)
    if soften > 0:
        body = ndimage.gaussian_filter(body, soften / GRID_MM * 0.5)
    rise = body * max(avail - 2 * feat_amp, 0.3 * avail) + feat
    rise = rise - min(float(rise[m].min()), 0.0)
    rise *= avail / max(float(rise[m].max()), 1e-6)  # the highest point reaches the full thickness
    hgt = plate + rise

    stats["mask"] = m.copy()
    # the outline's edge: a short vertical wall on the bed, then the chosen profile up to the surface.
    # The distance comes from the fine, smooth outline (signed, sub-pixel), so the rounding has no ripples.
    style = str(edges.get("style", "soft"))
    outer = float(edges.get("outer_mm", 1.5) or 0)
    if style == "crisp" or outer <= 0:  # crisp still gets a hairline round-over: a clean rim, not grid steps
        style, outer = "soft", 0.25
    if outer > 0:
        if outline_grid is not None:
            fo = outline_grid.astype(bool)
            sd = (ndimage.distance_transform_edt(np.pad(fo, 1))[1:-1, 1:-1]
                  - ndimage.distance_transform_edt(~np.pad(fo, 1, constant_values=False))[1:-1, 1:-1]) * OUTLINE_MM
            sd = ndimage.gaussian_filter(sd.astype(np.float32), 1.2)
            de = np.asarray(Image.fromarray(sd).resize((shape[1], shape[0]), Image.BILINEAR))
        else:
            de = ndimage.gaussian_filter(d, 1.0)
        wall = min(0.8, plate)
        inside = hgt
        if (~m).any():  # outside cells first take the nearest inside height, then the profile brings them down
            _, (iy, ix) = ndimage.distance_transform_edt(~m, return_indices=True)
            inside = hgt[iy, ix]
        hgt = wall + (inside - wall) * edge_profile(np.clip(de, 0, None), style, outer)
        m = np.ones_like(m)  # heights are already right everywhere: no carrying below
    step = float(finish.get("terrace_mm", 0) or 0)
    if step > 0:  # voxel / blocky looks: flat terraces
        hgt = np.maximum(np.round(hgt / step) * step, step)
    hgt = np.clip(hgt, 0.4, thickness)
    stats["features_mm"] = feat
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
          thickness: float, min_feature_mm: float, finish: dict[str, Any] | None = None,
          depth: np.ndarray | None = None, edges: dict[str, Any] | None = None,
          detail: dict[str, Any] | None = None) -> tuple[mf.Manifold, dict[str, Any]]:
    """The relief body lying on its back (z up from the bed, picture top towards +y), centred.
    `edges` style "auto" (or missing) takes the treatment that matches the picture."""
    box, mm_px = fit(mask, height_mm)
    w_mm, h_mm = (box[3] - box[2]) * mm_px, (box[1] - box[0]) * mm_px
    m_out, removed = outline(mask, box, mm_px, min_feature_mm)
    edges = dict(edges or {})
    if edges.get("style", "auto") == "auto":
        sug = suggest_edges(rgb, mask, mm_px)
        edges = {**edges, "style": sug["style"], "outer_mm": edges.get("outer_mm") or sug["outer_mm"]}
        if sug["style"] == "crisp":
            edges["outer_mm"] = 0.0
        edges["auto"] = True
    if edges.get("outer_mm") is None:
        edges["outer_mm"] = {"soft": 1.5, "bevel": 0.8}.get(edges["style"], 0.0)
    if edges.get("detail_mm") is None:
        edges["detail_mm"] = 0.4
    stats: dict[str, Any] = {}
    hgt = heightmap(rgb, mask, labels, box, mm_px, thickness, finish, depth=depth, edges=edges, stats=stats,
                    outline_grid=m_out, detail=detail)
    hgt_full = hgt
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
            "features_from": stats.get("source"), "feature_depth_mm": stats.get("feature_depth_mm"),
            "simplified": stats.get("simplified"),
            "edges_used": {"style": edges["style"], "outer_mm": round(float(edges["outer_mm"]), 1),
                           "detail_mm": round(float(edges["detail_mm"]), 2), "auto": bool(edges.get("auto"))},
            "_outline": m_out, "_box_mm": (w_mm, h_mm), "_hgt": hgt_full, "_stats": stats}
    info["relief_check"] = relief_check(hgt_full, stats)
    return solid, info


def relief_check(hgt: np.ndarray, stats: dict[str, Any]) -> dict[str, Any]:
    """Do the features survive in the finished surface? For each feature region (or, from depth, the
    feature map's strong areas): its height against a ring just around it. And how well the surface's
    detail follows the intended features (correlation of high-passed shapes)."""
    m = stats.get("mask")
    feat = stats.get("features_mm")
    out: dict[str, Any] = {}
    if m is None or feat is None:
        return out
    hp = hgt - ndimage.gaussian_filter(hgt, 3.0 / GRID_MM)
    fp = feat - ndimage.gaussian_filter(feat, 3.0 / GRID_MM)
    inner = m & (ndimage.distance_transform_edt(m) * GRID_MM > 2.0)
    if inner.sum() > 20 and fp[inner].std() > 1e-6 and hp[inner].std() > 1e-6:
        out["relief_match"] = round(float(np.corrcoef(hp[inner], fp[inner])[0, 1]), 3)
    regions = stats.get("regions")
    if regions:
        reg, signs = regions
        found = distinct = 0
        for k, sgn in enumerate(signs):
            if not sgn:
                continue
            R = reg == k
            ring = ndimage.binary_dilation(R, iterations=int(1.0 / GRID_MM)) & ~R & m
            if not ring.any():
                continue
            found += 1
            if abs(float(np.median(hgt[R]) - np.median(hgt[ring]))) >= 0.8 * MIN_FEATURE_DEPTH:
                distinct += 1
        out["features"] = {"found": found, "distinct": distinct}
    return out


def render_front(hgt: np.ndarray, m_out: np.ndarray, light: tuple[float, float, float] = (-0.45, 0.55, 0.7)) -> Image.Image:
    """The finished surface seen from the front, lit like a studio photo: what the print will look like
    head-on, to put beside the approved picture."""
    gy, gx = np.gradient(hgt.astype(np.float32), GRID_MM)
    n = np.stack([-gx, gy, np.ones_like(hgt)], -1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    L = np.asarray(light, np.float32)
    L /= np.linalg.norm(L)
    shade = np.clip(0.25 + 0.85 * np.clip(n @ L, 0, 1), 0, 1)
    img = (np.stack([shade * 0.78, shade * 0.77, shade * 0.72], -1) * 255).astype(np.uint8)
    pic = Image.fromarray(img)
    mask = Image.fromarray((m_out * 255).astype(np.uint8)).resize(pic.size, Image.BILINEAR)
    out = Image.new("RGB", pic.size, (255, 255, 255))
    out.paste(pic, (0, 0), mask)
    return out.resize((pic.width * 2, pic.height * 2), Image.LANCZOS)


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


# ---- depth from the picture (Depth Anything V2 small, ~100 MB, local) --------------------------------

def depth_ready() -> bool:
    """Is the small depth model on this Mac? (It comes with the SDXL download, or on its own.)"""
    try:
        from .. import models

        return all(models._is_cached(DEPTH_REPO, f) for f in DEPTH_FILES)
    except Exception:
        return False


_DEPTH_PIPE: dict[str, Any] = {}


def _depth_pipe():
    if "pipe" not in _DEPTH_PIPE:
        from transformers import pipeline

        from ..engines.local import pick_device

        dev = pick_device("auto")
        _DEPTH_PIPE["pipe"] = pipeline("depth-estimation", model=DEPTH_REPO, device=dev if dev != "mps" else "mps")
    return _DEPTH_PIPE["pipe"]


def depth_for(image_path, mask: np.ndarray | None = None) -> np.ndarray | None:
    """The picture's relative depth (bigger = nearer), at the picture's size; None when the depth model
    isn't downloaded or can't run. Cached next to the picture, so rebuilding the print is instant."""
    from pathlib import Path

    image_path = Path(image_path)
    if not depth_ready():
        return None
    cache = image_path.with_name(image_path.stem + "_depth.npy")
    st = image_path.stat()
    try:
        if cache.exists() and cache.stat().st_mtime_ns >= st.st_mtime_ns:
            return np.load(cache)
    except (OSError, ValueError):
        pass
    try:
        img = Image.open(image_path).convert("RGB")
        out = _depth_pipe()(img)
        pd = out.get("predicted_depth")
        arr = np.asarray(pd.squeeze().cpu().numpy() if hasattr(pd, "cpu") else out["depth"], dtype=np.float32)
        arr = np.asarray(Image.fromarray(arr).resize(img.size, Image.BILINEAR), dtype=np.float32)
        np.save(cache, arr)
        return arr
    except Exception as e:  # never stop a print over the depth model: the shading-read features still work
        import logging

        logging.getLogger(__name__).warning("depth model failed (%s); reading features from shading", e)
        return None
