"""Design advice for a look, in plain words, for the Style step's "Refine this look" chat.

Each suggestion explains what would print badly at the chosen size and type, and carries a ready
message for the restyle model ("make the thin parts thicker…"). The checks are measurements of the
picture itself at print scale, not guesses.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy import ndimage

AMS_SLOTS = 4


def _mask(rgb: np.ndarray) -> np.ndarray:
    m = rgb.min(axis=2) < 240  # options come on white
    m = ndimage.binary_opening(m, iterations=1)
    lab, n = ndimage.label(m)
    if n > 1:
        sizes = ndimage.sum(m, lab, range(1, n + 1))
        m = lab == (1 + int(np.argmax(sizes)))
    return ndimage.binary_fill_holes(m)


def advise(image: Path, params: dict[str, Any], palette_max: int | None = None) -> list[dict[str, Any]]:
    """[{id, kind: fix|idea, text, message}] for the picture at `image`, made as `params` describes."""
    from . import logo, relief

    img = Image.open(image).convert("RGB")
    rgb = np.asarray(img)
    mask = _mask(rgb)
    if mask.sum() < 100:
        return []
    geo = params.get("geometry") or {}
    height = float(geo.get("height_mm", 35))
    min_feat = float(geo.get("min_feature_mm", 1.6))
    flat = bool((params.get("flat_back") or {}).get("enabled"))
    fmt = params.get("format", "stand")
    box, mm_px = relief.fit(mask, height)
    out: list[dict[str, Any]] = []

    # thin parts: what an opening at the minimum printable width would remove
    _, removed = relief.outline(mask, box, mm_px, min_feat)
    if removed >= 0.01:
        out.append({"id": "thin", "kind": "fix",
                    "text": f"Some parts are thinner than {min_feat:g} mm at {height:g} mm tall ({removed:.0%} of the "
                            "outline): they'd snap or be trimmed.",
                    "message": "Make the thin parts (tips, branches, legs, edges) thicker and chunkier. "
                               "Keep everything else the same."})

    stats: dict[str, Any] = {}
    _, cols = logo.regions(img, mask, palette_max, mm_per_px=mm_px, stats=stats)
    if len(cols) > AMS_SLOTS:
        out.append({"id": "colours", "kind": "fix",
                    "text": f"{len(cols)} colours: more than one AMS unit holds ({AMS_SLOTS}). Fewer colours also "
                            "print faster and cheaper.",
                    "message": f"Use at most {AMS_SLOTS} flat colours. Keep the same overall look and shape."})
    match = stats.get("colour_match", 1.0)
    if match < 0.97:
        out.append({"id": "details", "kind": "fix",
                    "text": f"Small details ({100 * (1 - match):.0f}% of the picture) are under 1.2 mm: too small to "
                            "print in their own colour.",
                    "message": "Make small details like eyes, buttons and patterns bigger and bolder, with fewer "
                               "of them. Keep everything else the same."})

    if flat:
        what = {"magnet": "A magnet", "keychain": "A keychain", "ornament": "An ornament"}.get(fmt, "A flat print")
        out.append({"id": "front", "kind": "idea",
                    "text": f"{what} is made from the picture as you see it: a straight-on front view with clear, "
                            "flat colour areas prints best.",
                    "message": "Show it straight from the front with no perspective or tilt, in clear flat colour "
                               "areas. Keep the same colours and shape."})
    else:
        out.append({"id": "base", "kind": "idea",
                    "text": "A figurine stands best on wide feet or a flat bottom; thin legs or a top-heavy shape tip over.",
                    "message": "Give it a wider, flat bottom so it stands steadily. Keep everything else the same."})
    return out
