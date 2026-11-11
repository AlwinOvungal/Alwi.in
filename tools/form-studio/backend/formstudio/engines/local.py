"""Local engines: free, run on your Mac's GPU (Apple MPS), CUDA or CPU.

Models download from Hugging Face on first use (cached in ~/.cache/huggingface)
and stay loaded between runs, so only the first run is slow.
"""
from __future__ import annotations

import gc
import logging
import sys
import threading
import types
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .. import paths
from ..core.ingest import remove_background
from .base import EngineContext, Progress, ReconstructInput, StylizeInput, missing

log = logging.getLogger(__name__)

_models: dict[str, Any] = {}
_models_lock = threading.Lock()


def pick_device(pref: str = "auto") -> str:
    if pref and pref != "auto":
        return pref
    try:
        import torch
    except ImportError:
        return "cpu"
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _cached(key: str, factory):
    with _models_lock:
        if key not in _models:
            # one big model at a time keeps memory sane on a laptop
            for k in [k for k in _models if k.split(":")[0] != key.split(":")[0]]:
                del _models[k]
            gc.collect()
            _empty_cache()
            _models[key] = factory()
        return _models[key]


def _empty_cache() -> None:
    try:
        import torch

        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
        elif torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def unload_all() -> None:
    with _models_lock:
        _models.clear()
    gc.collect()
    _empty_cache()


def _speedups(pipe) -> None:
    """Memory savers, whichever way the installed diffusers spells them."""
    if hasattr(pipe, "enable_attention_slicing"):
        pipe.enable_attention_slicing()
    vae = getattr(pipe, "vae", None)
    if vae is not None and hasattr(vae, "enable_tiling"):
        vae.enable_tiling()
    elif hasattr(pipe, "enable_vae_tiling"):
        pipe.enable_vae_tiling()


def _ensure_models(pack: str, progress: Progress, lo: float = 0.0, hi: float = 0.02) -> None:
    """Download the engine's models first (with GB, speed and time left), if they're missing."""
    from ..models import downloads

    downloads.ensure(pack, lambda p, m: progress(None if p is None else lo + (hi - lo) * p, m))


def _on_white(img: Image.Image) -> Image.Image:
    """Clean the generated image: object on pure white, ready for 3D."""
    rgba = remove_background(img)
    canvas = Image.new("RGB", img.size, (255, 255, 255))
    canvas.paste(rgba, (0, 0), rgba)
    return canvas


# ---------------------------------------------------------------- stylize ----

class LocalSDXL:
    id = "local-sdxl"
    label = "SDXL + style reference (local)"
    kind = "local"
    note = "Free. SDXL guided by the style's reference image and your photo's depth. ~1–2 min per image on Apple Silicon."

    def available(self, ctx: EngineContext) -> tuple[bool, str]:
        why = missing("torch", "diffusers", "transformers")
        return (why is None, why or "")

    def _pipe(self, ctx: EngineContext, fast: bool):
        import torch

        device = ctx.device
        dtype = torch.float16 if device in ("mps", "cuda") else torch.float32

        if fast:
            def make():
                from diffusers import AutoPipelineForImage2Image

                p = AutoPipelineForImage2Image.from_pretrained("stabilityai/sdxl-turbo", torch_dtype=dtype, variant="fp16" if dtype == torch.float16 else None)
                p.to(device)
                _speedups(p)
                return p

            return _cached(f"sdxl-turbo:{device}", make)

        def make():
            from diffusers import AutoencoderKL, ControlNetModel, StableDiffusionXLControlNetPipeline

            controlnet = ControlNetModel.from_pretrained("diffusers/controlnet-depth-sdxl-1.0-small", torch_dtype=dtype)
            vae = AutoencoderKL.from_pretrained("madebyollin/sdxl-vae-fp16-fix", torch_dtype=dtype)
            p = StableDiffusionXLControlNetPipeline.from_pretrained(
                "stabilityai/stable-diffusion-xl-base-1.0", controlnet=controlnet, vae=vae, torch_dtype=dtype,
                variant="fp16" if dtype == torch.float16 else None, use_safetensors=True,
            )
            p.load_ip_adapter("h94/IP-Adapter", subfolder="sdxl_models", weight_name="ip-adapter_sdxl.safetensors")
            p.to(device)
            _speedups(p)
            return p

        return _cached(f"sdxl:{device}", make)

    def _depth(self, ctx: EngineContext, img: Image.Image) -> Image.Image:
        def make():
            from transformers import pipeline

            return pipeline("depth-estimation", model="depth-anything/Depth-Anything-V2-Small-hf", device=ctx.device)

        est = _cached(f"sdxl:depth:{ctx.device}", make)
        d = est(img)["depth"].convert("L").resize(img.size)
        return Image.merge("RGB", (d, d, d))

    def stylize(self, inp: StylizeInput, ctx: EngineContext, progress: Progress) -> list[dict]:
        import torch

        fast = bool(ctx.settings.get("fast_mode"))
        cfg = inp.style.stylize
        size = 512 if fast else int(cfg.get("size", 1024))
        photo = Image.open(inp.cutout).convert("RGB").resize((size, size), Image.LANCZOS)
        _ensure_models("sdxl-turbo" if fast else "sdxl", progress, 0.0, 0.5)
        progress(None, "Loading the models into memory")
        pipe = self._pipe(ctx, fast)
        depth = None if fast else self._depth(ctx, photo)
        ref = Image.open(inp.style.reference).convert("RGB") if (inp.style.reference and not fast) else None
        if not fast and hasattr(pipe, "set_ip_adapter_scale"):
            pipe.set_ip_adapter_scale(float(cfg.get("ip_adapter_scale", 0.6)))

        out = []
        n = len(inp.seeds)
        for i, seed in enumerate(inp.seeds):
            steps = 4 if fast else int(cfg.get("steps", 30))

            def cb(p, step, t, kw, i=i, steps=steps):
                progress((i + (step + 1) / steps) / n, f"Candidate {i + 1} of {n}: step {step + 1}/{steps}")
                return kw

            gen = torch.Generator("cpu").manual_seed(int(seed))
            prompt = inp.style.prompt_for(inp.subject)
            if fast:
                img = pipe(prompt=prompt, image=photo, strength=0.6, num_inference_steps=steps, guidance_scale=0.0,
                           generator=gen, callback_on_step_end=cb).images[0]
            else:
                img = pipe(
                    prompt=prompt, negative_prompt=inp.style.negative_prompt, image=depth, ip_adapter_image=ref,
                    controlnet_conditioning_scale=float(cfg.get("controlnet_scale", 0.55)),
                    num_inference_steps=steps, guidance_scale=float(cfg.get("guidance", 6.0)),
                    width=size, height=size, generator=gen, callback_on_step_end=cb,
                ).images[0]
            img = _on_white(img).resize((1024, 1024), Image.LANCZOS)
            name = f"candidate_{i + 1}.png"
            img.save(inp.out_dir / name)
            out.append({"file": name, "seed": seed})
        progress(1.0, "Done")
        return out


# ------------------------------------------------------------- reconstruct ----

def _rgba(inp: ReconstructInput) -> Image.Image:
    img = Image.open(inp.image).convert("RGBA")
    img.putalpha(Image.open(inp.mask).convert("L"))
    return img


def _install_torchmcubes_shim() -> None:
    """TripoSR imports torchmcubes (a CUDA/C++ build). On a Mac we swap in scikit-image."""
    try:
        import torchmcubes  # noqa: F401
        return
    except ImportError:
        pass
    import torch
    from skimage import measure

    def marching_cubes(vol, level):
        v, f, _, _ = measure.marching_cubes(vol.detach().cpu().numpy(), level)
        return torch.from_numpy(v.copy()).float(), torch.from_numpy(f.astype(np.int64).copy())

    sys.modules["torchmcubes"] = types.SimpleNamespace(marching_cubes=marching_cubes)  # type: ignore


def triposr_dir() -> Path:
    return paths.home() / "vendor" / "TripoSR"


class LocalTripoSR:
    id = "triposr"
    label = "TripoSR (local)"
    kind = "local"
    note = "Free, MIT licence. Fast single-image 3D, runs on Apple Silicon. Under a minute."

    def available(self, ctx: EngineContext) -> tuple[bool, str]:
        why = missing("torch", "omegaconf", "einops")
        if why:
            return False, why
        if not (triposr_dir() / "tsr").exists():
            return False, f"TripoSR code not found in {triposr_dir()}. Run scripts/setup-mac.sh."
        return True, ""

    def reconstruct(self, inp: ReconstructInput, ctx: EngineContext, progress: Progress) -> dict:
        import torch
        import trimesh

        sys.path.insert(0, str(triposr_dir()))
        _install_torchmcubes_shim()
        from tsr.system import TSR  # type: ignore
        from tsr.utils import resize_foreground  # type: ignore

        device = ctx.device

        def make():
            m = TSR.from_pretrained("stabilityai/TripoSR", config_name="config.yaml", weight_name="model.ckpt")
            m.renderer.set_chunk_size(8192)
            m.to(device)
            return m

        _ensure_models("triposr", progress, 0.0, 0.25)
        progress(None, "Loading TripoSR into memory")
        model = _cached(f"triposr:{device}", make)
        img = resize_foreground(_rgba(inp), 0.85)
        arr = np.asarray(img).astype(np.float32) / 255.0
        arr = arr[:, :, :3] * arr[:, :, 3:4] + (1 - arr[:, :, 3:4]) * 0.5
        image = Image.fromarray((arr * 255.0).astype(np.uint8))
        progress(0.3, "Building the shape")
        torch.manual_seed(inp.seed)
        with torch.no_grad():
            codes = model([image], device=device)
            progress(0.7, "Extracting the surface")
            try:
                meshes = model.extract_mesh(codes, has_vertex_color=False, resolution=256)
            except TypeError:
                meshes = model.extract_mesh(codes, resolution=256)
        mesh: trimesh.Trimesh = meshes[0]
        # TripoSR is z-up facing +x; turn it to glTF y-up facing the viewer
        mesh.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
        mesh.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0]))
        mesh.export(inp.out)
        progress(1.0, "Done")
        return {"model": "stabilityai/TripoSR", "device": device}


class LocalHunyuanMini:
    id = "hunyuan-mini"
    label = "Hunyuan3D-2 mini (local)"
    kind = "local"
    note = "Free (Tencent community licence). Better shapes than TripoSR, slower; uses ~6 GB."

    def available(self, ctx: EngineContext) -> tuple[bool, str]:
        why = missing("torch", "hy3dgen")
        return (why is None, why or "")

    def reconstruct(self, inp: ReconstructInput, ctx: EngineContext, progress: Progress) -> dict:
        import torch
        from hy3dgen.shapegen import DegenerateFaceRemover, FaceReducer, FloaterRemover, Hunyuan3DDiTFlowMatchingPipeline  # type: ignore

        device = ctx.device

        def make():
            return Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(
                "tencent/Hunyuan3D-2mini", subfolder="hunyuan3d-dit-v2-mini", use_safetensors=True, device=device,
            )

        _ensure_models("hunyuan", progress, 0.0, 0.2)
        progress(None, "Loading Hunyuan3D-2 mini into memory")
        pipe = _cached(f"hunyuan:{device}", make)
        progress(0.25, "Building the shape")
        steps = 30
        mesh = pipe(
            image=_rgba(inp), num_inference_steps=steps, octree_resolution=256,
            generator=torch.Generator("cpu").manual_seed(inp.seed), output_type="trimesh",
        )[0]
        progress(0.85, "Cleaning up")
        for fix in (FloaterRemover(), DegenerateFaceRemover(), FaceReducer()):
            mesh = fix(mesh)
        mesh.export(inp.out)  # Hunyuan already uses glTF y-up
        progress(1.0, "Done")
        return {"model": "tencent/Hunyuan3D-2mini", "device": device}
