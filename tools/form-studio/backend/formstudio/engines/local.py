"""Local engines: free, run on your Mac's GPU (Apple MPS), CUDA or CPU.

Models download from Hugging Face on first use (cached in ~/.cache/huggingface)
and stay loaded between runs, so only the first run is slow.
"""
from __future__ import annotations

import gc
import logging
import os
import sys
import threading
import types
from collections import OrderedDict
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .. import paths
from ..core import timing
from ..core.ingest import remove_background
from .base import EngineContext, Progress, ReconstructInput, StylizeInput, missing, quality_of

log = logging.getLogger(__name__)

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


# Loaded models, least recently used first. Keys are "<family>:<detail>:<device>".
_models: "OrderedDict[str, Any]" = OrderedDict()
_models_lock = threading.RLock()
# roughly what each family takes in memory (GB, fp16 on Apple Silicon)
FAMILY_GB = {"sdxl": 9.0, "sdxl-turbo": 7.0, "triposr": 2.0, "hunyuan": 6.0, "hunyuan-mv": 7.0, "mvadapter": 12.0}
_policy = {"keep_loaded": "auto"}


def configure(settings: dict[str, Any]) -> None:
    _policy["keep_loaded"] = settings.get("keep_loaded") or "auto"


def ram_gb() -> float | None:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3
    except (ValueError, OSError, AttributeError):
        return None


def budget_gb(keep_loaded: str | None = None) -> float:
    """How much memory loaded models may use. Macs share memory between the CPU and GPU, so half of it."""
    if (keep_loaded or _policy["keep_loaded"]) == "one":
        return 0.0
    return 0.5 * (ram_gb() or 16.0)


def _family(key: str) -> str:
    return key.split(":")[0]


def is_loaded(family: str) -> bool:
    with _models_lock:
        return any(_family(k) == family for k in _models)


def loaded_families() -> list[str]:
    with _models_lock:
        return list(dict.fromkeys(_family(k) for k in _models))


def would_fit(family: str) -> bool:
    """True if loading this family wouldn't push anything else out."""
    with _models_lock:
        others = {_family(k) for k in _models} - {family}
        used = sum(FAMILY_GB.get(f, 1.0) for f in others)
        return not others or used + FAMILY_GB.get(family, 1.0) <= budget_gb()


def _cached(key: str, factory):
    with _models_lock:
        if key in _models:
            _models.move_to_end(key)
            return _models[key]
        fam = _family(key)
        need = FAMILY_GB.get(fam, 1.0)
        freed = False
        while True:  # unload the least recently used other families until this one fits
            others = [k for k in _models if _family(k) != fam]
            used = sum(FAMILY_GB.get(f, 1.0) for f in {_family(k) for k in others})
            if not others or used + need <= budget_gb():
                break
            victim = _family(others[0])
            for k in [k for k in _models if _family(k) == victim]:
                del _models[k]
            log.info("unloaded %s to make room for %s", victim, fam)
            freed = True
        if freed:
            gc.collect()
            _empty_cache()
        with timing.stage("model_load", engine=fam, device=key.split(":")[-1]):
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


def _out_of_memory(e: BaseException) -> bool:
    return "out of memory" in str(e).lower()


def _retry_on_oom(fn):
    """Run; if memory runs out, unload every model and try once more."""
    try:
        return fn()
    except RuntimeError as e:
        if not _out_of_memory(e):
            raise
        log.warning("out of memory; unloading all models and trying again")
        unload_all()
        return fn()


def _speedups(pipe, attention_slicing: bool = True) -> None:
    """Memory savers, whichever way the installed diffusers spells them.

    Attention slicing must stay off once an IP-Adapter is loaded: it swaps in attention
    layers that can't take the style image, and generation fails with
    "'tuple' object has no attribute 'shape'".
    """
    if attention_slicing and hasattr(pipe, "enable_attention_slicing"):
        pipe.enable_attention_slicing()
    vae = getattr(pipe, "vae", None)
    if vae is not None and hasattr(vae, "enable_tiling"):
        vae.enable_tiling()
    elif hasattr(pipe, "enable_vae_tiling"):
        pipe.enable_vae_tiling()


def _vae_slicing(pipe) -> None:
    """Decode a batch of images one at a time (less memory), however the installed diffusers spells it.

    diffusers 0.42 removed pipe.enable_vae_slicing(); the VAE itself still has enable_slicing().
    """
    vae = getattr(pipe, "vae", None)
    if vae is not None and hasattr(vae, "enable_slicing"):
        vae.enable_slicing()
    elif hasattr(pipe, "enable_vae_slicing"):
        pipe.enable_vae_slicing()


def _ensure_models(pack: str, progress: Progress, lo: float = 0.0, hi: float = 0.02) -> None:
    """Download the engine's models first (with GB, speed and time left), if they're missing."""
    from ..models import downloads

    downloads.ensure(pack, lambda p, m: progress(None if p is None else lo + (hi - lo) * p, m))


def style_reference(style) -> tuple[Image.Image, float]:
    """The picture the IP-Adapter copies the look from, and how strongly.

    The SDXL pipeline always has the IP-Adapter loaded, and it refuses to run without
    an image ("requires the keyword argument image_embeds"). A style without reference
    pictures gets a plain white image at strength 0, so its words alone set the look.
    """
    if style.reference is not None:
        return Image.open(style.reference).convert("RGB"), float(style.stylize.get("ip_adapter_scale", 0.6))
    return Image.new("RGB", (224, 224), (255, 255, 255)), 0.0


def ip_adapter_scale_config(scale: float, mode: str = "style"):
    """How the style picture feeds SDXL.

    "style" (the default) sends it only into the block of SDXL that handles colour, material and
    surface (InstantStyle), so the look carries over but the objects and layout of the reference
    photo don't get pasted in. "full" is the old behaviour: every layer, look and composition.
    A strength of 0 (no reference pictures) switches it off everywhere.
    """
    if scale <= 0:
        return 0.0
    if mode == "full":
        return scale
    return {"up": {"block_0": [0.0, min(1.0, scale / 0.6), 0.0]}}


def _on_white(img: Image.Image) -> Image.Image:
    """Clean the generated image: object on pure white, ready for 3D."""
    rgba = remove_background(img)
    canvas = Image.new("RGB", img.size, (255, 255, 255))
    canvas.paste(rgba, (0, 0), rgba)
    return canvas


# ---------------------------------------------------------------- stylize ----

LIGHTNING = ("ByteDance/SDXL-Lightning", "sdxl_lightning_8step_lora.safetensors")
_pipe_state: dict[int, dict[str, Any]] = {}  # per loaded pipeline: its own scheduler, whether Lightning is in


def _set_quick(pipe, quick: bool) -> None:
    """Switch the loaded SDXL pipeline between Best (30 steps) and Quick (SDXL-Lightning, 8 steps).

    The Lightning LoRA goes in as a named adapter once and is switched on and off, so changing
    quality never reloads SDXL. The IP-Adapter and ControlNet stay as they are.
    """
    st = _pipe_state.setdefault(id(pipe), {"scheduler": pipe.scheduler, "lightning": False})
    if quick:
        if not st["lightning"]:
            from huggingface_hub import hf_hub_download

            with timing.stage("model_load", engine="sdxl-lightning"):
                pipe.load_lora_weights(hf_hub_download(*LIGHTNING), adapter_name="lightning")
            st["lightning"] = True
        if hasattr(pipe, "enable_lora"):
            pipe.enable_lora()
        pipe.set_adapters(["lightning"], adapter_weights=[1.0])
        from diffusers import EulerDiscreteScheduler

        pipe.scheduler = EulerDiscreteScheduler.from_config(st["scheduler"].config, timestep_spacing="trailing")
    else:
        if st["lightning"]:
            if hasattr(pipe, "disable_lora"):
                pipe.disable_lora()
            else:
                pipe.set_adapters(["lightning"], adapter_weights=[0.0])
        pipe.scheduler = st["scheduler"]


def sdxl_packs(settings: dict[str, Any]) -> list[str]:
    q = quality_of(settings)
    return ["sdxl-turbo"] if q == "lightest" else ["sdxl"] + (["sdxl-lightning"] if q == "quick" else [])


class LocalSDXL:
    id = "local-sdxl"
    label = "SDXL + style reference (local)"
    kind = "local"
    note = "Free. SDXL guided by the style's reference image and your photo's depth. Quick: ~20 s per image; Best: ~1–2 min."

    def __init__(self) -> None:
        self._depth_cache: "OrderedDict[tuple, Image.Image]" = OrderedDict()

    def available(self, ctx: EngineContext) -> tuple[bool, str]:
        why = missing("torch", "diffusers", "transformers")
        if not why and quality_of(ctx.settings) == "quick":
            why = missing("peft")
            if why:
                why += " (needed for Quick quality)"
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
            _speedups(p, attention_slicing=False)  # keeps the IP-Adapter's own attention layers
            return p

        return _cached(f"sdxl:{device}", make)

    def _depth_model(self, ctx: EngineContext):
        def make():
            from transformers import pipeline

            return pipeline("depth-estimation", model="depth-anything/Depth-Anything-V2-Small-hf", device=ctx.device)

        return _cached(f"sdxl:depth:{ctx.device}", make)

    def _depth(self, ctx: EngineContext, img: Image.Image, key: tuple) -> Image.Image:
        """The photo's depth map, worked out once per photo and size (every option reuses it)."""
        if key in self._depth_cache:
            self._depth_cache.move_to_end(key)
            return self._depth_cache[key]
        with timing.stage("depth", device=ctx.device, size=img.width):
            d = self._depth_model(ctx)(img)["depth"].convert("L").resize(img.size)
        out = Image.merge("RGB", (d, d, d))
        self._depth_cache[key] = out
        while len(self._depth_cache) > 4:
            self._depth_cache.popitem(last=False)
        return out

    def warm(self, ctx: EngineContext) -> None:
        """Load everything a Generate needs, so the first option starts straight away."""
        q = quality_of(ctx.settings)
        pipe = self._pipe(ctx, q == "lightest")
        if q != "lightest":
            self._depth_model(ctx)
            _set_quick(pipe, q == "quick")

    def stylize(self, inp: StylizeInput, ctx: EngineContext, progress: Progress) -> list[dict]:
        return _retry_on_oom(lambda: self._stylize(inp, ctx, progress))

    def _stylize(self, inp: StylizeInput, ctx: EngineContext, progress: Progress) -> list[dict]:
        import torch

        q = quality_of(ctx.settings)
        fast, quick = q == "lightest", q == "quick"
        cfg = inp.style.stylize
        size = 512 if fast else int(cfg.get("size", 1024))
        photo = Image.open(inp.cutout).convert("RGB").resize((size, size), Image.LANCZOS)
        for pack in sdxl_packs(ctx.settings):
            _ensure_models(pack, progress, 0.0, 0.5)
        warm = is_loaded("sdxl-turbo" if fast else "sdxl")
        if not warm:
            progress(None, "Loading the models into memory (only the first time)")
        pipe = self._pipe(ctx, fast)
        if not fast:
            _set_quick(pipe, quick)
        st = inp.cutout.stat()
        depth = None if fast else self._depth(ctx, photo, (str(inp.cutout), st.st_mtime_ns, size))
        ref, ip_scale = (None, 0.0) if fast else style_reference(inp.style)
        if ref is not None and ip_scale == 0.0:
            progress(None, "Style from words only. Add reference pictures in Styles for a closer match.")

        steps = 4 if fast else 8 if quick else int(cfg.get("steps", 30))
        out = []
        n = len(inp.seeds)
        for i, seed in enumerate(inp.seeds):
            def cb(p, step, t, kw, i=i):
                progress((i + (step + 1) / steps) / n, f"Option {i + 1} of {n}: step {step + 1} of {steps}")
                return kw

            gen = torch.Generator("cpu").manual_seed(int(seed))
            if not fast and hasattr(pipe, "set_ip_adapter_scale"):
                # every run: one style's strength and routing must not carry over to the next
                pipe.set_ip_adapter_scale(ip_adapter_scale_config(ip_scale, str(cfg.get("reference_mode", "style"))))
            prompt = inp.style.prompt_for(inp.subject)
            if quick:  # no guidance in Quick: spell out the main colour so the words still hold
                body = (inp.style.palette.get("body") or {}).get("name")
                if body and body.lower() not in prompt.lower():
                    prompt += f", {body.lower()} colour"
            with timing.stage("restyle_image", engine=self.id, device=ctx.device, quality=q, size=size,
                              steps=steps, warm=warm):
                if fast:
                    img = pipe(prompt=prompt, image=photo, strength=0.6, num_inference_steps=steps, guidance_scale=0.0,
                               generator=gen, callback_on_step_end=cb).images[0]
                else:
                    img = pipe(
                        prompt=prompt,
                        # Lightning runs without guidance, so there's nothing to steer away from
                        negative_prompt=None if quick else inp.style.negative_prompt,
                        image=depth, ip_adapter_image=ref,
                        controlnet_conditioning_scale=float(cfg.get("controlnet_scale", 0.55)),
                        num_inference_steps=steps, guidance_scale=0.0 if quick else float(cfg.get("guidance", 6.0)),
                        width=size, height=size, generator=gen, callback_on_step_end=cb,
                    ).images[0]
            warm = True
            with timing.stage("clean_output", device=ctx.device):
                img = _on_white(img).resize((1024, 1024), Image.LANCZOS)
            name = f"candidate_{i + 1}.png"
            img.save(inp.out_dir / name)
            out.append({"file": name, "seed": seed, "quality": q})
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
        return True, ""  # the code and model download on first use (Settings → Local AI models)

    def _model(self, ctx: EngineContext):
        if str(triposr_dir()) not in sys.path:
            sys.path.insert(0, str(triposr_dir()))
        _install_torchmcubes_shim()
        from tsr.system import TSR  # type: ignore

        device = ctx.device

        def make():
            m = TSR.from_pretrained("stabilityai/TripoSR", config_name="config.yaml", weight_name="model.ckpt")
            m.renderer.set_chunk_size(8192)
            m.to(device)
            return m

        return _cached(f"triposr:{device}", make)

    def warm(self, ctx: EngineContext) -> None:
        self._model(ctx)

    def reconstruct(self, inp: ReconstructInput, ctx: EngineContext, progress: Progress) -> dict:
        _ensure_models("triposr-code", progress, 0.0, 0.02)
        _ensure_models("triposr", progress, 0.0, 0.25)
        return _retry_on_oom(lambda: self._reconstruct(inp, ctx, progress))

    def _reconstruct(self, inp: ReconstructInput, ctx: EngineContext, progress: Progress) -> dict:
        import torch
        import trimesh

        device = ctx.device
        warm = is_loaded("triposr")
        if not warm:
            progress(None, "Loading TripoSR into memory (only the first time)")
        model = self._model(ctx)
        from tsr.utils import resize_foreground  # type: ignore

        img = resize_foreground(_rgba(inp), 0.85)
        arr = np.asarray(img).astype(np.float32) / 255.0
        arr = arr[:, :, :3] * arr[:, :, 3:4] + (1 - arr[:, :, 3:4]) * 0.5
        image = Image.fromarray((arr * 255.0).astype(np.uint8))
        progress(0.3, "Building the shape")
        torch.manual_seed(inp.seed)
        with timing.stage("reconstruct_infer", engine=self.id, device=device, warm=warm), torch.no_grad():
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


class LocalHunyuanMV:
    """Hunyuan3D-2mv: builds the shape from front, side and back views ("Make 3 views"). Without views, the front only."""

    id = "hunyuan-mv"
    label = "Hunyuan3D-2 multi-view (local)"
    kind = "local"
    multi_view = True
    note = "Free (Tencent community licence). Uses all 3 views for a much better back and sides; ~7 GB, a few minutes."

    def available(self, ctx: EngineContext) -> tuple[bool, str]:
        why = missing("torch", "hy3dgen")
        if why:
            return False, why + " Run ./scripts/setup-mac.sh --with-hunyuan."
        return True, ""

    def reconstruct(self, inp: ReconstructInput, ctx: EngineContext, progress: Progress) -> dict:
        _ensure_models("hunyuan-mv", progress, 0.0, 0.2)
        return _retry_on_oom(lambda: self._reconstruct(inp, ctx, progress))

    def _reconstruct(self, inp: ReconstructInput, ctx: EngineContext, progress: Progress) -> dict:
        import torch
        from hy3dgen.shapegen import DegenerateFaceRemover, FaceReducer, FloaterRemover, Hunyuan3DDiTFlowMatchingPipeline  # type: ignore

        device = ctx.device

        def make():
            return Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(
                "tencent/Hunyuan3D-2mv", subfolder="hunyuan3d-dit-v2-mv", use_safetensors=True, device=device,
            )

        if not is_loaded("hunyuan-mv"):
            progress(None, "Loading Hunyuan3D-2 multi-view into memory (only the first time)")
        pipe = _cached(f"hunyuan-mv:{device}", make)

        def rgba(img: Path, mask: Path) -> Image.Image:
            im = Image.open(img).convert("RGBA")
            im.putalpha(Image.open(mask).convert("L"))
            return im

        views = inp.views or {"front": (inp.image, inp.mask)}
        images = {name: rgba(*paths_) for name, paths_ in views.items()}
        progress(0.25, f"Building the shape from {len(images)} view{'s' if len(images) > 1 else ''}")
        with timing.stage("reconstruct_infer", engine=self.id, device=device, n=len(images)):
            mesh = pipe(
                image=images, num_inference_steps=30, octree_resolution=380, num_chunks=20000,
                generator=torch.Generator("cpu").manual_seed(inp.seed), output_type="trimesh",
            )[0]
        progress(0.85, "Cleaning up")
        for fix in (FloaterRemover(), DegenerateFaceRemover(), FaceReducer()):
            mesh = fix(mesh)
        mesh.export(inp.out)  # Hunyuan already uses glTF y-up
        progress(1.0, "Done")
        return {"model": "tencent/Hunyuan3D-2mv", "device": device, "views": len(images)}


# ---------------------------------------------------------------- warm-up ----

def warm(ctx: EngineContext, stylize_engine: str | None, reconstruct_engine: str | None) -> list[str]:
    """Load the default local engines in the background, so the next step starts at once.

    Only models that are already downloaded, and only what fits without unloading something
    else. Returns the families that are now in memory because of this call.
    """
    from ..models import status
    from . import RECONSTRUCTORS, STYLIZERS

    done: list[str] = []
    if (ram_gb() or 16.0) < 15:  # on an 8 GB Mac, loading SDXL nobody asked for yet would slow everything down
        return done

    def ready(packs: list[str]) -> bool:
        try:
            return all(status(p)["status"] == "ready" for p in packs)
        except Exception:  # can't tell offline: don't guess
            return False

    if stylize_engine == "local-sdxl":
        eng = STYLIZERS["local-sdxl"]
        fam = "sdxl-turbo" if quality_of(ctx.settings) == "lightest" else "sdxl"
        if eng.available(ctx)[0] and ready(sdxl_packs(ctx.settings)) and (is_loaded(fam) or would_fit(fam)):
            eng.warm(ctx)
            done.append(fam)
    if reconstruct_engine == "triposr":
        eng = RECONSTRUCTORS["triposr"]
        if eng.available(ctx)[0] and ready(["triposr-code", "triposr"]) and (is_loaded("triposr") or would_fit("triposr")):
            eng.warm(ctx)
            done.append("triposr")
    return done
