"""Step 1: photo → background removed, cropped to the object, centred on a white square."""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

log = logging.getLogger(__name__)

SIZE = 1024
PADDING = 0.12  # fraction of the square left empty around the object


@lru_cache(maxsize=1)
def _rembg_session():
    if os.environ.get("FORMSTUDIO_MATTING") == "threshold":  # tests: skip the 1 GB model
        return None
    try:
        from rembg import new_session  # type: ignore
    except ImportError:
        return None
    # isnet: ~170 MB and light on memory; birefnet is sharper but ~1 GB
    for model in (os.environ.get("FORMSTUDIO_MATTING_MODEL", "isnet-general-use"), "u2net"):
        try:
            return new_session(model)
        except Exception as e:  # model download or onnx issue → try the next
            log.warning("rembg model %s unavailable: %s", model, e)
    return None


def _alpha_from_plain_background(img: Image.Image) -> Image.Image:
    """Fallback when rembg isn't installed: treat colours close to the corners as background."""
    rgb = np.asarray(img.convert("RGB")).astype(np.float32)
    corners = np.concatenate([rgb[:8, :8], rgb[:8, -8:], rgb[-8:, :8], rgb[-8:, -8:]]).reshape(-1, 3)
    bg = np.median(corners, axis=0)
    dist = np.linalg.norm(rgb - bg, axis=2)
    alpha = (np.clip((dist - 18) / 30, 0, 1) * 255).astype(np.uint8)
    return Image.fromarray(alpha).filter(ImageFilter.MedianFilter(5))


def remove_background(img: Image.Image) -> Image.Image:
    """Returns RGBA with the background transparent."""
    session = _rembg_session()
    if session is not None:
        from rembg import remove  # type: ignore

        return remove(img.convert("RGB"), session=session).convert("RGBA")
    out = img.convert("RGBA")
    out.putalpha(_alpha_from_plain_background(img))
    return out


def _despeck(alpha: "np.ndarray") -> "np.ndarray":
    """Drop tiny stray bits — labels, dimension text, dust — so they never reach the print."""
    from scipy import ndimage

    labels, n = ndimage.label(alpha > 24)
    if n <= 1:
        return alpha
    areas = np.bincount(labels.ravel())[1:]
    keep = areas >= max(16, int(areas.max() * 0.02))
    if keep.all():
        return alpha
    out = alpha.copy()
    out[~np.isin(labels, np.nonzero(keep)[0] + 1)] = 0
    return out


def cutout(src: Path, dst: Path, size: int = SIZE) -> dict:
    img = ImageOps.exif_transpose(Image.open(src))
    rgba = remove_background(img)
    alpha = _despeck(np.asarray(rgba.getchannel("A")))
    rgba.putalpha(Image.fromarray(alpha))
    ys, xs = np.nonzero(alpha > 24)
    if len(xs) == 0:  # nothing found: keep the whole frame
        box = (0, 0, rgba.width, rgba.height)
    else:
        box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    obj = rgba.crop(box)
    inner = int(size * (1 - 2 * PADDING))
    scale = inner / max(obj.width, obj.height)
    obj = obj.resize((max(1, round(obj.width * scale)), max(1, round(obj.height * scale))), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    canvas.alpha_composite(obj, ((size - obj.width) // 2, (size - obj.height) // 2))
    canvas.convert("RGB").save(dst)
    # keep the mask too: stylizers and reconstructors use it
    mask = Image.new("L", (size, size), 0)
    mask.paste(obj.getchannel("A"), ((size - obj.width) // 2, (size - obj.height) // 2))
    mask.save(dst.with_name(dst.stem + "_mask.png"))
    return {"bbox": box, "source_size": [img.width, img.height], "matting": "rembg" if _rembg_session() else "threshold"}
