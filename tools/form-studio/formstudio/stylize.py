"""Stage 2: cutout -> N styled candidates on a white background.

Backends:
- "passthrough": copies the cutout N times. For tests and the stub flow.
- "sdxl": SDXL + IP-Adapter (style from the reference image) + ControlNet-depth
  (keeps the real silhouette). Runs on MPS in fp16 with attention slicing.
- "turbo": same as sdxl but SDXL-Turbo, for low-memory Macs (8 GB).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Literal

from PIL import Image

from .style import StyleSpec

Backend = Literal["passthrough", "sdxl", "turbo"]


def stylize(
    cutout: Path,
    style: StyleSpec,
    out_dir: Path,
    n: int | None = None,
    backend: Backend = "passthrough",
    device: str | None = None,
) -> list[Path]:
    n = n or style.candidates
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.png"):
        old.unlink()
    if backend == "passthrough":
        paths = []
        for i in range(1, n + 1):
            dst = out_dir / f"{i}.png"
            shutil.copyfile(cutout, dst)
            paths.append(dst)
        return paths
    return _stylize_diffusion(cutout, style, out_dir, n, turbo=backend == "turbo", device=device)


def _pick_device(preferred: str | None) -> str:
    import torch

    if preferred:
        return preferred
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _depth_map(image: Image.Image, model_id: str, device: str) -> Image.Image:
    from transformers import pipeline

    pipe = pipeline("depth-estimation", model=model_id, device=device)
    return pipe(image)["depth"].convert("RGB")


def _stylize_diffusion(
    cutout: Path,
    style: StyleSpec,
    out_dir: Path,
    n: int,
    turbo: bool,
    device: str | None,
) -> list[Path]:
    import torch
    from diffusers import ControlNetModel, StableDiffusionXLControlNetPipeline

    dev = _pick_device(device)
    dtype = torch.float16 if dev in ("mps", "cuda") else torch.float32
    m = style.models
    base_id = m["stylize_fast"] if turbo else m["stylize"]

    controlnet = ControlNetModel.from_pretrained(m["controlnet_depth"], torch_dtype=dtype)
    pipe = StableDiffusionXLControlNetPipeline.from_pretrained(
        base_id, controlnet=controlnet, torch_dtype=dtype, variant="fp16" if dtype == torch.float16 else None
    )
    pipe.load_ip_adapter(m["ip_adapter_repo"], subfolder="sdxl_models", weight_name="ip-adapter_sdxl.bin")
    pipe.set_ip_adapter_scale(style.ip_adapter_scale)
    pipe.enable_attention_slicing()
    pipe.to(dev)

    source = Image.open(cutout).convert("RGB").resize((style.image_size, style.image_size))
    depth = _depth_map(source, m["depth_estimator"], dev).resize(source.size)
    reference = Image.open(style.reference_path).convert("RGB").resize(source.size)

    paths: list[Path] = []
    steps = 4 if turbo else style.steps
    guidance = 0.0 if turbo else style.guidance_scale
    for i in range(1, n + 1):
        gen = torch.Generator(device="cpu").manual_seed(style.seed_base + i)
        img = pipe(
            prompt=style.prompt,
            negative_prompt=style.negative_prompt,
            image=depth,
            ip_adapter_image=reference,
            controlnet_conditioning_scale=style.controlnet_scale,
            num_inference_steps=steps,
            guidance_scale=guidance,
            generator=gen,
        ).images[0]
        dst = out_dir / f"{i}.png"
        img.save(dst)
        paths.append(dst)
    del pipe, controlnet
    return paths
