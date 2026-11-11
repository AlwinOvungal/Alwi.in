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


def split_sheet(sheet: Path, out_dir: Path) -> dict[str, Path]:
    """Cut a front · side · back sheet into front.png, left.png and back.png (+ masks) in out_dir."""
    img = Image.open(sheet)
    img.load()
    out_dir.mkdir(parents=True, exist_ok=True)
    pad = max(4, img.width // 100)
    out = {}
    for name, (a, b) in zip(VIEW_NAMES, find_columns(img)):
        crop = img.crop((max(0, a - pad), 0, min(img.width, b + pad + 1), img.height))
        if crop.mode not in ("RGB", "RGBA"):
            crop = crop.convert("RGBA")
        if crop.mode == "RGBA":  # flatten onto white so the cut-out sees a plain background
            flat = Image.new("RGB", crop.size, (255, 255, 255))
            flat.paste(crop, (0, 0), crop)
            crop = flat
        tmp = out_dir / f".{name}_crop.png"
        crop.save(tmp)
        ingest.cutout(tmp, out_dir / f"{name}.png")
        tmp.unlink(missing_ok=True)
        out[name] = out_dir / f"{name}.png"
    return out


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
