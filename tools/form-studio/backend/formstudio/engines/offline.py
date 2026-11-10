"""Offline engines: no AI, no downloads, instant. Good for testing the flow,
and genuinely useful for simple shapes:

- Palette preview: recolours the cutout in the style palette (a quick look, not a restyle)
- Lathe: spins the silhouette around its centre line (cups, vases, bottles, lamps)
- Extrude: pushes the silhouette into a thick rounded slab (logos, flat objects, 2D art)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from scipy import ndimage
from skimage import measure

from .base import EngineContext, Progress, ReconstructInput, StylizeInput


def _hex(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


class PalettePreview:
    id = "preview"
    label = "Palette preview (offline)"
    kind = "offline"
    note = "Instant. Recolours your photo in the style palette, no AI restyle."

    def available(self, ctx: EngineContext) -> tuple[bool, str]:
        return True, ""

    def stylize(self, inp: StylizeInput, ctx: EngineContext, progress: Progress) -> list[dict]:
        img = Image.open(inp.cutout).convert("L")
        mask = Image.open(inp.mask).convert("L")
        out = []
        body = _hex(inp.style.palette["body"]["hex"])
        for i, seed in enumerate(inp.seeds):
            progress(i / len(inp.seeds), f"Candidate {i + 1} of {len(inp.seeds)}")
            rng = np.random.default_rng(seed)
            blur = 2 + int(rng.integers(0, 6))
            shade = ImageOps.autocontrast(img.filter(ImageFilter.GaussianBlur(blur)))
            shade = ImageEnhance.Contrast(shade).enhance(0.45 + rng.random() * 0.2)
            tinted = ImageOps.colorize(shade, black=tuple(int(c * 0.45) for c in body), white=tuple(min(255, int(c * 1.6)) for c in body))
            canvas = Image.new("RGB", img.size, (255, 255, 255))
            canvas.paste(tinted, (0, 0), mask.filter(ImageFilter.GaussianBlur(1.5)))
            name = f"candidate_{i + 1}.png"
            canvas.save(inp.out_dir / name)
            out.append({"file": name, "seed": seed})
        progress(1.0, "Done")
        return out


def _mask_array(path: Path, size: int = 160) -> np.ndarray:
    m = Image.open(path).convert("L")
    bbox = m.point(lambda v: 255 if v > 64 else 0).getbbox()
    if bbox:
        m = m.crop(bbox)
    scale = size / max(m.size)
    m = m.resize((max(1, round(m.width * scale)), max(1, round(m.height * scale))), Image.LANCZOS)
    a = np.asarray(m) > 127
    a = ndimage.binary_fill_holes(a)
    return a  # [row (top→bottom), col]


def _volume_to_glb(vol: np.ndarray, out: Path) -> None:
    """vol indexed [x, y(depth), z(up)] → GLB in glTF y-up convention."""
    v = np.pad(vol.astype(np.float32), 2)
    v = ndimage.gaussian_filter(v, 0.8)
    verts, faces, _, _ = measure.marching_cubes(v, 0.5)
    m = trimesh.Trimesh(verts, faces)
    if m.volume < 0:
        m.invert()
    m.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))  # z-up → y-up
    m.export(out)


class Lathe:
    id = "lathe"
    label = "Lathe from silhouette (offline)"
    kind = "offline"
    note = "Instant. Spins the outline around its centre: great for cups, vases, bottles."

    def available(self, ctx: EngineContext) -> tuple[bool, str]:
        return True, ""

    def reconstruct(self, inp: ReconstructInput, ctx: EngineContext, progress: Progress) -> dict:
        progress(0.2, "Reading the silhouette")
        a = _mask_array(inp.mask)
        rows, cols = a.shape
        centre = np.nonzero(a.any(0))[0].mean()
        radius = np.zeros(rows)
        for r in range(rows):
            xs = np.nonzero(a[r])[0]
            if len(xs):
                radius[r] = max(abs(xs.max() - centre), abs(xs.min() - centre)) * 0.92
        R = int(radius.max()) + 2
        g = np.arange(-R, R + 1)
        dist = np.sqrt(g[:, None] ** 2 + g[None, :] ** 2)
        progress(0.6, "Spinning")
        vol = np.zeros((2 * R + 1, 2 * R + 1, rows), dtype=bool)
        for r in range(rows):
            vol[:, :, rows - 1 - r] = dist <= radius[r]
        _volume_to_glb(vol, inp.out)
        progress(1.0, "Done")
        return {"method": "lathe"}


class Extrude:
    id = "extrude"
    label = "Extrude silhouette (offline)"
    kind = "offline"
    note = "Instant. Turns the outline into a thick, rounded slab: good for 2D art and flat things."

    def available(self, ctx: EngineContext) -> tuple[bool, str]:
        return True, ""

    def reconstruct(self, inp: ReconstructInput, ctx: EngineContext, progress: Progress) -> dict:
        progress(0.3, "Reading the silhouette")
        a = _mask_array(inp.mask)
        rows, cols = a.shape
        depth = max(6, int(min(rows, cols) * 0.28))
        # puffy slab: thickness follows distance from the edge, like an inflated cushion
        edt = ndimage.distance_transform_edt(a)
        half = np.minimum(depth / 2, np.sqrt(np.maximum(edt, 0) * depth / 2) + 1) * a
        dz = np.arange(depth) - (depth - 1) / 2
        vol = np.abs(dz)[None, :, None] <= half.T[:, None, ::-1]  # [x, y(depth), z(up)]
        progress(0.7, "Building the slab")
        _volume_to_glb(vol, inp.out)
        progress(1.0, "Done")
        return {"method": "extrude"}
