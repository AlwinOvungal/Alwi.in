"""Stage 1: remove the background, crop to the object, pad to a white square."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Literal

import numpy as np
from PIL import Image

Matte = Literal["rembg", "threshold"]


def remove_background_rembg(img: Image.Image) -> Image.Image:
    """BiRefNet via rembg. Weights download on first use."""
    from rembg import new_session, remove

    session = new_session("birefnet-general")
    return remove(img.convert("RGBA"), session=session)


def remove_background_threshold(img: Image.Image) -> Image.Image:
    """Cheap fallback for plain backgrounds: pixels close to the border colour
    become transparent. Used by tests and the stub backend."""
    rgb = np.asarray(img.convert("RGB")).astype(np.float32)
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    bg = np.median(border, axis=0)
    dist = np.linalg.norm(rgb - bg, axis=2)
    alpha = np.where(dist > 40, 255, 0).astype(np.uint8)
    out = np.dstack([rgb.astype(np.uint8), alpha])
    return Image.fromarray(out, "RGBA")


def crop_to_square(rgba: Image.Image, size: int = 1024, margin: float = 0.08) -> Image.Image:
    """Crop to the opaque bounding box (plus margin), centre it on a square
    white canvas, and resize to size x size."""
    alpha = np.asarray(rgba.getchannel("A"))
    ys, xs = np.where(alpha > 16)
    if xs.size == 0:
        raise ValueError("No object found after background removal")
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    w, h = x1 - x0, y1 - y0
    pad = int(max(w, h) * margin)
    side = max(w, h) + 2 * pad
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    box = (int(cx - side / 2), int(cy - side / 2), int(cx + side / 2), int(cy + side / 2))
    crop = rgba.crop(box)
    canvas = Image.new("RGBA", crop.size, (255, 255, 255, 255))
    canvas.alpha_composite(crop)
    return canvas.convert("RGB").resize((size, size), Image.Resampling.LANCZOS)


def ingest(src: Path, dst: Path, size: int = 1024, matte: Matte = "rembg") -> Path:
    remover: Callable[[Image.Image], Image.Image] = {
        "rembg": remove_background_rembg,
        "threshold": remove_background_threshold,
    }[matte]
    img = Image.open(src)
    img = img.convert("RGBA")
    cut = remover(img)
    crop_to_square(cut, size=size).save(dst)
    return dst
