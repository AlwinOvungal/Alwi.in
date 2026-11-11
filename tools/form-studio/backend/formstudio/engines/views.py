"""View makers: the chosen picture → front, side and back of the same object.

Multi-view 3D engines (Meshy, TRELLIS, Hunyuan3D-2 multi-view) build a far more faithful shape
when they can see the sides and back instead of guessing them.
"""
from __future__ import annotations

import base64
import logging
import sys
from pathlib import Path

from PIL import Image

from .. import paths
from ..core import views as views_mod
from . import openai_image
from .base import VIEW_NAMES, EngineContext, EngineError, Progress, ViewInput, missing
from .cloud import _data_uri, _endpoint, _FalEngine, _scaled, make_client

log = logging.getLogger(__name__)

SHEET_PROMPT = (
    "Make a turnaround sheet of exactly this {subject}: three views of the same object side by side, from left to "
    "right the FRONT view, the LEFT SIDE view (turned 90 degrees) and the BACK view (turned 180 degrees). Keep the "
    "same object, proportions, colours, materials and finish as in the picture, at the same size and height in all "
    "three, each fully in frame with clear space between them. Plain background, even soft light, no shadows on the "
    "ground. No text, labels, arrows, measurements or extra objects."
)


def _on_transparent(inp: ViewInput, out: Path) -> Path:
    img = Image.open(inp.image).convert("RGBA")
    img.putalpha(Image.open(inp.mask).convert("L"))
    img.save(out)
    return out


class OpenAIViews:
    id = "openai-views"
    label = "ChatGPT images"
    kind = "cloud"
    note = "Paid, about 5–20¢ per sheet. Best at keeping it the same object in all three views."

    def available(self, ctx: EngineContext) -> tuple[bool, str]:
        ok = bool(ctx.secrets.get("openai"))
        return ok, "" if ok else "Add an OpenAI API key in Settings."

    def make_views(self, inp: ViewInput, ctx: EngineContext, progress: Progress) -> dict[str, Path]:
        key = ctx.secrets.get("openai")
        if not key:
            raise PermissionError("Add your OpenAI API key in Settings.")
        cfg = ctx.settings.get("openai") or {}
        src = _on_transparent(inp, inp.out_dir / ".source.png")
        body = {
            "model": cfg.get("model") or openai_image.DEFAULT_MODEL,
            "prompt": SHEET_PROMPT.format(subject=inp.subject or "object"),
            "images": [{"image_url": _data_uri(src)}],
            "n": 1,
            "size": "1536x1024",
            "quality": cfg.get("quality") or "medium",
            "background": "transparent",
            "output_format": "png",
        }
        progress(None, "OpenAI is drawing the front, side and back")
        data = openai_image.post_images(body, key, ctx, progress)
        src.unlink(missing_ok=True)
        if not data:
            raise RuntimeError("OpenAI returned no picture. Try again.")
        sheet = inp.out_dir / "sheet.png"
        sheet.write_bytes(base64.b64decode(data[0]["b64_json"]))
        progress(0.9, "Cutting the sheet into three views")
        return views_mod.split_sheet(sheet, inp.out_dir)


class FalViews(_FalEngine):
    id = "fal-views"
    label = "Nano Banana (fal.ai)"
    note = "Paid, about 4¢ per sheet. Keeps the object consistent across views."
    endpoint_of = "fal-nano-banana"

    def make_views(self, inp: ViewInput, ctx: EngineContext, progress: Progress) -> dict[str, Path]:
        client = make_client(ctx)
        endpoint = _endpoint(ctx, self.endpoint_of)
        src = _on_transparent(inp, inp.out_dir / ".source.png")
        res = client.run(endpoint, {
            "prompt": SHEET_PROMPT.format(subject=inp.subject or "object"),
            "image_urls": [_data_uri(src)],
            "seed": int(inp.seed),
            "num_images": 1,
            "output_format": "png",
            "aspect_ratio": "3:2",
        }, _scaled(progress, 0.05, 0.85))
        src.unlink(missing_ok=True)
        sheet = inp.out_dir / "sheet.png"
        client.download(res["images"][0]["url"], sheet)
        progress(0.9, "Cutting the sheet into three views")
        return views_mod.split_sheet(sheet, inp.out_dir)


def mvadapter_dir() -> Path:
    return paths.home() / "vendor" / "MV-Adapter"


def _mvadapter_on_path() -> None:
    """MV-Adapter is a cloned folder (setup-mac.sh --with-views), not an installed package."""
    if str(mvadapter_dir()) not in sys.path:
        sys.path.insert(0, str(mvadapter_dir()))


def _mv_geometry():
    """MV-Adapter's geometry helpers, loaded from their file on its own.

    Importing `mvadapter.utils` would also load its mesh tools, which need nvdiffrast (NVIDIA
    graphics cards only) plus OpenCV and matplotlib, so it can never load on a Mac. The geometry
    file itself only needs numpy and torch.
    """
    import importlib.util

    name = "formstudio_mvadapter_geometry"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, mvadapter_dir() / "mvadapter" / "utils" / "geometry.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules[name] = mod
    return mod


def camera_to_world(azimuth_deg: list[float], distance: float = 1.8, device: str = "cpu"):
    """Camera-to-world matrices for cameras circling the object at eye level.

    Adapted from MV-Adapter's get_c2w (mvadapter/utils/mesh_utils/camera.py, Apache-2.0,
    github.com/huanngzh/MV-Adapter); that file can't be imported on a Mac (see _mv_geometry).
    """
    import math

    import torch
    import torch.nn.functional as F

    n = len(azimuth_deg)
    az = torch.tensor(azimuth_deg, dtype=torch.float32, device=device) * math.pi / 180
    d = torch.full((n,), float(distance), dtype=torch.float32, device=device)
    pos = torch.stack([d * torch.cos(az), d * torch.sin(az), torch.zeros_like(d)], dim=-1)  # elevation 0
    up = torch.tensor([0, 0, 1], dtype=torch.float32, device=device)[None, :].repeat(n, 1)
    lookat = F.normalize(-pos, dim=-1)
    right = F.normalize(torch.cross(lookat, up, dim=-1), dim=-1)
    up = F.normalize(torch.cross(right, lookat, dim=-1), dim=-1)
    c2w3x4 = torch.cat([torch.stack([right, up, -lookat], dim=-1), pos[:, :, None]], dim=-1)
    c2w = torch.cat([c2w3x4, torch.zeros_like(c2w3x4[:, :1])], dim=1)
    c2w[:, 3, 3] = 1.0
    return c2w


class LocalViews:
    """MV-Adapter (image → multi-view) on the SDXL you already have. Experimental: needs `setup-mac.sh --with-views`."""

    id = "local-views"
    label = "Free on this Mac (MV-Adapter)"
    kind = "local"
    note = "Free. Draws 6 turns of the object with SDXL and keeps front, side and back. Slow (several minutes); needs ~12 GB of memory."
    AZIMUTHS = [0, 45, 90, 180, 270, 315]  # the six turns MV-Adapter was trained on
    KEEP = {"front": 0, "left": 2, "back": 3}  # 0°, 90°, 180°

    def available(self, ctx: EngineContext) -> tuple[bool, str]:
        why = missing("torch", "diffusers", "transformers", "einops")
        if why:
            return False, why
        if not (mvadapter_dir() / "mvadapter").is_dir():
            return False, "Not installed: run ./scripts/setup-mac.sh --with-views (about 3.6 GB)."
        return True, ""

    def _pipe(self, ctx: EngineContext):
        from .local import _cached

        _mvadapter_on_path()

        def make():
            import torch
            from diffusers import AutoencoderKL
            from mvadapter.pipelines.pipeline_mvadapter_i2mv_sdxl import MVAdapterI2MVSDXLPipeline  # type: ignore
            from mvadapter.schedulers.scheduling_shift_snr import ShiftSNRScheduler  # type: ignore

            dtype = torch.float16 if ctx.device in ("mps", "cuda") else torch.float32
            pipe = MVAdapterI2MVSDXLPipeline.from_pretrained(
                "stabilityai/stable-diffusion-xl-base-1.0", vae=AutoencoderKL.from_pretrained("madebyollin/sdxl-vae-fp16-fix"),
            )
            pipe.scheduler = ShiftSNRScheduler.from_scheduler(pipe.scheduler, shift_mode="interpolated", shift_scale=8.0)
            pipe.init_custom_adapter(num_views=len(self.AZIMUTHS))
            pipe.load_custom_adapter("huanngzh/mv-adapter", weight_name="mvadapter_i2mv_sdxl.safetensors")
            pipe.to(device=ctx.device, dtype=dtype)
            pipe.cond_encoder.to(device=ctx.device, dtype=dtype)
            pipe.enable_vae_slicing()
            return pipe

        return _cached(f"mvadapter:{ctx.device}", make)

    def make_views(self, inp: ViewInput, ctx: EngineContext, progress: Progress) -> dict[str, Path]:
        from .local import _ensure_models, _retry_on_oom

        _ensure_models("sdxl", progress, 0.0, 0.1)
        _ensure_models("mvadapter", progress, 0.1, 0.3)
        try:
            return _retry_on_oom(lambda: self._make(inp, ctx, progress))
        except ModuleNotFoundError as e:
            raise EngineError(
                f"The free view maker is missing a piece it needs ({e.name}).",
                str(e),
                steps=["In Terminal, run ./scripts/setup-mac.sh --with-views again.",
                       "Or use another view maker: Nano Banana (fal.ai key) or ChatGPT images."],
            ) from e

    def _make(self, inp: ViewInput, ctx: EngineContext, progress: Progress) -> dict[str, Path]:
        import torch

        _mvadapter_on_path()
        progress(None, "Loading the view model into memory (only the first time)")
        pipe = self._pipe(ctx)
        n, size, steps = len(self.AZIMUTHS), 768, 40
        # the same cameras as MV-Adapter's own example: eye level, 1.8 away, orthographic scale 1.1
        c2w = camera_to_world([x - 90 for x in self.AZIMUTHS], 1.8, ctx.device)
        plucker = _mv_geometry().get_plucker_embeds_from_cameras_ortho(c2w, [1.1] * n, size)
        control = ((plucker + 1.0) / 2.0).clamp(0, 1)
        # the reference: the object centred at 90% on mid-grey, as MV-Adapter expects
        rgba = Image.open(inp.image).convert("RGBA")
        rgba.putalpha(Image.open(inp.mask).convert("L"))
        box = rgba.getbbox() or (0, 0, rgba.width, rgba.height)
        obj = rgba.crop(box)
        k = size * 0.9 / max(obj.size)
        obj = obj.resize((max(1, int(obj.width * k)), max(1, int(obj.height * k))), Image.LANCZOS)
        ref = Image.new("RGBA", (size, size), (128, 128, 128, 255))
        ref.alpha_composite(obj, ((size - obj.width) // 2, (size - obj.height) // 2))

        def cb(p, step, t, kw):
            progress(0.3 + 0.65 * (step + 1) / steps, f"Drawing the views: step {step + 1} of {steps}")
            return kw

        images = pipe(
            f"a {inp.subject or 'object'}, high quality", height=size, width=size, num_inference_steps=steps,
            guidance_scale=3.0, num_images_per_prompt=n, control_image=control, control_conditioning_scale=1.0,
            reference_image=ref.convert("RGB"), reference_conditioning_scale=1.0,
            negative_prompt="watermark, ugly, deformed, noisy, blurry, low contrast",
            generator=torch.Generator("cpu").manual_seed(int(inp.seed)), callback_on_step_end=cb,
        ).images
        return views_mod.save_views({name: images[i] for name, i in self.KEEP.items()}, inp.out_dir)


VIEWERS = {e.id: e for e in (OpenAIViews(), FalViews(), LocalViews())}
