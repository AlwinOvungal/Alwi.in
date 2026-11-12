"""Three views of one object (front, side, back), the way a turnaround sheet shows them.

Cloud image models draw the three views side by side on one sheet; this splits the sheet into
one picture per view, each cut out and centred on white like every other picture in Form Studio.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from ..engines.base import VIEW_NAMES
from . import ingest


def _foreground(img: Image.Image) -> np.ndarray:
    """Where the objects are: the alpha channel if the sheet has a transparent background, else not-near-white."""
    if img.mode in ("RGBA", "LA") or "transparency" in img.info:
        a = np.asarray(img.convert("RGBA").getchannel("A"))
        if a.min() < 250:
            return a > 24
    rgb = np.asarray(img.convert("RGB")).astype(np.int16)
    corners = np.concatenate([rgb[:6, :6], rgb[:6, -6:], rgb[-6:, :6], rgb[-6:, -6:]]).reshape(-1, 3)
    bg = np.median(corners, axis=0)
    return np.abs(rgb - bg).max(axis=2) > 28


def find_columns(img: Image.Image, n: int = 3) -> list[tuple[int, int]]:
    """Left/right edges of the n objects on the sheet, left to right. Falls back to equal parts."""
    w = img.width
    cols = _foreground(img).sum(axis=0) > max(2, img.height // 200)
    runs: list[list[int]] = []
    gap_ok = max(2, w // 60)  # small gaps inside one object (between a handle and a mug) don't split it
    for x, on in enumerate(cols):
        if not on:
            continue
        if runs and x - runs[-1][1] <= gap_ok:
            runs[-1][1] = x
        else:
            runs.append([x, x])
    runs = [r for r in runs if r[1] - r[0] >= w // 40]  # crumbs
    if len(runs) > n:  # keep the n widest, in order
        runs = sorted(sorted(runs, key=lambda r: r[1] - r[0], reverse=True)[:n])
    if len(runs) != n:
        step = w / n
        return [(round(i * step), round((i + 1) * step) - 1) for i in range(n)]
    return [(a, b) for a, b in runs]


def _cut_panel(img: Image.Image, span: tuple[int, int], dst: Path) -> None:
    """One panel of a sheet → a normal Form Studio cut-out (centred on white, with its mask)."""
    pad = max(4, img.width // 100)
    a, b = span
    crop = img.crop((max(0, a - pad), 0, min(img.width, b + pad + 1), img.height))
    if crop.mode not in ("RGB", "RGBA"):
        crop = crop.convert("RGBA")
    if crop.mode == "RGBA":  # flatten onto white so the cut-out sees a plain background
        flat = Image.new("RGB", crop.size, (255, 255, 255))
        flat.paste(crop, (0, 0), crop)
        crop = flat
    tmp = dst.with_name(f".{dst.stem}_crop.png")
    crop.save(tmp)
    ingest.cutout(tmp, dst)
    tmp.unlink(missing_ok=True)


def split_sheet(sheet: Path, out_dir: Path) -> dict[str, Path]:
    """Cut a front · side · back sheet into front.png, left.png and back.png (+ masks) in out_dir."""
    img = Image.open(sheet)
    img.load()
    out_dir.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, span in zip(VIEW_NAMES, find_columns(img)):
        _cut_panel(img, span, out_dir / f"{name}.png")
        out[name] = out_dir / f"{name}.png"
    return out


def _panel_components(img: Image.Image):
    """The sheet's big pieces, left to right: (label array, [(x0, x1, y0, y1, id), …]) or None.

    Deliberately strict, so a single object (a mug with a handle, a shaded ball) never matches:
    2–4 pieces of similar size, side by side, together holding almost everything on the sheet.
    """
    fg = _foreground(img)
    fg = ingest._despeck(fg * np.uint8(255)) > 0  # labels and callout text are not panels
    from scipy import ndimage

    lab, n = ndimage.label(fg)
    if not fg.any() or n < 2:
        return None
    areas = np.bincount(lab.ravel())
    areas[0] = 0
    big = np.nonzero(areas >= max(1, int(areas.max() * 0.15)))[0]
    if not 2 <= len(big) <= 4:
        return None
    boxes = []
    for i in big:
        ys, xs = np.nonzero(lab == i)
        boxes.append((int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max()), int(i)))
    boxes.sort()
    h = img.height
    heights = [y1 - y0 + 1 for _, _, y0, y1, _ in boxes]
    if max(heights) > 1.7 * min(heights) or min(heights) < h // 6:
        return None
    for (a0, b0, *_), (a1, b1, *_) in zip(boxes, boxes[1:]):  # side by side, not stacked or entangled
        if min(b0, b1) - a1 > 0.25 * min(b0 - a0, b1 - a1):
            return None
    if sum(int(areas[i]) for *_, i in boxes) < 0.8 * int(fg.sum()):
        return None
    return lab, boxes


def panels(img: Image.Image) -> list[tuple[int, int]] | None:
    """The left/right spans of a turnaround or product sheet's panels, or None for one object."""
    found = _panel_components(img)
    return [(x0, x1) for x0, x1, *_ in found[1]] if found else None


def _cut_component(img: Image.Image, lab, box, dst: Path) -> None:
    """One piece of a sheet → a normal cut-out, with any neighbour's overhang wiped to white."""
    x0, x1, y0, y1, i = box
    pad = max(4, img.width // 100)
    l, t = max(0, x0 - pad), max(0, y0 - pad)
    r, b = min(img.width, x1 + pad + 1), min(img.height, y1 + pad + 1)
    crop = img.crop((l, t, r, b)).convert("RGB")
    a = np.asarray(crop).copy()
    others = (lab[t:b, l:r] != i) & (lab[t:b, l:r] != 0)
    a[others] = 255
    tmp = dst.with_name(f".{dst.stem}_crop.png")
    Image.fromarray(a).save(tmp)
    ingest.cutout(tmp, dst)
    tmp.unlink(missing_ok=True)


def split_panels(src: Path, out_dir: Path) -> dict[str, Path] | None:
    """A detected 3-panel sheet → front.png, left.png, back.png (+ masks), by its own pieces."""
    img = Image.open(src)
    img.load()
    found = _panel_components(img)
    if not found or len(found[1]) != 3:
        return None
    lab, boxes = found
    out_dir.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, box in zip(VIEW_NAMES, boxes):
        _cut_component(img, lab, box, out_dir / f"{name}.png")
        out[name] = out_dir / f"{name}.png"
    return out


def panels_of(src: Path) -> list[tuple[int, int]] | None:
    img = Image.open(src)
    img.load()
    return panels(img)


def main_object(src: Path, dst: Path) -> dict | None:
    """If src is a sheet of panels, write its FIRST panel (the front) to dst as a normal cut-out
    (with its mask beside it) and say how many panels there were. None when src is one object."""
    img = Image.open(src)
    img.load()
    found = _panel_components(img)
    if not found:
        return None
    lab, boxes = found
    _cut_component(img, lab, boxes[0], dst)
    return {"panels": len(boxes)}


def save_views(images: dict[str, Image.Image], out_dir: Path) -> dict[str, Path]:
    """Separate pictures (e.g. from the local view maker) → the same cut-out, centred files."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out = {}
    for name in VIEW_NAMES:
        tmp = out_dir / f".{name}_raw.png"
        images[name].convert("RGB").save(tmp)
        ingest.cutout(tmp, out_dir / f"{name}.png")
        tmp.unlink(missing_ok=True)
        out[name] = out_dir / f"{name}.png"
    return out
