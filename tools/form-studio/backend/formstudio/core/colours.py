"""Pick a style's two print colours from its reference pictures (free, no AI).

The object colour is the strongest frequent colour; the base is the lightest frequent
colour that's clearly different from it. Near-white and near-black backgrounds are ignored.
"""
from __future__ import annotations

import colorsys
from pathlib import Path

from PIL import Image

NAMES: dict[str, tuple[int, int, int]] = {
    "Olive": (94, 107, 46), "Moss": (110, 130, 60), "Forest": (34, 90, 50), "Sage": (156, 175, 136),
    "Mint": (170, 220, 190), "Teal": (30, 120, 120), "Sky Blue": (110, 170, 230), "Cobalt": (40, 80, 200),
    "Navy": (25, 40, 90), "Lavender": (180, 160, 220), "Purple": (110, 60, 150), "Plum": (100, 40, 80),
    "Pink": (240, 160, 190), "Rose": (210, 90, 120), "Red": (200, 40, 40), "Tomato": (230, 85, 60),
    "Terracotta": (200, 100, 60), "Orange": (240, 140, 40), "Peach": (242, 170, 140), "Mustard": (210, 170, 50),
    "Yellow": (245, 215, 70), "Sand": (225, 205, 170), "Tan": (190, 150, 110), "Brown": (110, 70, 40),
    "Chocolate": (70, 40, 25), "Cream": (245, 238, 220), "Bone White": (242, 240, 234), "Light Grey": (200, 200, 200),
    "Grey": (128, 128, 128), "Charcoal": (50, 50, 50), "Black": (20, 20, 20), "White": (250, 250, 250),
}


def name_for(rgb: tuple[int, int, int]) -> str:
    return min(NAMES, key=lambda n: sum((a - b) ** 2 for a, b in zip(NAMES[n], rgb)))


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _dist(a, b) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _usable(rgb) -> bool:
    lo, hi = min(rgb), max(rgb)
    return not (lo > 232) and not (hi < 28)  # skip paper-white and deep-black backgrounds


def suggest(images: list[Path | Image.Image]) -> dict[str, dict[str, str]]:
    pixels: list[tuple[int, int, int]] = []
    for item in images:
        img = item if isinstance(item, Image.Image) else Image.open(item)
        img = img.convert("RGB")
        img.thumbnail((160, 160))
        pixels += [p for p in img.getdata() if _usable(p)]
    if not pixels:
        return {"body": {"name": "Olive", "hex": "#5E6B2E"}, "base": {"name": "Bone White", "hex": "#F2F0EA"}}
    strip = Image.new("RGB", (len(pixels), 1))
    strip.putdata(pixels)
    q = strip.quantize(colors=8, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()[: 8 * 3]
    counts = sorted(q.getcolors() or [], reverse=True)
    total = sum(c for c, _ in counts) or 1
    colours = [(c / total, tuple(pal[i * 3 : i * 3 + 3])) for c, i in counts]

    # the object colour: the hue family with the most saturated pixels, averaged
    bins: dict[int, list[tuple[int, int, int]]] = {}
    weight: dict[int, float] = {}
    for p in pixels:
        h, sat, v = colorsys.rgb_to_hsv(*(x / 255 for x in p))
        if sat < 0.18 or v < 0.15:
            continue
        b = int(h * 18) % 18
        bins.setdefault(b, []).append(p)
        weight[b] = weight.get(b, 0.0) + sat**4  # strongly favour vivid colours over big dull areas
    if weight:
        best = bins[max(weight, key=weight.get)]
        body = tuple(sorted(c[i] for c in best)[len(best) // 2] for i in range(3))  # median: robust to edges
    else:  # a grey-only picture
        body = max(colours, key=lambda c: c[0])[1]
    candidates = [(share, rgb) for share, rgb in colours if share > 0.04 and _dist(rgb, body) > 70]
    if candidates:
        base = max(candidates, key=lambda c: sum(c[1]) * (0.5 + c[0]))[1]
        if sum(base) < sum(body):  # the base is the lighter of the two
            base = tuple(min(255, int(x + (255 - x) * 0.75)) for x in body)
    else:
        base = tuple(min(255, int(x + (255 - x) * 0.85)) for x in body)
    return {"body": {"name": name_for(body), "hex": _hex(body)}, "base": {"name": name_for(base), "hex": _hex(base)}}
