"""Logos and text: built straight from your artwork, so every letter stays exactly as drawn.

An AI picture model can't spell and a 3D model loses thin letters, so for flat graphics we
skip both:

  1. `is_graphic`: is this a flat graphic (a few flat colours on a plain background)?
  2. `layers`: the artwork split into its colours (at most 3), background removed, holes kept
  3. `build`: each colour becomes a raised, puffy part on a plaque (or a sticker-cut outline),
     in the house finish, as separate parts for a multi-colour printer (filaments 1..4)
"""
from __future__ import annotations

import io
import json
import math
from pathlib import Path
from typing import Any, Callable

import manifold3d as mf
import numpy as np
import trimesh
from PIL import Image, ImageOps
from scipy import ndimage
from skimage import color, measure

from . import colours as colours_mod

Progress = Callable[[float, str], None]

MAX_COLOURS = 3  # the plaque is the 4th filament
SINK_MM = 0.2  # raised parts sit this deep in the plaque so they key together
LAYERS_FILE = "logo_layers.npz"
SIMPLIFY_MM = 0.01  # how far a surface may move when triangles are merged (far below what a printer shows)
BACKERS = ("plaque", "outline", "bar")

DEFAULTS: dict[str, Any] = {
    "backer": "plaque",  # plaque · outline (sticker cut) · bar (outline standing on a bar; Stand only)
    "width_mm": 80,
    "margin_mm": 4,
    "corner_mm": 4,
    "plaque_mm": 3,
    "raise_mm": 2,
    "min_stroke_mm": 1.0,
}


# ---- reading the artwork -------------------------------------------------------

def _open(src: Path | Image.Image) -> Image.Image:
    img = src if isinstance(src, Image.Image) else Image.open(src)
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        if np.asarray(rgba.getchannel("A")).min() < 250:  # transparent background: put it on white
            flat = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            flat.alpha_composite(rgba)
            return flat.convert("RGB")
    return img.convert("RGB")


def _lab(rgb: np.ndarray) -> np.ndarray:
    return color.rgb2lab(rgb.astype(np.float32) / 255.0)


def _border(a: np.ndarray) -> np.ndarray:
    b = max(1, min(a.shape[:2]) // 50)
    return np.concatenate([a[:b].reshape(-1, a.shape[-1]), a[-b:].reshape(-1, a.shape[-1]),
                           a[:, :b].reshape(-1, a.shape[-1]), a[:, -b:].reshape(-1, a.shape[-1])])


def _kmeans(x: np.ndarray, k: int, seed: int = 0, iters: int = 25) -> tuple[np.ndarray, np.ndarray]:
    """Plain k-means (k-means++ start). Returns (centres, labels)."""
    rng = np.random.default_rng(seed)
    k = max(1, min(k, len(x)))
    centres = [x[rng.integers(len(x))]]
    for _ in range(1, k):
        d = np.min(((x[:, None, :] - np.asarray(centres)[None]) ** 2).sum(-1), axis=1)
        if d.sum() <= 0:
            break
        centres.append(x[rng.choice(len(x), p=d / d.sum())])
    c = np.asarray(centres, dtype=np.float64)
    labels = np.zeros(len(x), dtype=int)
    for _ in range(iters):
        labels = np.argmin(((x[:, None, :] - c[None]) ** 2).sum(-1), axis=1)
        new = np.array([x[labels == i].mean(0) if (labels == i).any() else c[i] for i in range(len(c))])
        if np.allclose(new, c, atol=0.05):
            break
        c = new
    return c, labels


def _interior(lab: np.ndarray) -> np.ndarray:
    """Pixels whose neighbours have the same colour (not anti-aliased edges)."""
    grad = np.zeros(lab.shape[:2], dtype=np.float32)
    for ax in (0, 1):
        grad = np.maximum(grad, np.abs(np.diff(lab[..., 0], axis=ax, prepend=lab[..., 0].take([0], axis=ax))))
    return ndimage.maximum_filter(grad, size=3) < 6


def is_graphic(src: Path | Image.Image) -> bool:
    """A flat graphic: a plain background and a few flat colours covering nearly every pixel."""
    img = _open(src)
    img.thumbnail((640, 640), Image.LANCZOS)
    lab = _lab(np.asarray(img))
    border = _border(lab)
    bg = np.median(border, axis=0)
    if np.mean(np.linalg.norm(border - bg, axis=1) < 8) < 0.9:  # busy or shaded background: a photo
        return False
    fg = np.linalg.norm(lab - bg, axis=2) > 20
    share = fg.mean()
    if share < 0.004 or share > 0.9:
        return False
    flat = fg & _interior(lab)
    pts = lab[flat]
    if len(pts) < 50:  # all edges, no flat areas (thin line art): treat as a graphic
        return True
    pts = pts[np.random.default_rng(0).choice(len(pts), min(len(pts), 6000), replace=False)]
    centres, labels = _kmeans(pts, 5)
    err = np.linalg.norm(pts - centres[labels], axis=1)
    # photos have shading and texture: many flat-area pixels far from any of 5 colours
    return bool(np.mean(err < 3) >= 0.85) and bool(flat.sum() / max(fg.sum(), 1) > 0.15)


def layers(src: Path | Image.Image, max_side: int = 2400, max_colours: int = MAX_COLOURS) -> dict[str, Any]:
    """The artwork's colours as masks: {"layers": [{hex, name, mask}], "size": [w, h]} (masks are rows top→bottom)."""
    img = _open(src)
    long = max(img.size)
    scale = max_side / long if long > max_side else max(1.0, 1200 / long)
    if scale != 1.0:
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
    rgb = np.asarray(img)
    lab = _lab(rgb)
    bg = np.median(_border(lab), axis=0)
    fg = np.linalg.norm(lab - bg, axis=2) > 22
    if not fg.any():
        raise ValueError("Couldn't find anything on the background")
    ys, xs = np.nonzero(fg)
    pad = 4
    y0, y1, x0, x1 = max(0, ys.min() - pad), min(fg.shape[0], ys.max() + pad + 1), max(0, xs.min() - pad), min(fg.shape[1], xs.max() + pad + 1)
    rgb, lab, fg = rgb[y0:y1, x0:x1], lab[y0:y1, x0:x1], fg[y0:y1, x0:x1]

    # the colours: from flat (interior) pixels only, so edge blends don't become colours of their own
    flat = fg & _interior(lab)
    pts = lab[flat] if flat.sum() >= 50 else lab[fg]
    pts = pts[np.random.default_rng(0).choice(len(pts), min(len(pts), 40000), replace=False)]
    centres, labels = _kmeans(pts, max_colours + 2)
    counts = np.bincount(labels, minlength=len(centres)).astype(float)
    keep = [i for i in np.argsort(-counts) if counts[i] / counts.sum() > 0.01]
    merged: list[np.ndarray] = []
    weights: list[float] = []
    for i in keep:  # merge near-identical colours (ΔE < 15)
        hit = next((j for j, c in enumerate(merged) if np.linalg.norm(c - centres[i]) < 15), None)
        if hit is None:
            merged.append(centres[i].copy())
            weights.append(counts[i])
        else:
            w = weights[hit] + counts[i]
            merged[hit] = (merged[hit] * weights[hit] + centres[i] * counts[i]) / w
            weights[hit] = w
    merged = merged[:max_colours]
    C = np.asarray(merged)

    # every foreground pixel goes to its nearest colour (anti-aliased edges included)
    flat_lab = lab.reshape(-1, 3)
    nearest = np.argmin(((flat_lab[:, None, :] - C[None]) ** 2).sum(-1), axis=1).reshape(fg.shape)
    min_area = max(12, int((max(fg.shape) / 600) ** 2 * 6))
    out = []
    for i, c in enumerate(C):
        m = fg & (nearest == i)
        m = ndimage.binary_opening(m, structure=np.ones((2, 2)))  # one-pixel slivers between colours
        lab_, n = ndimage.label(m)
        if n:
            sizes = ndimage.sum(m, lab_, range(1, n + 1))
            m = np.isin(lab_, 1 + np.nonzero(sizes >= min_area)[0])
        # fill only tiny holes (specks); real holes (the inside of a, o, e) stay
        holes = ndimage.binary_fill_holes(m) & ~m
        hl, hn = ndimage.label(holes)
        if hn:
            hs = ndimage.sum(holes, hl, range(1, hn + 1))
            m |= np.isin(hl, 1 + np.nonzero(hs < min_area)[0])
        if m.sum() < min_area:
            continue
        rgb_c = tuple(int(v) for v in np.clip(color.lab2rgb(c[None, None, :].astype(np.float64))[0, 0] * 255, 0, 255).round())
        out.append({"hex": "#{:02X}{:02X}{:02X}".format(*rgb_c), "name": colours_mod.name_for(rgb_c), "mask": m})
    if not out:
        raise ValueError("Couldn't find the logo's shapes")
    out.sort(key=lambda d: float(np.nonzero(d["mask"].any(0))[0].mean()))  # left to right: reading order
    return {"layers": out, "size": [int(fg.shape[1]), int(fg.shape[0])]}


_LAYERS_CACHE: dict[tuple, dict[str, Any]] = {}


def layers_cached(path: Path) -> dict[str, Any]:
    """layers() of a file, remembered while the file is unchanged (the Style, Shape and Print steps all use it)."""
    st = Path(path).stat()
    key = (str(Path(path).resolve()), st.st_mtime_ns, st.st_size)
    if key not in _LAYERS_CACHE:
        if len(_LAYERS_CACHE) > 6:
            _LAYERS_CACHE.pop(next(iter(_LAYERS_CACHE)))
        _LAYERS_CACHE[key] = layers(path)
    return _LAYERS_CACHE[key]


def save_layers(data: dict[str, Any], path: Path) -> None:
    arrays = {f"mask_{i}": np.packbits(l["mask"], axis=None) for i, l in enumerate(data["layers"])}
    meta = {"size": data["size"], "layers": [{"hex": l["hex"], "name": l["name"]} for l in data["layers"]]}
    buf = io.BytesIO()
    np.savez_compressed(buf, meta=np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8), **arrays)
    path.write_bytes(buf.getvalue())


def load_layers(path: Path) -> dict[str, Any]:
    z = np.load(path)
    meta = json.loads(bytes(z["meta"]).decode())
    w, h = meta["size"]
    out = []
    for i, l in enumerate(meta["layers"]):
        mask = np.unpackbits(z[f"mask_{i}"])[: w * h].reshape(h, w).astype(bool)
        out.append({**l, "mask": mask})
    return {"layers": out, "size": [w, h]}


MAX_PAINTS = 16  # with colour reduction off, every real colour is kept, up to this many


def _same_paint(a: np.ndarray, b: np.ndarray) -> bool:
    """Are two Lab colours the same paint seen in different light (shading, highlight, texture)?

    Shading changes lightness and a little chroma but keeps the hue: a shaded olive is still olive,
    while black eyes, a white belly and an orange beak are different paints.
    """
    ca, cb = float(np.hypot(a[1], a[2])), float(np.hypot(b[1], b[2]))
    dl = abs(float(a[0] - b[0]))
    if ca < 8 and cb < 8:  # two neutrals (white, greys, black): same only if close in lightness
        return dl < 25
    if min(ca, cb) < 8:
        return False
    dh = abs(math.degrees(math.atan2(a[2], a[1]) - math.atan2(b[2], b[1]))) % 360
    dh = min(dh, 360 - dh)
    return dh < 20 and min(ca, cb) / max(ca, cb) > 0.45 and dl < 40


def regions(img: "Image.Image", mask: np.ndarray, n: int | None = None, mm_per_px: float | None = None,
            min_mm: float = 1.2, palette: list[dict[str, str]] | None = None
            ) -> tuple[np.ndarray, list[dict[str, str]]]:
    """The picture's colour regions, chunky enough to print: (label image, colours biggest-first).

    Regions are *paints*, not light: the picture is smoothed (clay texture, speckle), clustered with
    lightness down-weighted, then clusters that are the same paint in different light are merged, so a
    shaded one-colour object stays one region. `n=None` keeps every real colour (no reduction, at most
    MAX_PAINTS); `n=k` reduces to at most k by merging the closest paints. Regions narrower than
    `min_mm` (given `mm_per_px`) can't print as their own colour and join their neighbour.

    With an approved `palette` ([{hex, name}], from the Photo step's "Reduce colours") every pixel
    simply takes the nearest approved colour, and the colours returned are those.

    Labels: -1 outside the mask, else 0..k-1 into the returned colour list. Every masked pixel is
    assigned, so the regions partition the silhouette exactly.
    """
    H, W = mask.shape
    rgb_full = np.asarray(img.convert("RGB").resize((W, H)) if img.size != (W, H) else img.convert("RGB"))
    ys, xs = np.nonzero(mask)
    if not len(ys):
        return np.full(mask.shape, -1, np.int32), []
    # work on the object's box at ~600 px: fast, and plenty for print-sized colour regions
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    f = min(1.0, 600 / max(y1 - y0, x1 - x0))
    sw, sh = max(1, round((x1 - x0) * f)), max(1, round((y1 - y0) * f))
    small = np.asarray(Image.fromarray(rgb_full[y0:y1, x0:x1]).resize((sw, sh), Image.LANCZOS))
    m = np.asarray(Image.fromarray(mask[y0:y1, x0:x1].astype(np.uint8) * 255).resize((sw, sh), Image.NEAREST)) > 127
    if not m.any():
        m[sh // 2, sw // 2] = True
    span = max(sh, sw)
    k = max(3, int(span * 0.01)) | 1
    smooth = np.stack([ndimage.median_filter(small[..., c], size=k) for c in range(3)], -1)
    lab = _lab(smooth.reshape(-1, 3)).reshape(sh, sw, 3)
    w = np.array([0.5, 1.0, 1.0])
    px = lab[m]
    want = MAX_PAINTS if n is None else max(1, int(n))
    if palette:
        centres = _lab(np.array([[int(c["hex"][k:k + 2], 16) for k in (1, 3, 5)] for c in palette], np.uint8))
        want = len(palette)
    else:
        centres, _ = _kmeans(px[:: max(1, len(px) // 40000)] * w, min(want + 3, 12), seed=3)
        centres = centres / w
    lab_id = np.argmin(np.linalg.norm(((lab[..., None, :] - centres[None, None]) * w), axis=-1), axis=-1)
    counts = np.bincount(lab_id[m], minlength=len(centres)).astype(float)
    # how sharp the lightness changes are: paint meets paint at an edge, light fades over a ramp
    grad = np.hypot(ndimage.sobel(lab[..., 0], 0), ndimage.sobel(lab[..., 0], 1)) / 8

    def soft_boundary(i: int, j: int) -> bool:
        a, b = (lab_id == i) & m, (lab_id == j) & m
        edge = (ndimage.binary_dilation(a) & b) | (ndimage.binary_dilation(b) & a)
        return not edge.any() or float(np.median(grad[edge])) < 2.5
    # merge clusters that are one paint in different light (union-find over the cluster pairs)
    parent = list(range(len(centres)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    live = [i for i in range(len(centres)) if counts[i] > 0]
    for i in live:
        for j in live:
            if palette or i >= j or find(i) == find(j) or not _same_paint(centres[i], centres[j]):
                continue
            # close in lightness: shading for sure; further apart: only if they fade into each other
            if abs(float(centres[i][0] - centres[j][0])) < 22 or soft_boundary(i, j):
                parent[find(j)] = find(i)
    groups: dict[int, list[int]] = {}
    for i in live:
        groups.setdefault(find(i), []).append(i)
    paints = [[g, float(counts[g].sum()), (centres[g] * counts[g, None]).sum(0) / counts[g].sum()]
              for g in (np.array(v) for v in groups.values())]
    # colour reduction: merge the closest pair (weighted by size, so big regions keep their colour)
    while len(paints) > want:
        best = None
        for a in range(len(paints)):
            for b in range(a + 1, len(paints)):
                d = np.linalg.norm((paints[a][2] - paints[b][2]) * w) * min(paints[a][1], paints[b][1]) ** 0.25
                if best is None or d < best[0]:
                    best = (d, a, b)
        _, a, b = best
        pa, pb = paints[a], paints[b]
        tot = pa[1] + pb[1]
        paints[a] = [np.concatenate([pa[0], pb[0]]), tot, (pa[2] * pa[1] + pb[2] * pb[1]) / tot]
        del paints[b]
    lut = np.zeros(len(centres), np.int32)
    for pi, (g, _, _) in enumerate(paints):
        lut[g] = pi
    pid = lut[lab_id]
    # printable: open each region with the smallest feature a nozzle can lay down in its own colour
    px_mm = (mm_per_px / f) if mm_per_px else None
    r = max(1, int(round((min_mm / 2) / px_mm))) if px_mm else max(2, int(span * 0.006))
    disk = np.hypot(*np.mgrid[-r:r + 1, -r:r + 1]) <= r
    min_area = (min_mm ** 2 * 2) / (px_mm ** 2) if px_mm else 0.002 * m.sum()
    solid = np.full(m.shape, -1, np.int32)
    keep: list[int] = []
    for i in range(len(paints)):
        reg = ndimage.binary_opening(m & (pid == i), structure=disk)
        if reg.sum() >= max(min_area, 4):
            solid[reg] = len(keep)
            keep.append(i)
    if not keep:
        solid[m] = 0
        keep = [int(np.bincount(pid[m]).argmax())]
    missing = m & (solid < 0)
    if missing.any():
        _, (iy, ix) = ndimage.distance_transform_edt(solid < 0, return_indices=True)
        solid[missing] = solid[iy[missing], ix[missing]]
    # back to full size; edge pixels the small grid didn't own take the nearest region
    big = np.asarray(Image.fromarray((solid + 1).astype(np.uint8)).resize((x1 - x0, y1 - y0), Image.NEAREST)).astype(np.int32) - 1
    full = np.full(mask.shape, -1, np.int32)
    full[y0:y1, x0:x1] = big
    hole = mask & (full < 0)
    if hole.any():
        _, (iy, ix) = ndimage.distance_transform_edt(full < 0, return_indices=True)
        full[hole] = full[iy[hole], ix[hole]]
    full[~mask] = -1
    # biggest region first: that's filament 1, and any boolean slivers join it later
    order = np.argsort([-np.sum(full == i) for i in range(len(keep))], kind="stable")
    out = np.full(mask.shape, -1, np.int32)
    cols = []
    for new, old in enumerate(order):
        sel = full == old
        if not sel.any():
            continue
        out[sel] = len(cols)
        if palette:
            src = [g for g in paints[keep[old]][0]][0]
            cols.append({"hex": palette[src]["hex"].upper(), "name": palette[src]["name"]})
            out[sel] = len(cols) - 1
            continue
        px = rgb_full[sel]
        lum = px.mean(axis=1)
        lo_, hi_ = np.percentile(lum, [50, 92])
        lit = px[(lum >= lo_) & (lum <= hi_)]  # the paint as it looks lit: not its shadows, not glare
        mean = np.median(lit if len(lit) else px, axis=0).astype(int)
        cols.append({"hex": "#{:02X}{:02X}{:02X}".format(*mean), "name": colours_mod.name_for(tuple(mean))})
    return out, cols


def palette(data: dict[str, Any]) -> list[dict[str, str]]:
    return [{"hex": l["hex"], "name": l["name"]} for l in data["layers"]]


# ---- a quick look (Style step) -----------------------------------------------------

def preview(data: dict[str, Any], colours: list[dict[str, str]], plaque_hex: str, size: int = 1024) -> Image.Image:
    """The exact logo, shaded like soft clay, on its plaque: an instant picture with no AI."""
    w, h = data["size"]
    s = (size * 0.8) / max(w, h)
    W, H = max(1, round(w * s)), max(1, round(h * s))
    margin = max(10, int(min(W, H) * 0.12))
    canvas = np.ones((size, size, 3), dtype=np.float32)
    ox, oy = (size - W) // 2, (size - H) // 2
    # plaque: a rounded rectangle with a soft shadow
    pw, ph = W + 2 * margin, H + 2 * margin
    px, py = ox - margin, oy - margin
    yy, xx = np.mgrid[0:size, 0:size]
    r = margin * 0.9
    dx = np.maximum(np.abs(xx - (px + pw / 2)) - (pw / 2 - r), 0)
    dy = np.maximum(np.abs(yy - (py + ph / 2)) - (ph / 2 - r), 0)
    plaque = np.hypot(dx, dy) <= r
    shadow = ndimage.gaussian_filter(np.roll(plaque.astype(np.float32), (10, 6), (0, 1)), 12)
    canvas *= (1 - 0.18 * shadow)[..., None]
    pc = np.array([int(plaque_hex[i:i + 2], 16) for i in (1, 3, 5)], np.float32) / 255
    canvas[plaque] = pc * 0.97
    light = np.array([-0.5, -0.6, 0.62])
    light /= np.linalg.norm(light)
    for layer, c in zip(data["layers"], colours):
        m = np.asarray(Image.fromarray(layer["mask"].astype(np.uint8) * 255).resize((W, H), Image.LANCZOS)) > 127
        if not m.any():
            continue
        d = ndimage.distance_transform_edt(m)
        rr = max(2.0, min(10.0, size / 120))
        hgt = np.sqrt(np.clip(d / rr, 0, 1)) * rr
        gy, gx = np.gradient(ndimage.gaussian_filter(hgt, 1.0))
        n = np.dstack([-gx, -gy, np.ones_like(hgt)])
        n /= np.linalg.norm(n, axis=2, keepdims=True)
        shade = np.clip(0.55 + 0.6 * (n @ light), 0.35, 1.15)
        col = np.array([int(c["hex"][i:i + 2], 16) for i in (1, 3, 5)], np.float32) / 255
        sub = canvas[oy:oy + H, ox:ox + W]
        drop = ndimage.gaussian_filter(np.roll(m.astype(np.float32), (3, 2), (0, 1)), 2) * 0.25
        sub *= (1 - drop)[..., None]
        sub[m] = np.clip(col[None] * shade[m][:, None] + (shade[m][:, None] - 1).clip(0) * 0.6, 0, 1)
    return Image.fromarray((np.clip(canvas, 0, 1) * 255).astype(np.uint8))


# ---- building the print (Print step) --------------------------------------------------

def _disk(r: float) -> np.ndarray:
    r = max(1, int(round(r)))
    g = np.mgrid[-r:r + 1, -r:r + 1]
    return (g ** 2).sum(0) <= r * r + 0.5


def _sdf(mask: np.ndarray, pitch: float) -> np.ndarray:
    """Signed distance in mm, positive inside."""
    inside = ndimage.distance_transform_edt(mask)
    outside = ndimage.distance_transform_edt(~mask)
    return (np.where(mask, inside - 0.5, -(outside - 0.5))) * pitch


def _rounded_rect_sdf(shape: tuple[int, int], box: tuple[float, float, float, float], r: float, pitch: float) -> np.ndarray:
    """Positive inside a rounded rectangle; box = (x0, y0, x1, y1) in cells."""
    ys, xs = np.mgrid[0:shape[0], 0:shape[1]].astype(np.float32)
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    hx, hy = (box[2] - box[0]) / 2, (box[3] - box[1]) / 2
    rc = min(r / pitch, hx, hy)
    qx = np.abs(xs - cx) - (hx - rc)
    qy = np.abs(ys - cy) - (hy - rc)
    out = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - rc
    return -out * pitch


def _surface(field: np.ndarray, pitch: float, dz: float, z0: float, offset_xy: tuple[float, float]) -> trimesh.Trimesh:
    """Closed mesh of field > 0 (field indexed [row, col, z]); rows run top→bottom."""
    f = np.pad(field.astype(np.float32), 1, constant_values=-1.0)
    verts, faces, _, _ = measure.marching_cubes(f, level=0.0, spacing=(pitch, pitch, dz))
    rows, cols, zs = verts[:, 0] - pitch, verts[:, 1] - pitch, verts[:, 2] - dz
    v = np.column_stack([cols + offset_xy[0], -(rows + offset_xy[1]), zs + z0])  # x right, y up
    m = trimesh.Trimesh(v, faces[:, ::-1], process=True)
    if m.volume < 0:
        m.invert()
    return m


def _round_top(sdf2: np.ndarray, top: np.ndarray, r: float) -> np.ndarray:
    """Intersection of 'inside the outline' and 'below the top' with a rounded edge of radius r (all mm)."""
    if r <= 0:
        return np.minimum(sdf2, top)
    q = np.maximum(r - sdf2, 0)
    w = np.maximum(r - top, 0)
    near = (sdf2 < r) | (top < r)
    return np.where(near, r - np.hypot(q, w), np.minimum(sdf2, top))


def _thicken(mask: np.ndarray, min_cells: float) -> tuple[np.ndarray, float]:
    """Grow strokes thinner than min_cells; returns (mask, share of the shape that was too thin)."""
    r = min_cells / 2
    if r < 1:
        return mask, 0.0
    opened = ndimage.binary_opening(np.pad(mask, int(r) + 2), structure=_disk(r))[int(r) + 2:-(int(r) + 2), int(r) + 2:-(int(r) + 2)]
    thin = mask & ~opened
    share = float(thin.sum() / max(mask.sum(), 1))
    if share < 0.01:
        return mask, share
    grow = ndimage.binary_dilation(thin, structure=_disk(max(1, r / 2)))
    return mask | grow, share


def _to_manifold(m: trimesh.Trimesh) -> mf.Manifold:
    from .printify import to_manifold

    return to_manifold(m)


def build(data: dict[str, Any], params: dict[str, Any], progress: Progress | None = None) -> dict[str, Any]:
    """Parts for the printer: [{id, label, mesh}] (raised colours first, the plaque last) and a report."""
    from .printify import _overhang_pct, add_ring, facet, magnet_cutter, to_trimesh

    say = progress or (lambda p, m: None)
    lp = {**DEFAULTS, **(params.get("logo") or {})}
    geo = params.get("geometry") or {}
    fmt = params.get("format", "stand")
    mag = params.get("magnet") or {}
    ring_p = params.get("ring") or {}
    finish = str(geo.get("finish", "soft"))
    backer = lp["backer"] if lp["backer"] in BACKERS else "plaque"
    if backer == "bar" and fmt != "stand":
        backer = "outline"
    warnings: list[str] = []

    width = float(lp["width_mm"])
    margin = max(0.0, float(lp["margin_mm"]))
    T = float(lp["plaque_mm"])
    raise_mm = max(0.4, float(lp["raise_mm"]))
    if mag.get("enabled") and fmt == "magnet":
        need = float(mag.get("depth_mm", 2)) + float(mag.get("clearance_mm", 0.15)) + 0.8
        if T < need:
            T = need
            warnings.append(f"The plaque was made {T:.1f} mm thick to fit the magnet with a 0.8 mm roof.")

    w_px, h_px = data["size"]
    total_w = width + 2 * margin
    pitch = max(0.08, total_w / 700)
    mm_per_px = width / w_px
    W, H = max(2, round(width / pitch)), max(2, round(h_px * mm_per_px / pitch))
    pad = int(math.ceil((margin + 1.0) / pitch)) + 2
    gshape = (H + 2 * pad, W + 2 * pad)

    say(0.1, "Reading the logo's shapes")
    masks = []
    for layer in data["layers"]:
        m = np.asarray(Image.fromarray(layer["mask"].astype(np.uint8) * 255).resize((W, H), Image.BOX)) >= 128
        full = np.zeros(gshape, dtype=bool)
        full[pad:pad + H, pad:pad + W] = m
        min_cells = float(lp["min_stroke_mm"]) / pitch
        full, share = _thicken(full, min_cells)
        if share >= 0.01:
            warnings.append(f"Thin strokes in the {layer['name'].lower()} part were thickened to "
                            f"{float(lp['min_stroke_mm']):.1f} mm ({share:.0%} of it).")
        masks.append(full)
    union = np.logical_or.reduce(masks)
    if not union.any():
        raise ValueError("The logo is too small to print at this width")

    # the plaque or outline the parts sit on
    say(0.2, "Making the plaque")
    if backer == "plaque":
        ys, xs = np.nonzero(union)
        m_cells = margin / pitch
        box = (xs.min() - m_cells, ys.min() - m_cells, xs.max() + 1 + m_cells, ys.max() + 1 + m_cells)
        back2 = _rounded_rect_sdf(gshape, box, float(lp["corner_mm"]), pitch)
    else:
        grown = _sdf(union, pitch) > -max(margin, 1.0)
        grown = ndimage.binary_fill_holes(grown)
        rc = max(1, int(round(max(margin, 1.0) / pitch)))
        grown = ndimage.binary_closing(np.pad(grown, rc + 1), structure=_disk(rc))[rc + 1:-(rc + 1), rc + 1:-(rc + 1)]
        lbl, n = ndimage.label(grown)
        if n > 1:  # pieces too far apart for the margin to join: bridge them with the plaque shape
            ys, xs = np.nonzero(union)
            bar_h = max(2.0, margin) / pitch
            yc = (ys.min() + ys.max()) / 2
            grown |= _rounded_rect_sdf(gshape, (xs.min(), yc - bar_h / 2, xs.max() + 1, yc + bar_h / 2), 1.0, pitch) > 0
            warnings.append("Parts of the logo were too far apart for the outline; a bar joins them.")
        back2 = _sdf(grown, pitch)

    dz = min(pitch, 0.12)
    # raised parts: puffy domes (soft), flat-topped blocks (voxel), or cut into facets
    rr = float(np.clip(float(geo.get("soften_mm", 0.9)) * 0.6, 0.35, 1.4))
    parts: list[dict[str, Any]] = []
    say(0.35, "Raising the logo")
    for i, (layer, m) in enumerate(zip(data["layers"], masks)):
        if not m.any():
            continue
        ys, xs = np.nonzero(m)
        bp = 3
        r0, r1, c0, c1 = max(0, ys.min() - bp), min(gshape[0], ys.max() + bp + 1), max(0, xs.min() - bp), min(gshape[1], xs.max() + bp + 1)
        sub = m[r0:r1, c0:c1]
        if finish == "voxel":
            b = max(1, int(round(min(float(geo.get("voxel_size_mm", 2.5)), max(0.6, width / 70)) / pitch)))
            hgt, wid = sub.shape
            ph_, pw_ = (-hgt) % b, (-wid) % b
            blocks = np.pad(sub, ((0, ph_), (0, pw_))).reshape((hgt + ph_) // b, b, (wid + pw_) // b, b).mean((1, 3)) >= 0.5
            sub = np.repeat(np.repeat(blocks, b, 0), b, 1)[:hgt, :wid]
            sd = np.where(sub, 1.0, -1.0) * pitch * 0.5
            top_mm = np.where(sub, raise_mm, 0.0)
        else:
            sd = _sdf(sub, pitch)
            dome = 0.3 + 0.7 * np.sqrt(np.clip(sd / rr, 0, 1))
            top_mm = raise_mm * dome
        z_lo = T - SINK_MM
        zs = z_lo + np.arange(int(math.ceil((T + raise_mm + dz - z_lo) / dz)) + 1) * dz
        top = (T + top_mm)[..., None] - zs[None, None, :]
        bottom = zs[None, None, :] - z_lo
        field = np.minimum(_round_top(sd[..., None], top, 0.0 if finish == "voxel" else min(0.3, rr / 2)), bottom)
        mesh = _surface(field, pitch, dz, z_lo, (c0 * pitch, r0 * pitch))
        if finish == "faceted":
            mesh = facet(mesh, max(200, int(geo.get("facets", 400)) * 2), warnings)
        parts.append({"id": f"logo_{i + 1}", "label": layer["name"], "mesh": mesh})
        say(0.35 + 0.3 * (i + 1) / len(masks), f"Raised {layer['name'].lower()}")

    # the plaque body
    say(0.7, "Shaping the plaque")
    zs = np.arange(int(math.ceil(T / dz)) + 1) * dz
    field = np.minimum(_round_top(back2[..., None], (T - zs)[None, None, :], min(1.0, T * 0.35)), zs[None, None, :] - 0.0)
    plaque = _surface(field, pitch, dz, 0.0, (0.0, 0.0))

    # booleans: no part overlaps another or the plaque (each later colour gives way to earlier ones)
    say(0.78, "Fitting the parts together")
    raised_m: list[mf.Manifold] = []
    for p in parts:
        pm = _to_manifold(p["mesh"]).simplify(SIMPLIFY_MM)  # flat areas need few triangles
        for prev in raised_m:
            pm = pm - prev
        raised_m.append(pm)
    plaque_m = _to_manifold(plaque).simplify(SIMPLIFY_MM)
    for pm in raised_m:
        plaque_m = plaque_m - pm

    # centre the whole thing on the bed
    bb = plaque_m.bounding_box()
    cx, cy = (bb[0] + bb[3]) / 2, (bb[1] + bb[4]) / 2
    plaque_m = plaque_m.translate((-cx, -cy, 0))
    raised_m = [pm.translate((-cx, -cy, 0)) for pm in raised_m]

    if fmt == "magnet" and mag.get("enabled"):
        say(0.84, "Cutting the magnet pocket")
        d_m = float(mag.get("diameter_mm", 10))
        # the deepest point inside the plaque, so the pocket never breaks through an edge
        inside = back2 > 0
        dist = ndimage.distance_transform_edt(inside) * pitch
        r_i, c_i = np.unravel_index(np.argmax(dist), dist.shape)
        if dist[r_i, c_i] < d_m / 2 + 1.0:
            warnings.append("The plaque is narrow for this magnet; check the pocket in the slicer.")
        mx, my = c_i * pitch - cx, -(r_i * pitch) - cy
        if inside[int(round(gshape[0] / 2)), int(round(gshape[1] / 2))]:
            ctr = ndimage.distance_transform_edt(inside)[gshape[0] // 2, gshape[1] // 2] * pitch
            if ctr >= d_m / 2 + 1.0:
                mx, my = (gshape[1] / 2) * pitch - cx, -(gshape[0] / 2) * pitch - cy
        cutter = magnet_cutter(d_m, float(mag.get("depth_mm", 2)), float(mag.get("clearance_mm", 0.15))).translate((mx, my, 0))
        plaque_m = plaque_m - cutter
    if fmt == "keychain" and ring_p.get("enabled"):
        say(0.86, "Adding the key-ring loop")
        plaque_m = add_ring(plaque_m, {**ring_p, "thickness_mm": min(float(ring_p.get("thickness_mm", 3.5)), T)}, warnings)
        for pm in raised_m:
            plaque_m = plaque_m - pm
    if fmt == "stand":
        say(0.86, "Standing it on a foot")
        # face forward (−y), top up (+z)
        plaque_m = plaque_m.rotate((90, 0, 0))
        raised_m = [pm.rotate((90, 0, 0)) for pm in raised_m]
        bb = plaque_m.bounding_box()
        depth = max(16.0, (T + raise_mm) * 3)
        foot_w = (bb[3] - bb[0]) if backer == "plaque" else (bb[3] - bb[0]) + 2 * margin
        foot_t = 3.0
        r = min(3.0, depth / 3)
        foot = mf.CrossSection.square((foot_w - 2 * r, depth - 2 * r)).translate((-(foot_w - 2 * r) / 2, -(depth - 2 * r) / 2))
        foot = foot.offset(r, mf.JoinType.Round, circular_segments=48).extrude(foot_t)
        y_mid = (bb[1] + bb[4]) / 2 - (raise_mm / 2)  # under the plaque, a little forward for balance
        foot = foot.translate(((bb[0] + bb[3]) / 2, y_mid, 0))
        lift = foot_t - 0.4 - bb[2]
        plaque_m = plaque_m.translate((0, 0, lift))
        raised_m = [pm.translate((0, 0, lift)) for pm in raised_m]
        plaque_m = plaque_m + foot
        for pm in raised_m:
            plaque_m = plaque_m - pm
        bb = plaque_m.bounding_box()
        plaque_m = plaque_m.translate((0, -(bb[1] + bb[4]) / 2, 0))
        raised_m = [pm.translate((0, -(bb[1] + bb[4]) / 2, 0)) for pm in raised_m]

    say(0.94, "Checking printability")
    out = []
    for p, pm in zip(parts, raised_m):
        tm = to_trimesh(pm)
        if len(tm.faces):
            out.append({"id": p["id"], "label": p["label"], "mesh": tm})
    plaque_t = to_trimesh(plaque_m)
    out.append({"id": "plaque", "label": "Plaque" if backer == "plaque" else "Backing", "mesh": plaque_t})

    allm = trimesh.util.concatenate([p["mesh"] for p in out])
    ok = all(p["mesh"].is_watertight for p in out)
    if not ok:
        warnings.append("A part is not watertight; the slicer may need to repair it.")
    overhang = _overhang_pct(allm, allm.bounds[0][2])
    if fmt == "stand" and overhang > 12:
        warnings.append(f"{overhang:.0f}% of the surface overhangs; print it lying flat (Magnet) if it sags.")
    raised_vol = sum(float(p["mesh"].volume) for p in out[:-1])
    report = {
        "watertight": ok,
        "size_mm": [round(float(x), 1) for x in allm.extents],
        "body_volume_cm3": round(raised_vol / 1000, 2),
        "base_volume_cm3": round(float(plaque_t.volume) / 1000, 2),
        "faces": int(sum(len(p["mesh"].faces) for p in out)),
        "remesh_mm": round(pitch, 2),
        "min_feature_mm": float(lp["min_stroke_mm"]),
        "thin_removed_pct": 0.0,
        "overhang_pct": round(overhang, 1),
        "warnings": warnings,
        "format": fmt,
        "logo": True,
        "parts": [{"id": p["id"], "label": p["label"], "volume_cm3": round(float(p["mesh"].volume) / 1000, 2)} for p in out],
    }
    say(1.0, "Ready to print")
    return {"parts": out, "report": report}


def preview_mesh(data: dict[str, Any]) -> trimesh.Trimesh:
    """A quick 3D look for the Shape step: the raised parts on a plaque, glTF y-up, ~50 mm tall."""
    res = build(data, {"format": "magnet", "logo": {"width_mm": 60, "min_stroke_mm": 0.6}, "geometry": {"finish": "soft"}})
    m = trimesh.util.concatenate([p["mesh"] for p in res["parts"]])
    m.apply_transform(trimesh.transformations.rotation_matrix(-math.pi / 2, [1, 0, 0]))  # z-up → y-up
    return m
