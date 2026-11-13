"""Offline engines: no AI, no downloads, instant. Good for testing the flow,
and genuinely useful for simple shapes:

- Quick preview: your photo simplified in its own colours (a quick look, not a restyle)
- Lathe: spins the silhouette around its centre line (cups, vases, bottles, lamps)
- Extrude: pushes the silhouette into a thick rounded slab (logos, flat objects, 2D art)
- Picture relief: magnets, keychains, ornaments made from the picture itself (exact outline, its colours)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from scipy import ndimage
from skimage import measure

from .base import EngineContext, Progress, ReconstructInput, StylizeInput
from ..core import logo as logo_mod


def _hex(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


class PalettePreview:
    id = "preview"
    label = "Quick preview (offline)"
    kind = "offline"
    note = "Instant. A simplified, softened look at your photo in its own colours, no AI restyle."

    def available(self, ctx: EngineContext) -> tuple[bool, str]:
        return True, ""

    def stylize(self, inp: StylizeInput, ctx: EngineContext, progress: Progress) -> list[dict]:
        img = Image.open(inp.cutout).convert("RGB")
        mask = Image.open(inp.mask).convert("L")
        out = []
        for i, seed in enumerate(inp.seeds):
            progress(i / len(inp.seeds), f"Candidate {i + 1} of {len(inp.seeds)}")
            rng = np.random.default_rng(seed)
            # the object's own colours, simplified: fewer tones, softened detail (styles never set colours)
            soft = img.filter(ImageFilter.MedianFilter(3 + 2 * int(rng.integers(0, 3))))
            soft = ImageOps.posterize(soft, 3 + int(rng.integers(0, 2)))
            soft = ImageEnhance.Contrast(soft).enhance(0.9 + rng.random() * 0.15)
            canvas = Image.new("RGB", img.size, (255, 255, 255))
            canvas.paste(soft, (0, 0), mask.filter(ImageFilter.GaussianBlur(1.5)))
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


class PictureRelief:
    id = "picture"
    label = "From the picture (instant)"
    kind = "offline"
    note = ("Instant. For magnets, keychains and ornaments: the picture becomes a relief with its exact "
            "outline and its own colours. No AI 3D model needed.")
    flat_only = True

    def available(self, ctx: EngineContext) -> tuple[bool, str]:
        return True, ""

    def reconstruct(self, inp: ReconstructInput, ctx: EngineContext, progress: Progress) -> dict:
        from ..core import relief
        from ..core.printify import to_trimesh

        progress(0.2, "Reading the picture")
        img = Image.open(inp.image).convert("RGB")
        mask = np.asarray(Image.open(inp.mask).convert("L").resize(img.size)) > 24
        progress(0.5, "Pressing the picture into a relief")
        labels, _ = logo_mod.regions(img, mask, None)
        body, _ = relief.build(np.asarray(img), mask, labels, 50.0, 10.0, 1.0, {"soften_mm": 0.6})
        # lying flat it's x right, y up, z towards you: already the y-up, front-facing frame of a GLB
        to_trimesh(body).export(inp.out, file_type="glb")
        progress(1.0, "Done")
        return {"method": "picture"}


def _logo_source(inp) -> Path:
    src = inp.source if inp.source and Path(inp.source).exists() else None
    if src is None:
        raise RuntimeError("This object's original picture is missing. Create it again from the logo file.")
    return Path(src)


class LogoPreview:
    """Logos and text: a picture of the exact artwork as raised clay on a plaque. No AI, so no misspelt letters."""
    id = "logo"
    label = "Exact logo (from your artwork)"
    kind = "offline"
    logo_only = True
    note = "Instant and free. Keeps every letter and shape exactly; the logo's own colours on a plaque."

    def available(self, ctx: EngineContext) -> tuple[bool, str]:
        return True, ""

    def stylize(self, inp: StylizeInput, ctx: EngineContext, progress: Progress) -> list[dict]:
        progress(0.2, "Reading the logo's colours and shapes")
        data = logo_mod.layers_cached(_logo_source(inp))
        progress(0.6, "Drawing it in clay")
        plaque = "#F2F0EA"  # the preview's plaque; its print colour is chosen in the Print step
        img = logo_mod.preview(data, logo_mod.palette(data), plaque)
        name = "candidate_1.png"
        img.save(inp.out_dir / name)
        progress(1.0, "Done")
        return [{"file": name, "seed": inp.seeds[0] if inp.seeds else 0}]


class LogoShape:
    """Logos and text: the 3D shape straight from the artwork (each colour a raised part on a plaque)."""
    id = "logo"
    label = "From your artwork (exact)"
    kind = "offline"
    logo_only = True
    note = "Instant and free. Every letter and shape exactly as drawn; each logo colour is its own part."

    def available(self, ctx: EngineContext) -> tuple[bool, str]:
        return True, ""

    def reconstruct(self, inp: ReconstructInput, ctx: EngineContext, progress: Progress) -> dict:
        progress(0.2, "Reading the logo's colours and shapes")
        data = logo_mod.layers_cached(_logo_source(inp))
        progress(0.5, "Raising the shapes")
        logo_mod.preview_mesh(data).export(inp.out)
        progress(1.0, "Done")
        return {"method": "logo", "colours": logo_mod.palette(data)}
