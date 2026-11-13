"""Local AI models: what each local engine needs, with downloads you can see.

Each *pack* is the exact list of files one local engine needs from Hugging Face.
Downloading a pack goes file by file, so the app can show GB done, speed and
time left. Interrupted downloads continue where they stopped (Hugging Face
keeps the partial file). Deleting a pack removes only its own repositories
from the Hugging Face cache.
"""
from __future__ import annotations

import fnmatch
import json
import logging
import shutil
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import paths
from .engines.base import quality_of

log = logging.getLogger("formstudio.models")

Progress = Callable[[float | None, str], None]

GB = 1024**3

PACKS: dict[str, dict[str, Any]] = {
    "sdxl": {
        "label": "Restyle · SDXL",
        "used_by": "SDXL + style reference (local restyle)",
        "repos": {
            "stabilityai/stable-diffusion-xl-base-1.0": [
                "model_index.json", "scheduler/*", "tokenizer/*", "tokenizer_2/*", "*/config.json",
                "text_encoder/model.fp16.safetensors", "text_encoder_2/model.fp16.safetensors",
                "unet/diffusion_pytorch_model.fp16.safetensors", "vae/diffusion_pytorch_model.fp16.safetensors",
            ],
            "diffusers/controlnet-depth-sdxl-1.0-small": ["config.json", "diffusion_pytorch_model.safetensors"],
            "madebyollin/sdxl-vae-fp16-fix": ["config.json", "diffusion_pytorch_model.safetensors"],
            "h94/IP-Adapter": [
                "sdxl_models/ip-adapter_sdxl.safetensors",
                "sdxl_models/image_encoder/config.json", "sdxl_models/image_encoder/model.safetensors",
            ],
            "depth-anything/Depth-Anything-V2-Small-hf": ["config.json", "preprocessor_config.json", "model.safetensors"],
        },
    },
    "depth": {
        "label": "Relief depth · Depth Anything V2 (small)",
        "used_by": "Magnets, keychains, ornaments: raised and recessed features read from the picture",
        "repos": {
            "depth-anything/Depth-Anything-V2-Small-hf": ["config.json", "preprocessor_config.json", "model.safetensors"],
        },
    },
    "sdxl-turbo": {
        "label": "Lightest restyle · SDXL-Turbo",
        "used_by": "Lightest quality for the local restyle (8 GB Macs)",
        "repos": {
            "stabilityai/sdxl-turbo": [
                "model_index.json", "scheduler/*", "tokenizer/*", "tokenizer_2/*", "*/config.json",
                "text_encoder/model.fp16.safetensors", "text_encoder_2/model.fp16.safetensors",
                "unet/diffusion_pytorch_model.fp16.safetensors", "vae/diffusion_pytorch_model.fp16.safetensors",
            ],
        },
    },
    "sdxl-lightning": {
        "label": "Quick restyle · SDXL-Lightning",
        "used_by": "Quick quality for the local restyle (8 steps instead of 30)",
        "repos": {
            "ByteDance/SDXL-Lightning": ["sdxl_lightning_8step_lora.safetensors"],
        },
    },
    "triposr": {
        "label": "3D · TripoSR",
        "used_by": "TripoSR (local image → 3D)",
        "repos": {
            "stabilityai/TripoSR": ["config.yaml", "model.ckpt"],
            "facebook/dino-vitb16": ["config.json", "preprocessor_config.json", "pytorch_model.bin"],
        },
    },
    "hunyuan": {
        "label": "3D · Hunyuan3D-2 mini",
        "used_by": "Hunyuan3D-2 mini (local image → 3D, optional)",
        "repos": {
            "tencent/Hunyuan3D-2mini": [
                "hunyuan3d-dit-v2-mini/*.yaml", "hunyuan3d-dit-v2-mini/model.fp16.safetensors",
                "hunyuan3d-vae-v2-mini/*.yaml", "hunyuan3d-vae-v2-mini/model.fp16.safetensors",
            ],
        },
    },
    "hunyuan-mv": {
        "label": "3D · Hunyuan3D-2 multi-view",
        "used_by": "Hunyuan3D-2 multi-view (local, builds from 3 views; optional)",
        "repos": {
            "tencent/Hunyuan3D-2mv": ["hunyuan3d-dit-v2-mv/config.yaml", "hunyuan3d-dit-v2-mv/model.fp16.safetensors"],
        },
    },
    "mvadapter": {
        "label": "3 views · MV-Adapter",
        "used_by": "Free on this Mac view maker (front, side, back; optional, uses SDXL too)",
        "repos": {
            "huanngzh/mv-adapter": ["mvadapter_i2mv_sdxl.safetensors"],
        },
    },
}


# Engine downloads that don't come from Hugging Face. They're managed the same way.
ENGINE_PACKS: dict[str, dict[str, Any]] = {
    "matting": {
        "label": "Background remover",
        "used_by": "Cutting the object out of every photo",
        "bytes": 179 * 1024**2,
    },
    "triposr-code": {
        "label": "3D · TripoSR code",
        "used_by": "TripoSR (local image → 3D) program files",
        "bytes": 64 * 1024**2,
    },
}
TRIPOSR_GIT = "https://github.com/VAST-AI-Research/TripoSR.git"


def all_packs() -> list[str]:
    return list(ENGINE_PACKS) + list(PACKS)


def _u2net_dir() -> Path:
    import os

    return Path(os.environ.get("U2NET_HOME") or Path.home() / ".u2net").expanduser()


def _vendor_dir() -> Path:
    return paths.home() / "vendor" / "TripoSR"


def _engine_path(pack: str) -> Path:
    return _u2net_dir() if pack == "matting" else _vendor_dir()


def _engine_status(pack: str) -> dict[str, Any]:
    p = _engine_path(pack)
    on_disk = _dir_size(p)
    total = ENGINE_PACKS[pack]["bytes"]
    if pack == "matting":
        # rembg keeps its models in ~/.u2net or a subfolder of it, depending on the version
        ready = any(_safe_size(f) > 50 * 1024**2 for f in p.rglob("*.onnx")) if p.exists() else False
    else:
        ready = (p / "tsr").is_dir()
    state = "ready" if ready else ("partial" if on_disk else "missing")
    return {"status": state, "total_bytes": total, "done_bytes": total if ready else on_disk, "on_disk_bytes": on_disk}


def _matting_download() -> None:  # seam for tests
    from rembg import new_session  # downloads the model into ~/.u2net

    new_session("isnet-general-use")


def _triposr_download(dest: Path) -> None:  # seam for tests
    import subprocess

    dest.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["git", "clone", "--quiet", "--depth", "1", TRIPOSR_GIT, str(dest)], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "git clone failed")


def _engine_download(pack: str, state: "DownloadState", progress: Progress | None) -> None:
    label = ENGINE_PACKS[pack]["label"]
    total = ENGINE_PACKS[pack]["bytes"]
    state.total_bytes, state.status, state.error = total, "downloading", None
    done = threading.Event()
    err: list[BaseException] = []

    def work():
        try:
            if pack == "matting":
                _matting_download()
            else:
                shutil.rmtree(_vendor_dir(), ignore_errors=True)
                _triposr_download(_vendor_dir())
        except BaseException as e:  # reported below
            err.append(e)
        finally:
            done.set()

    threading.Thread(target=work, daemon=True).start()
    start = time.time()
    while not done.wait(0.5):
        if state.stop.is_set():
            state.status, state.message = "stopped", "Stopped. Press Download to try again."
            raise DownloadStopped()
        size = min(_dir_size(_engine_path(pack)), total)
        span = time.time() - start
        state.done_bytes = size
        state.speed = size / span if span > 3 and size > 1024**2 else 0.0
        state.eta_s = (total - size) / state.speed if state.speed else None
        state.message = f"Downloading {label}: {_fmt_pair(size, total)}" + (
            f" · {state.speed / 1024**2:.1f} MB/s · {_fmt_eta(state.eta_s or 0)}" if state.speed else "")
        if progress:
            progress(size / total, state.message)
    if err:
        state.status = "error"
        state.error = f"{label} couldn't be downloaded ({err[0].__class__.__name__}: {err[0]}). Press Download to try again."
        raise DownloadFailed(state.error)
    state.status, state.done_bytes, state.eta_s = "done", total, 0
    state.message = f"{label} is ready"
    if progress:
        progress(1.0, state.message)


class DownloadStopped(Exception):
    pass


class DownloadFailed(RuntimeError):
    pass


# ---- the Hugging Face side (small seams so tests can fake them) --------------------

def hub_cache() -> Path:
    from huggingface_hub import constants

    return Path(constants.HF_HUB_CACHE)


def _list_remote(repo: str) -> list[tuple[str, int]]:
    from huggingface_hub import HfApi

    info = HfApi().model_info(repo, files_metadata=True)
    return [(s.rfilename, int(s.size or 0)) for s in info.siblings]


def _download_file(repo: str, filename: str, tqdm_class) -> str:
    from huggingface_hub import hf_hub_download

    return hf_hub_download(repo, filename, tqdm_class=tqdm_class)


def _is_cached(repo: str, filename: str) -> bool:
    from huggingface_hub import try_to_load_from_cache

    p = try_to_load_from_cache(repo, filename)
    return isinstance(p, str) and Path(p).exists()


def _repo_dir(repo: str) -> Path:
    return hub_cache() / ("models--" + repo.replace("/", "--"))


# ---- file lists and sizes -----------------------------------------------------------

def _sizes_file() -> Path:
    return paths.home() / "model_sizes.json"


def _load_sizes() -> dict[str, Any]:
    try:
        return json.loads(_sizes_file().read_text())
    except (OSError, ValueError):
        return {}


_failed_at: dict[str, float] = {}


def files_for(pack: str, refresh: bool = False) -> dict[str, list[tuple[str, int]]] | None:
    """{repo: [(file, bytes), …]} for a pack, from Hugging Face (cached on disk for offline use)."""
    cache = _load_sizes()
    if not refresh and pack in cache:
        return {repo: [tuple(f) for f in files] for repo, files in cache[pack].items()}
    if not refresh and time.time() - _failed_at.get(pack, 0) < 60:
        return None
    out: dict[str, list[tuple[str, int]]] = {}
    try:
        for repo, patterns in PACKS[pack]["repos"].items():
            out[repo] = [(f, s) for f, s in _list_remote(repo) if any(fnmatch.fnmatch(f, p) for p in patterns)]
    except Exception as e:  # offline: sizes unknown until we can reach Hugging Face
        log.warning("couldn't list %s files: %s", pack, e)
        _failed_at[pack] = time.time()
        return None
    cache[pack] = out
    _sizes_file().write_text(json.dumps(cache, indent=1))
    return out


def _incomplete_bytes(repo: str) -> int:
    blobs = _repo_dir(repo) / "blobs"
    if not blobs.exists():
        return 0
    total = 0
    for f in blobs.glob("*.incomplete"):
        try:
            total += f.stat().st_size
        except OSError:
            pass
    return total


def _linked_blobs(repo_dir: Path) -> set[Path]:
    """Data files a repo folder links to outside itself.

    Newer Hugging Face versions keep model data in a shared store under the hub cache
    and put only links in each model's folder, so a folder's own size says little.
    """
    out: set[Path] = set()
    if not repo_dir.exists():
        return out
    for f in repo_dir.rglob("*"):
        if f.is_symlink():
            try:
                target = f.resolve()
            except OSError:
                continue
            if target.is_file() and repo_dir not in target.parents:
                out.add(target)
    return out


def _repo_bytes(repo: str) -> int:
    d = _repo_dir(repo)
    return _dir_size(d) + sum(_safe_size(b) for b in _linked_blobs(d))


def _safe_size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def status(pack: str) -> dict[str, Any]:
    if pack in ENGINE_PACKS:
        return _engine_status(pack)
    files = files_for(pack)
    on_disk = sum(_repo_bytes(r) for r in PACKS[pack]["repos"])
    if files is None:
        return {"status": "unknown", "total_bytes": None, "done_bytes": on_disk, "on_disk_bytes": on_disk}
    total = sum(s for fl in files.values() for _, s in fl)
    done = sum(s for repo, fl in files.items() for f, s in fl if _is_cached(repo, f))
    partial = sum(_incomplete_bytes(r) for r in files)
    state = "ready" if done >= total else ("partial" if done + partial > 0 else "missing")
    return {"status": state, "total_bytes": total, "done_bytes": done + partial, "on_disk_bytes": on_disk}


def _dir_size(p: Path) -> int:
    if not p.exists():
        return 0
    total = 0
    for f in p.rglob("*"):
        try:
            if f.is_file() and not f.is_symlink():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def clear_chunk_cache() -> int:
    """Hugging Face's xet chunk cache: copies of downloaded data, shared by every model."""
    d = hub_cache().parent / "xet"
    freed = _dir_size(d)
    for sub in ("chunk-cache", "shard-cache", "chunk_cache", "shard_cache"):
        shutil.rmtree(d / sub, ignore_errors=True)
    for child in d.glob("*"):
        if child.is_dir() and child.name != "logs":
            shutil.rmtree(child, ignore_errors=True)
    return freed - _dir_size(d)


def _prune_empty(root: Path) -> None:
    if not root.exists():
        return
    for d in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass


def delete(pack: str) -> int:
    """Remove a pack's files (Hugging Face repositories, or an engine's folder). Returns bytes freed."""
    if pack in ENGINE_PACKS:
        p = _engine_path(pack)
        freed = _dir_size(p)
        shutil.rmtree(p, ignore_errors=True)
        if pack == "matting":
            try:
                from .core import ingest

                ingest._rembg_session.cache_clear()
            except Exception:
                pass
        return freed
    mine = {_repo_dir(r) for r in PACKS[pack]["repos"]}
    # shared data files still used by any other model must stay
    in_use: set[Path] = set()
    for other in hub_cache().glob("models--*"):
        if other not in mine:
            in_use |= _linked_blobs(other)
    freed = 0
    for d in mine:
        for blob in _linked_blobs(d) - in_use:
            freed += _safe_size(blob)
            blob.unlink(missing_ok=True)
        freed += _dir_size(d)
        shutil.rmtree(d, ignore_errors=True)
    _prune_empty(hub_cache() / "blobs")
    try:
        from .engines.local import unload_all

        unload_all()
    except Exception:
        pass
    log.info("deleted model pack %s (%.1f GB)", pack, freed / GB)
    return freed


# ---- downloading -------------------------------------------------------------------

def _fmt_gb(n: float) -> str:
    return f"{n / GB:.1f} GB" if n >= 0.1 * GB else f"{n / 1024**2:.0f} MB"


def _fmt_pair(done: float, total: float) -> str:
    """'41 of 179 MB' or '3.2 of 11.6 GB': both numbers in the total's unit."""
    if total >= GB:
        return f"{done / GB:.1f} of {total / GB:.1f} GB"
    return f"{done / 1024**2:.0f} of {total / 1024**2:.0f} MB"


def _fmt_eta(seconds: float) -> str:
    if seconds < 60:
        return "less than a minute left"
    if seconds < 3600:
        return f"about {round(seconds / 60)} min left"
    return f"about {seconds / 3600:.1f} h left"


@dataclass
class DownloadState:
    pack: str
    status: str = "idle"  # idle | downloading | done | error | stopped
    done_bytes: int = 0
    total_bytes: int = 0
    speed: float = 0.0  # bytes per second
    eta_s: float | None = None
    message: str = ""
    error: str | None = None
    stop: threading.Event = field(default_factory=threading.Event, repr=False)

    def public(self) -> dict[str, Any]:
        return {"status": self.status, "done_bytes": self.done_bytes, "total_bytes": self.total_bytes,
                "speed": round(self.speed), "eta_s": None if self.eta_s is None else round(self.eta_s),
                "message": self.message, "error": self.error}


RETRY_WAITS = (5, 15, 45)


def download(pack: str, progress: Progress | None = None, state: DownloadState | None = None,
             retry_waits: tuple[float, ...] = RETRY_WAITS) -> None:
    """Download every missing file of a pack, reporting bytes, speed and time left."""
    from tqdm.auto import tqdm

    state = state or DownloadState(pack)
    if pack in ENGINE_PACKS:
        return _engine_download(pack, state, progress)
    label = PACKS[pack]["label"].split("·")[-1].strip()
    files = files_for(pack, refresh=True)
    if files is None:
        raise DownloadFailed("Couldn't reach Hugging Face to list the model files. Check your internet connection.")
    todo = [(repo, f, s) for repo, fl in files.items() for f, s in fl if not _is_cached(repo, f)]
    total = sum(s for fl in files.values() for _, s in fl)
    done_before = total - sum(s for _, _, s in todo)
    state.total_bytes, state.done_bytes, state.status, state.error = total, done_before, "downloading", None
    # bytes of the file being downloaded now. Hugging Face may report one file through
    # more than one progress bar, so take the furthest bar rather than adding them up.
    current = {"base": 0, "size": 0, "bars": {}}
    samples: deque[tuple[float, int]] = deque()

    def current_bytes() -> int:
        bars = current["bars"].values()
        return current["base"] + min(current["size"], max(bars, default=0))

    def report() -> None:
        now = time.time()
        done = done_before + current_bytes()
        samples.append((now, done))
        while len(samples) > 2 and now - samples[0][0] > 10:
            samples.popleft()
        span = samples[-1][0] - samples[0][0]
        moved = samples[-1][1] - samples[0][1]
        # only trust the speed after a few seconds and a real amount of data
        state.speed = moved / span if span >= 3 and moved >= 1024**2 else 0.0
        state.done_bytes = min(done, total)
        left = total - state.done_bytes
        state.eta_s = left / state.speed if state.speed else None
        parts = [f"Downloading {label}: {_fmt_pair(state.done_bytes, total)}"]
        if state.speed:
            parts.append(f"{state.speed / 1024**2:.1f} MB/s")
            parts.append(_fmt_eta(state.eta_s or 0))
        else:
            parts.append("measuring speed…")
        state.message = " · ".join(parts)
        if progress:
            progress(state.done_bytes / total if total else None, state.message)

    class Bar(tqdm):  # hf_hub_download reports bytes through this
        def __init__(self, *a, **kw):
            kw["disable"] = True
            super().__init__(*a, **kw)

        def update(self, n=1):
            if state.stop.is_set():
                raise DownloadStopped()
            current["bars"][id(self)] = current["bars"].get(id(self), 0) + int(n or 0)
            report()

    report()
    def stopped() -> DownloadStopped:
        state.status = "stopped"
        state.message = "Stopped. Press Download to continue where it stopped."
        return DownloadStopped()

    for repo, filename, size in todo:
        base = current["base"]
        attempt = 0
        while True:
            if state.stop.is_set():
                raise stopped()
            current.update(base=base, size=size, bars={})
            try:
                _download_file(repo, filename, Bar)
                break
            except DownloadStopped:
                raise stopped()
            except Exception as e:  # network trouble: try again, keeping the partial file
                log.warning("download %s/%s failed (attempt %d): %s", repo, filename, attempt + 1, e)
                if attempt >= len(retry_waits):
                    state.status = "error"
                    state.error = f"Download interrupted ({e.__class__.__name__}). Press Download again to continue where it stopped."
                    raise DownloadFailed(state.error) from e
                wait = retry_waits[attempt]
                attempt += 1
                state.message = f"Connection problem; trying again in {wait:g} s…"
                if progress:
                    progress(None, state.message)
                state.stop.wait(wait)
        current.update(base=base + size, size=0, bars={})
        report()
    state.status, state.done_bytes, state.eta_s = "done", total, 0
    state.message = f"{label} is ready ({_fmt_gb(total)})"
    if progress:
        progress(1.0, state.message)
    log.info("model pack %s ready (%.1f GB)", pack, total / GB)


# ---- one download at a time per pack, in the background ------------------------------

def packs_needed(settings: dict[str, Any]) -> list[str]:
    """What the current default engines need, in download order."""
    engines = settings.get("engines") or {}
    need = ["matting"]
    if engines.get("stylize") == "local-sdxl":
        q = quality_of(settings)
        need += ["sdxl-turbo"] if q == "lightest" else ["sdxl"] + (["sdxl-lightning"] if q == "quick" else [])
    if engines.get("reconstruct") == "triposr":
        need += ["triposr-code", "triposr"]
    elif engines.get("reconstruct") == "hunyuan-mini":
        need.append("hunyuan")
    elif engines.get("reconstruct") == "hunyuan-mv":
        need.append("hunyuan-mv")
    if engines.get("views") == "local-views":
        need += [p for p in ("sdxl", "mvadapter") if p not in need]
    return need


@dataclass
class Queue:
    packs: list[str]
    index: int = 0
    status: str = "running"  # running | done | error | stopped
    error: str | None = None

    def public(self, states: dict[str, DownloadState]) -> dict[str, Any]:
        current = self.packs[self.index] if self.index < len(self.packs) else None
        msg = ""
        if self.status == "running" and current:
            st = states.get(current)
            label = (PACKS.get(current) or ENGINE_PACKS[current])["label"]
            msg = f"Step {self.index + 1} of {len(self.packs)} · {label}" + (f" · {st.message}" if st and st.message else "")
        elif self.status == "done":
            msg = "Start over finished: everything is downloaded fresh."
        return {"packs": self.packs, "step": min(self.index + 1, len(self.packs)), "total": len(self.packs),
                "current": current, "status": self.status, "message": msg, "error": self.error}


class Downloads:
    def __init__(self) -> None:
        self.states: dict[str, DownloadState] = {}
        self.lock = threading.Lock()
        self.queue: Queue | None = None

    def running(self, pack: str) -> bool:
        s = self.states.get(pack)
        return bool(s and s.status == "downloading")

    def any_running(self) -> bool:
        return any(self.running(p) for p in all_packs()) or bool(self.queue and self.queue.status == "running")

    def start(self, pack: str, fresh: bool = False, wait: bool = False) -> DownloadState:
        with self.lock:
            if self.running(pack):
                return self.states[pack]
            if fresh:
                delete(pack)
            state = DownloadState(pack, status="downloading", message="Starting download…")
            self.states[pack] = state

        def run():
            try:
                download(pack, state=state)
            except (DownloadStopped, DownloadFailed):
                pass
            except Exception as e:
                state.status, state.error = "error", str(e)
                log.exception("download %s failed", pack)

        if wait:
            run()
        else:
            threading.Thread(target=run, name=f"download-{pack}", daemon=True).start()
        return state

    def stop(self, pack: str) -> None:
        s = self.states.get(pack)
        if s:
            s.stop.set()

    def stop_all(self) -> None:
        if self.queue and self.queue.status == "running":
            self.queue.status = "stopped"
        for s in self.states.values():
            s.stop.set()
        for _ in range(100):  # give running downloads a moment to notice
            if not any(s.status == "downloading" for s in self.states.values()):
                break
            time.sleep(0.05)

    def delete_everything(self) -> int:
        """Every model and engine download, plus Hugging Face's chunk cache."""
        self.stop_all()
        freed = sum(delete(p) for p in all_packs()) + clear_chunk_cache()
        self.states.clear()
        return freed

    def start_over(self, settings: dict[str, Any]) -> Queue:
        """Delete everything, then download what the current engines need, one after another."""
        freed = self.delete_everything()
        log.info("start over: freed %.1f GB", freed / GB)
        q = Queue(packs=packs_needed(settings))
        self.queue = q

        def run():
            for i, pack in enumerate(q.packs):
                if q.status != "running":
                    return
                q.index = i
                st = self.start(pack, wait=True)
                if st.status != "done":
                    q.status = "stopped" if st.status == "stopped" else "error"
                    q.error = st.error or st.message
                    return
            q.index = len(q.packs)
            q.status = "done"

        threading.Thread(target=run, name="start-over", daemon=True).start()
        return q

    def ensure(self, pack: str, progress: Progress) -> None:
        """Called by the local engines before loading: download first if needed, with progress."""
        if status(pack)["status"] == "ready":
            return
        state = self.states.get(pack) if self.running(pack) else self.start(pack)
        while state.status == "downloading":
            progress(state.done_bytes / state.total_bytes if state.total_bytes else None, state.message)
            time.sleep(0.5)
        if state.status != "done":
            raise RuntimeError(state.error or state.message or "The model download didn't finish.")


downloads = Downloads()


def overview() -> dict[str, Any]:
    rows = []
    for pack in all_packs():
        spec = PACKS.get(pack) or ENGINE_PACKS[pack]
        st = status(pack)
        dl = downloads.states.get(pack)
        rows.append({"id": pack, "label": spec["label"], "used_by": spec["used_by"], "engine": pack in ENGINE_PACKS,
                     **st, "download": dl.public() if dl else None})
    q = downloads.queue
    return {"packs": rows, "queue": q.public(downloads.states) if q else None}
