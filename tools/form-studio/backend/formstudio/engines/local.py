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
from .base import SHEET_NEGATIVE, EngineContext, EngineError, Progress, ReconstructInput, StylizeInput, missing, quality_of

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
FAMILY_GB = {"sdxl": 9.0, "sdxl-turbo": 7.0, "triposr": 2.0, "hunyuan": 6.0, "hunyuan-mv": 7.0, "triposg": 6.0, "mvadapter": 12.0}
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
            with timing.stage("restyle_image", engine=self.id, device=ctx.device, quality=q, size=size,
                              steps=steps, warm=warm):
                if fast:
                    img = pipe(prompt=prompt, image=photo, strength=0.6, num_inference_steps=steps, guidance_scale=0.0,
                               generator=gen, callback_on_step_end=cb).images[0]
                else:
                    img = pipe(
                        prompt=prompt,
                        # Lightning runs without guidance, so there's nothing to steer away from
                        negative_prompt=None if quick else f"{inp.style.negative_prompt}, {SHEET_NEGATIVE}",
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


# transformers 5 renamed the ViT layers inside TripoSR's image encoder (DINO). TripoSR's checkpoint has the
# old names and is loaded with a plain load_state_dict, so transformers' own conversion never runs.
_VIT_RENAMES = [  # (old, new) as regex pieces after "...encoder.layer.N." / "...layers.N."
    (r"attention\.attention\.query", "attention.q_proj"),
    (r"attention\.attention\.key", "attention.k_proj"),
    (r"attention\.attention\.value", "attention.v_proj"),
    (r"attention\.output\.dense", "attention.o_proj"),
    (r"intermediate\.dense", "mlp.fc1"),
    (r"output\.dense", "mlp.fc2"),
    (r"layernorm_before", "layernorm_before"),
    (r"layernorm_after", "layernorm_after"),
]


def _rename_vit(key: str, to_new: bool) -> str:
    import re

    if to_new:
        m = re.match(r"^(.*)\bencoder\.layer\.(\d+)\.(.+)$", key)
        if not m:
            return key
        pre, n, rest = m.groups()
        for old, new in _VIT_RENAMES:
            if re.fullmatch(old + r"\.(weight|bias)", rest):
                return f"{pre}layers.{n}.{new}.{rest.rsplit('.', 1)[1]}"
        return key
    m = re.match(r"^(.*)\blayers\.(\d+)\.(.+)$", key)
    if not m:
        return key
    pre, n, rest = m.groups()
    for old, new in _VIT_RENAMES:
        if re.fullmatch(re.escape(new) + r"\.(weight|bias)", rest):
            return f"{pre}encoder.layer.{n}.{old.replace(chr(92), '')}.{rest.rsplit('.', 1)[1]}"
    return key


def _match_state_dict(ckpt: dict[str, Any], model_keys: set[str], name: str = "TripoSR") -> dict[str, Any]:
    """The checkpoint with its keys renamed to what the installed library's model expects (either
    direction of the transformers 4 → 5 ViT renaming). Raises a readable EngineError if they still differ."""
    if set(ckpt) == model_keys:
        return ckpt
    out = {}
    for k, v in ckpt.items():
        if k in model_keys:
            out[k] = v
            continue
        for to_new in (True, False):
            k2 = _rename_vit(k, to_new)
            if k2 in model_keys:
                out[k2] = v
                break
        else:
            out[k] = v
    missing_ = sorted(model_keys - set(out))
    extra = sorted(set(out) - model_keys)
    if missing_ or extra:
        raise EngineError(
            f"{name}'s model files don't match the installed AI library, so it can't load. "
            "Run setup again (git pull, then ./scripts/setup-mac.sh), or build this object From the picture "
            "or with a cloud 3D engine.",
            detail=f"{len(missing_)} missing, {len(extra)} unexpected, e.g. {', '.join((missing_ + extra)[:3])}",
        )
    return out


def _load_tsr(TSR):
    """TSR.from_pretrained, but tolerant of the transformers version (see _match_state_dict)."""
    import torch
    from huggingface_hub import hf_hub_download
    from omegaconf import OmegaConf

    cfg = OmegaConf.load(hf_hub_download("stabilityai/TripoSR", "config.yaml"))
    OmegaConf.resolve(cfg)
    model = TSR(cfg)
    ckpt = torch.load(hf_hub_download("stabilityai/TripoSR", "model.ckpt"), map_location="cpu")
    model.load_state_dict(_match_state_dict(ckpt, set(model.state_dict().keys())))
    return model


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
            m = _load_tsr(TSR)
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


# ---- TripoSG -------------------------------------------------------------------------------

class _DiffDMC:
    """Stand-in for diso.DiffDMC (a CUDA build) using scikit-image's marching cubes on the CPU.

    TripoSG calls `DiffDMC(dtype=...).to(device)` then `dmc(sdf, deform=None, return_quads=False,
    normalize=False)` with sdf < 0 inside, and expects (vertices in grid units, faces) as tensors whose
    winding it flips itself. Cells it never evaluated are NaN: they're outside."""

    def __init__(self, dtype=None):
        self.dtype = dtype

    def to(self, *_a, **_k):
        return self

    def __call__(self, sdf, deform=None, return_quads=False, normalize=False):
        import torch
        from skimage import measure

        vol = sdf.detach().float().cpu().numpy()
        vol = np.where(np.isfinite(vol), vol, max(float(np.nanmax(vol)) if np.isfinite(vol).any() else 1.0, 1e-3))
        if not (vol.min() < 0 < vol.max()):
            raise ValueError("no surface in the shape field")
        # inward-facing, like DiffDMC: TripoSG flips the winding itself
        v, f, _, _ = measure.marching_cubes(vol, 0.0, gradient_direction="ascent")
        if normalize:
            v = v / (np.array(vol.shape) - 1)
        return torch.from_numpy(v.astype(np.float32).copy()), torch.from_numpy(f.astype(np.int64).copy())


def _install_diso_shim() -> None:
    """TripoSG imports diso (CUDA-only marching cubes). Everything else in it is plain PyTorch."""
    try:
        import diso  # noqa: F401
        return
    except ImportError:
        pass
    sys.modules["diso"] = types.SimpleNamespace(DiffDMC=_DiffDMC, DiffMC=_DiffDMC)  # type: ignore


def triposg_dir() -> Path:
    return paths.home() / "vendor" / "TripoSG"


class LocalTripoSG:
    id = "triposg"
    label = "TripoSG (local)"
    kind = "local"
    note = "Free, MIT licence. Much better shapes than TripoSR, a few minutes on Apple Silicon; ~8 GB download."

    def available(self, ctx: EngineContext) -> tuple[bool, str]:
        why = missing("torch", "diffusers", "einops", "jaxtyping", "typeguard", "omegaconf")
        return (why is None, why or "")  # the code and model download on first use (Settings → Local AI models)

    def _pipe(self, ctx: EngineContext):
        if str(triposg_dir()) not in sys.path:
            sys.path.insert(0, str(triposg_dir()))
        _install_diso_shim()
        from triposg.pipelines.pipeline_triposg import TripoSGPipeline  # type: ignore

        device = ctx.device

        def make():
            import torch

            # half precision halves the memory on the GPU; the CPU needs full precision
            dtype = torch.float32 if device == "cpu" else torch.float16
            return TripoSGPipeline.from_pretrained("VAST-AI/TripoSG").to(device, dtype)

        return _cached(f"triposg:{device}", make)

    def warm(self, ctx: EngineContext) -> None:
        self._pipe(ctx)

    def reconstruct(self, inp: ReconstructInput, ctx: EngineContext, progress: Progress) -> dict:
        _ensure_models("triposg-code", progress, 0.0, 0.02)
        _ensure_models("triposg", progress, 0.0, 0.25)
        return _retry_on_oom(lambda: self._reconstruct(inp, ctx, progress))

    def _reconstruct(self, inp: ReconstructInput, ctx: EngineContext, progress: Progress) -> dict:
        import torch
        import trimesh

        device = ctx.device
        warm = is_loaded("triposg")
        if not warm:
            progress(None, "Loading TripoSG into memory (only the first time)")
        pipe = self._pipe(ctx)
        # the object on white, filling most of a square (what TripoSG was trained on); our own cut-out,
        # so TripoSG's background remover is never needed
        rgba = _rgba(inp)
        box = rgba.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox() or (0, 0, *rgba.size)
        obj = rgba.crop(box)
        side = int(max(obj.size) / 0.9)
        canvas = Image.new("RGB", (side, side), (255, 255, 255))
        canvas.paste(obj, ((side - obj.width) // 2, (side - obj.height) // 2), obj)
        image = canvas.resize((512, 512), Image.LANCZOS)
        progress(0.3, "Building the shape")
        with timing.stage("reconstruct_infer", engine=self.id, device=device, warm=warm), torch.no_grad():
            out = pipe(image=image, generator=torch.Generator("cpu").manual_seed(int(inp.seed)),
                       num_inference_steps=50, guidance_scale=7.0)
        verts, faces = out.samples[0]
        if verts is None or len(verts) == 0:
            raise EngineError("TripoSG couldn't find a shape in this picture.", "empty mesh",
                              steps=["Try again with another seed, or use another engine."])
        progress(0.85, "Cleaning up")
        mesh = trimesh.Trimesh(np.asarray(verts, np.float32), np.ascontiguousarray(faces), process=True)
        parts = mesh.split(only_watertight=False)
        if len(parts) > 1:  # floaters: keep the main body
            mesh = max(parts, key=lambda m: len(m.faces))
        trimesh.repair.fix_normals(mesh)
        mesh.export(inp.out)  # TripoSG already uses glTF y-up
        progress(1.0, "Done")
        return {"model": "VAST-AI/TripoSG", "device": device, "faces": int(len(mesh.faces))}


# ---- Hunyuan3D with transformers 5 ----------------------------------------------------------

# transformers 5 renamed DINOv2's weights (its own from_pretrained converts old checkpoints with these same
# rules: transformers.conversion_mapping). Hunyuan3D loads its image encoder with a plain load_state_dict,
# which skips that conversion, so its model file's old names come up "missing".
DINOV2_RENAMES = [
    ("attention.attention.query", "attention.q_proj"),
    ("attention.attention.key", "attention.k_proj"),
    ("attention.attention.value", "attention.v_proj"),
    ("attention.output.dense", "attention.o_proj"),
    ("mlp.weights_out.", "mlp.down_proj."),
    ("LayerNorm.gamma", "LayerNorm.weight"),
    ("LayerNorm.beta", "LayerNorm.bias"),
]


def convert_dinov2_keys(state_dict: dict) -> dict:
    """Old DINOv2 weight names → transformers 5 names, whatever comes before them. Identical maths:
    the old SwiGLU computed silu(first half) * second half of weights_in; the new one silu(gate) * up."""
    if not any(".attention.attention." in k or ".mlp.weights_in." in k or ".attention.output.dense." in k for k in state_dict):
        return state_dict
    out = {}
    for k, v in state_dict.items():
        if ".mlp.weights_in." in k:
            gate, up = v.chunk(2, dim=0)
            out[k.replace(".mlp.weights_in.", ".mlp.gate_proj.")] = gate
            out[k.replace(".mlp.weights_in.", ".mlp.up_proj.")] = up
            continue
        for old, new in DINOV2_RENAMES:
            k = k.replace(old, new)
        out[k] = v
    return out


def _hy3dgen_compat() -> None:
    """Let Hunyuan3D's image encoder load its (old-named) DINOv2 weights under transformers 5. Safe to call often."""
    try:
        from hy3dgen.shapegen.models import conditioner  # type: ignore
    except ImportError:
        return
    for name in ("SingleImageEncoder", "DualImageEncoder"):
        cls = getattr(conditioner, name, None)
        if cls is None or getattr(cls.load_state_dict, "_formstudio", False):
            continue
        original = cls.load_state_dict

        def load_state_dict(self, state_dict, *args, _original=original, **kwargs):
            if any(".attention.q_proj." in k for k in self.state_dict()):  # this transformers uses the new names
                state_dict = convert_dinov2_keys(dict(state_dict))
            return _original(self, state_dict, *args, **kwargs)

        load_state_dict._formstudio = True
        cls.load_state_dict = load_state_dict


def _hunyuan_load(make, family: str, device: str):
    """Load a Hunyuan3D pipeline; explain plainly when the installed libraries can't load it."""
    _hy3dgen_compat()
    try:
        return _cached(f"{family}:{device}", make)
    except (RuntimeError, KeyError, AttributeError, TypeError) as e:
        if "state_dict" not in str(e) and "has no attribute" not in str(e):
            raise
        raise EngineError(
            "Hunyuan3D couldn't load its model with the AI libraries installed on this Mac.",
            str(e)[:1500],
            steps=["Build the shape with another engine: TRELLIS, Meshy or Hunyuan3D (fal.ai) in the cloud, or TripoSR.",
                   "Send the details below to whoever set up Form Studio."],
        ) from e


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
        pipe = _hunyuan_load(make, "hunyuan", device)
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
        pipe = _hunyuan_load(make, "hunyuan-mv", device)

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
    elif reconstruct_engine == "triposg":
        eng = RECONSTRUCTORS["triposg"]
        if eng.available(ctx)[0] and ready(["triposg-code", "triposg"]) and (is_loaded("triposg") or would_fit("triposg")):
            eng.warm(ctx)
            done.append("triposg")
    return done
