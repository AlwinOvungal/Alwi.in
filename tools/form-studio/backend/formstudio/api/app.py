"""Form Studio's local server: the REST API, live progress, and the web UI."""
from __future__ import annotations

import asyncio
import io
import json
import logging
import platform
import re
import time
import os
import subprocess
import tempfile
import threading
from collections import defaultdict
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__, paths, pipeline
from ..core.job import PRINT_DIR, Job
from ..core import style as style_mod
from ..core.style import get_style, list_styles
from ..engines import RECONSTRUCTORS, STYLIZERS, describe
from ..engines.cloud import FalClient
from . import settings as settings_mod
from ..core import timing
from ..engines.base import quality_of
from .worker import BACKGROUND, Bus, Worker

log = logging.getLogger(__name__)

bus = Bus()
worker = Worker(bus)


@asynccontextmanager
async def lifespan(app: FastAPI):
    bus.bind(asyncio.get_running_loop())
    try:
        _warm_soon()  # get the default local engines ready while you pick a photo
    except Exception as e:  # never stop the app from opening
        log.warning("warm-up skipped: %s", e)
    yield


app = FastAPI(title="Form Studio", version=__version__, lifespan=lifespan)


# ---- helpers -------------------------------------------------------------------

_job_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)


@contextmanager
def _locked(job_id: str):
    """One change at a time per object: sync endpoints run in parallel threads."""
    if worker.busy(job_id):
        raise HTTPException(409, "Wait for the current step to finish.")
    with _job_locks[job_id]:
        yield _job(job_id)  # load inside the lock so we never save a stale manifest


def _job(job_id: str) -> Job:
    try:
        return Job.load(job_id)
    except KeyError:
        raise HTTPException(404, "Object not found")


def _view(job: Job) -> dict[str, Any]:
    d = job.to_dict()
    t = worker.tasks.get(job.id)
    d["task"] = t.public() if t else None
    return d


def _publish_state(job: Job) -> None:
    bus.publish(job.id, {"type": "job", "job": _view(Job.load(job.id))})


def _queue(job: Job, kind: str, fn, *, meta: dict[str, Any] | None = None, eta_s: float | None = None,
           replace: bool = False) -> dict[str, Any]:
    def run(progress):
        try:
            with _job_locks[job.id]:
                fn(progress)
        finally:
            _publish_state(job)

    try:
        worker.submit(job.id, kind, run, meta=meta, eta_s=eta_s, replace=replace)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return _view(job)


WARM = "_warm"


def _warm_soon(stylize: bool = True, reconstruct: bool = True) -> None:
    """Load the default local engines in the background (low priority, only what's downloaded and fits)."""
    s = settings_mod.load()
    if not s.get("warm_up", True):
        return
    st = s["engines"]["stylize"] if stylize else None
    rc = s["engines"]["reconstruct"] if reconstruct else None
    if st != "local-sdxl" and rc != "triposr":
        return
    if worker.busy(WARM):
        return
    from ..engines import local

    ctx = settings_mod.context()
    try:
        worker.submit(WARM, "warm", lambda p: local.warm(ctx, st, rc), priority=BACKGROUND)
    except RuntimeError:
        pass


def _eta(kind: str, engine: str, n: int = 1, quality: str | None = None) -> float | None:
    """Expected seconds for a task, from past runs on this Mac (None until there's history)."""
    per = timing.estimate(f"task.{kind}", per="n" if kind == "stylize" else None, engine=engine, quality=quality)
    return per * n if per else None


def _bad(e: Exception) -> HTTPException:
    return HTTPException(400, str(e))


# ---- meta ------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "version": __version__, "platform": platform.system()}


# ---- styles -------------------------------------------------------------------------

def _style(style_id: str):
    try:
        return get_style(style_id)
    except KeyError:
        raise HTTPException(404, "Unknown style")


def _style_call(fn, *args):
    try:
        return fn(*args).detail()
    except KeyError:
        raise HTTPException(404, "Unknown style")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except (ValueError, IndexError) as e:
        raise HTTPException(400, str(e))


@app.get("/api/styles")
def styles() -> list[dict[str, Any]]:
    return [s.summary() for s in list_styles()]


@app.post("/api/styles")
def create_style(body: style_mod.StyleEdit) -> dict[str, Any]:
    return _style_call(style_mod.create_style, body)


@app.get("/api/styles/{style_id}")
def style_detail(style_id: str) -> dict[str, Any]:
    return _style(style_id).detail()


@app.put("/api/styles/{style_id}")
def update_style(style_id: str, body: style_mod.StyleEdit) -> dict[str, Any]:
    return _style_call(style_mod.update_style, style_id, body)


@app.delete("/api/styles/{style_id}")
def delete_style(style_id: str) -> dict[str, bool]:
    try:
        style_mod.delete_style(style_id)
    except KeyError:
        raise HTTPException(404, "Unknown style")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    return {"ok": True}


@app.post("/api/styles/{style_id}/duplicate")
def duplicate_style(style_id: str) -> dict[str, Any]:
    return _style_call(style_mod.duplicate_style, style_id)


@app.post("/api/styles/{style_id}/references")
def add_style_reference(style_id: str, image: list[UploadFile] = File(...)) -> dict[str, Any]:
    """One or several pictures (form field "image", repeated)."""
    return _style_call(style_mod.add_references, style_id, [f.file.read() for f in image])


@app.post("/api/styles/{style_id}/references/{index}/first")
def first_style_reference(style_id: str, index: int) -> dict[str, Any]:
    return _style_call(style_mod.make_reference_first, style_id, index)


@app.post("/api/styles/suggest-colours")
def suggest_colours(style: str | None = None, image: list[UploadFile] | None = File(None)) -> dict[str, Any]:
    """The two print colours, picked from pictures (uploaded, or a style's references). Free, no AI."""
    from PIL import Image as PILImage

    from ..core import colours

    pics: list[Any] = []
    if style:
        pics += _style(style).reference_paths
    for f in image or []:
        try:
            pics.append(PILImage.open(io.BytesIO(f.file.read())))
        except Exception:
            raise HTTPException(400, "One of the files isn't an image.")
    if not pics:
        raise HTTPException(400, "Add a reference picture first.")
    return colours.suggest(pics)


@app.delete("/api/styles/{style_id}/references/{index}")
def remove_style_reference(style_id: str, index: int) -> dict[str, Any]:
    return _style_call(style_mod.remove_reference, style_id, index)


@app.get("/api/styles/{style_id}/references/{index}")
def style_reference_n(style_id: str, index: int):
    refs = _style(style_id).reference_paths
    if not 0 <= index < len(refs):
        raise HTTPException(404, "No such reference image")
    return FileResponse(refs[index], headers={"Cache-Control": "no-store"})


@app.get("/api/styles/{style_id}/reference")
def style_reference(style_id: str):
    return style_reference_n(style_id, 0)


class DraftBody(BaseModel):
    notes: str = ""


@app.post("/api/styles/{style_id}/draft")
def draft_style(style_id: str, body: DraftBody) -> dict[str, Any]:
    """Claude reads the style's reference images and drafts its words, colours and shape settings."""
    from .. import claude

    style = _style(style_id)
    ctx = settings_mod.context()
    try:
        draft = claude.draft_style(ctx.secrets.get("anthropic"), (ctx.settings.get("claude") or {}).get("model"),
                                   style.reference_paths, body.notes)
    except claude.ClaudeError as e:
        raise HTTPException(400, str(e))
    return draft.model_dump()


@app.get("/api/engines")
def engines() -> dict[str, Any]:
    ctx = settings_mod.context()
    return {
        "stylize": [describe(e, ctx) for e in STYLIZERS.values()],
        "reconstruct": [describe(e, ctx) for e in RECONSTRUCTORS.values()],
        "device": ctx.device,
    }


# ---- settings -----------------------------------------------------------------------

class SettingsUpdate(BaseModel):
    engines: dict[str, str] | None = None
    device: str | None = None
    sdxl_quality: Literal["best", "quick", "lightest"] | None = None
    keep_loaded: Literal["auto", "one"] | None = None
    warm_up: bool | None = None
    candidates: int | None = None
    export_formats: list[str] | None = None
    fal: dict[str, Any] | None = None
    claude: dict[str, Any] | None = None
    openai: dict[str, Any] | None = None
    meshy: dict[str, Any] | None = None
    slicer: str | None = None
    fal_key: str | None = None  # "" removes it
    anthropic_key: str | None = None
    openai_key: str | None = None
    meshy_key: str | None = None


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return settings_mod.public(settings_mod.load())


@app.put("/api/settings")
def put_settings(body: SettingsUpdate) -> dict[str, Any]:
    update = body.model_dump(exclude_none=True)
    for field, name in (("fal_key", "fal"), ("anthropic_key", "anthropic"), ("openai_key", "openai"), ("meshy_key", "meshy")):
        key = update.pop(field, None)
        if key is not None:
            settings_mod.set_secret(name, key.strip() or None)
    for section in ("fal", "claude", "openai", "meshy"):
        if section in update:
            update[section] = {k: v for k, v in update[section].items() if k not in ("has_key", "key_hint")}
    if "engines" in update:
        for step, eid in update["engines"].items():
            reg = STYLIZERS if step == "stylize" else RECONSTRUCTORS if step == "reconstruct" else None
            if reg is None or eid not in reg:
                raise HTTPException(400, f"Unknown engine {eid} for {step}")
    if "candidates" in update:
        update["candidates"] = max(1, min(8, update["candidates"]))
    saved = settings_mod.save(update)
    if update.get("keep_loaded") == "one":
        from ..engines import local

        local.configure(saved)
    if {"engines", "sdxl_quality", "warm_up", "keep_loaded"} & update.keys():
        _warm_soon()
    return settings_mod.public(saved)


@app.post("/api/settings/test-key")
def test_key(service: str = "fal") -> dict[str, Any]:
    if service not in settings_mod.SECRETS:
        raise HTTPException(400, "Unknown service")
    key = settings_mod.get_secret(service)
    if not key:
        return {"ok": False, "message": "No key saved."}
    if service == "anthropic":
        import anthropic

        try:
            anthropic.Anthropic(api_key=key, max_retries=0, timeout=20).models.list(limit=1)
        except anthropic.AuthenticationError:
            return {"ok": False, "message": "Anthropic rejected this key."}
        except anthropic.APIError as e:
            return {"ok": False, "message": f"Couldn't check the key: {e.__class__.__name__}"}
        return {"ok": True, "message": "Key works."}
    if service == "openai":
        model = (settings_mod.load().get("openai") or {}).get("model") or "gpt-image-2.5-sunburst"
        try:
            r = httpx.get(f"https://api.openai.com/v1/models/{model}", headers={"Authorization": f"Bearer {key}"}, timeout=20)
        except httpx.HTTPError as e:
            return {"ok": False, "message": f"Couldn't reach OpenAI: {e.__class__.__name__}"}
        if r.status_code == 401:
            return {"ok": False, "message": "OpenAI rejected this key."}
        if r.status_code in (403, 404):
            return {"ok": False, "message": f"The key works, but it can't use {model}. Pick another image model below, or check the key's project permissions."}
        if r.status_code >= 400:
            return {"ok": False, "message": f"Unexpected answer from OpenAI ({r.status_code})."}
        return {"ok": True, "message": f"Key works and can use {model}. OpenAI only reveals your image allowance (your usage tier) when you generate; if that fails, the message says exactly what to fix."}
    if service in ("openai", "meshy"):
        url, auth = {
            "openai": ("https://api.openai.com/v1/models", f"Bearer {key}"),
            "meshy": ("https://api.meshy.ai/openapi/v1/image-to-3d?page_size=1", f"Bearer {key}"),
        }[service]
        try:
            r = httpx.get(url, headers={"Authorization": auth}, timeout=20)
        except httpx.HTTPError as e:
            return {"ok": False, "message": f"Couldn't reach {service}: {e.__class__.__name__}"}
        if r.status_code in (401, 403):
            return {"ok": False, "message": "This key was rejected."}
        if r.status_code == 402:
            return {"ok": False, "message": "Key works, but this Meshy account can't use the API (needs Pro or credits)."}
        return {"ok": r.status_code < 400, "message": "Key works." if r.status_code < 400 else f"Unexpected answer ({r.status_code})."}
    try:
        ok = FalClient(key).check()
    except Exception as e:
        return {"ok": False, "message": f"Couldn't reach fal.ai: {e}"}
    return {"ok": ok, "message": "Key works." if ok else "fal.ai rejected this key."}


# ---- speed test --------------------------------------------------------------------

BENCH = "_bench"


class BenchBody(BaseModel):
    size: Literal["quick", "full"] = "quick"  # quick: 1 photo × 2 runs; full: 3 photos × 3 runs
    compare: bool = True  # also run "before" and Quick quality


def _bench_state() -> dict[str, Any]:
    from ..core import bench

    t = worker.tasks.get(BENCH)
    return {"task": t.public() if t else None, "latest": bench.latest(), "photos": len(bench.pick_photos(3))}


@app.get("/api/bench")
def bench_state() -> dict[str, Any]:
    return _bench_state()


@app.post("/api/bench")
def start_bench(body: BenchBody) -> dict[str, Any]:
    from ..core import bench

    photos = bench.pick_photos(1 if body.size == "quick" else 3)
    if not photos:
        raise HTTPException(400, "Add at least one object first: the speed test uses your photos.")
    s = settings_mod.load()
    ctx = settings_mod.context()
    variants = ["before", "best", "quick"] if body.compare else ["best"]
    runs = 2 if body.size == "quick" else 3

    def run(progress):
        bench.run(ctx, photos, style_id="clay-minimal", stylize_engine=s["engines"]["stylize"],
                  reconstruct_engine=s["engines"]["reconstruct"], variants=variants, runs=runs,
                  options=4, progress=progress)

    try:
        worker.submit(BENCH, "bench", run, meta={"kind": body.size})
    except RuntimeError:
        raise HTTPException(409, "A speed test is already running.")
    return _bench_state()


@app.post("/api/bench/cancel")
def cancel_bench() -> dict[str, Any]:
    worker.cancel(BENCH)
    return _bench_state()


@app.get("/api/bench/files/{name}")
def bench_file(name: str):
    from ..core import bench

    report = bench.latest()
    if not report or not re.fullmatch(r"[a-z0-9_]+\.(png|md|json)", name):
        raise HTTPException(404, "Not found")
    f = Path(report["folder"]) / name
    if not f.exists():
        raise HTTPException(404, "Not found")
    return FileResponse(f)


# ---- local AI models and engines ---------------------------------------------------

def _pack(pack: str) -> str:
    from .. import models

    if pack not in models.all_packs():
        raise HTTPException(404, "Unknown model pack")
    return pack


def _generation_running() -> bool:
    return any(t.status in ("queued", "running") for t in worker.tasks.values())


def _no_generation(what: str = "deleting models") -> None:
    if _generation_running():
        raise HTTPException(409, f"Wait for the current step to finish before {what}.")


@app.get("/api/models")
def list_models() -> dict[str, Any]:
    from .. import models

    return models.overview()


@app.post("/api/models/start-over", status_code=202)
def start_over() -> dict[str, Any]:
    """Delete every model and engine download, then download what the current engines need."""
    from .. import models

    _no_generation("starting over")
    q = models.downloads.start_over(settings_mod.load())
    _size_cache["data"] = None
    return q.public(models.downloads.states)


@app.post("/api/models/{pack}/download", status_code=202)
def download_models(pack: str, fresh: bool = False) -> dict[str, Any]:
    from .. import models

    _pack(pack)
    if fresh:
        _no_generation()
        models.clear_chunk_cache()  # a truly fresh download: no reused chunks either
    return models.downloads.start(pack, fresh=fresh).public()


@app.post("/api/models/{pack}/stop")
def stop_models(pack: str) -> dict[str, bool]:
    from .. import models

    models.downloads.stop(_pack(pack))
    return {"ok": True}


@app.delete("/api/models/{pack}")
def delete_models(pack: str) -> dict[str, Any]:
    from .. import models

    _pack(pack)
    if models.downloads.running(pack):
        raise HTTPException(409, "Stop the download first.")
    _no_generation()
    freed = models.delete(pack)
    models.downloads.states.pop(pack, None)
    _size_cache["data"] = None
    return {"ok": True, "freed_bytes": freed}


@app.delete("/api/models")
def delete_all_models() -> dict[str, Any]:
    """Every model and engine download (stops downloads in progress)."""
    from .. import models

    _no_generation()
    freed = models.downloads.delete_everything()
    models.downloads.queue = None
    _size_cache["data"] = None
    return {"ok": True, "freed_bytes": freed}


REINSTALL_CMD = "cd {root} && ./scripts/setup-mac.sh --reinstall-ai"


@app.post("/api/models/reinstall-libraries")
def reinstall_libraries() -> dict[str, Any]:
    """The AI libraries live in the Python environment this app runs from, so a Terminal script reinstalls them."""
    import shlex

    cmd = REINSTALL_CMD.format(root=shlex.quote(str(paths.PROJECT_DIR)))
    opened = False
    if platform.system() == "Darwin":
        script = f'tell application "Terminal" to do script "{cmd}"\ntell application "Terminal" to activate'
        opened = subprocess.run(["osascript", "-e", script], capture_output=True).returncode == 0
    return {"opened": opened, "command": cmd}


# ---- storage -----------------------------------------------------------------------

_size_cache: dict[str, Any] = {"at": 0.0, "data": None}


def _folder_size(path: Path, skip: frozenset[str] = frozenset()) -> int:
    """Total size of a folder, without following symlinks; unreadable parts are skipped."""
    if path.is_file():
        return path.stat().st_size
    total, stack = 0, [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for e in entries:
                    try:
                        if e.is_symlink():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            if e.name not in skip:
                                stack.append(Path(e.path))
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


@app.get("/api/storage")
def storage(refresh: bool = False) -> dict[str, Any]:
    now = time.time()
    if not refresh and _size_cache["data"] and now - _size_cache["at"] < 60:
        return _size_cache["data"]
    items = []
    for loc in paths.storage_locations():
        p = Path(loc["path"])
        exists = p.exists()
        # the code folder holds the Python environment, which has its own row
        skip = frozenset({".venv", "node_modules"}) if loc["id"] == "code" else frozenset()
        items.append({**loc, "exists": exists, "bytes": _folder_size(p, skip) if exists else 0})
    data = {
        "home": str(Path.home()),
        "items": items,
        "total_models_bytes": sum(i["bytes"] for i in items if i["id"] in ("hf", "rembg")),
    }
    _size_cache.update(at=now, data=data)
    return data


class StorageOpen(BaseModel):
    id: str


@app.post("/api/storage/open")
def open_storage(body: StorageOpen) -> dict[str, bool]:
    loc = next((l for l in paths.storage_locations() if l["id"] == body.id), None)
    if loc is None:
        raise HTTPException(400, "Unknown folder")
    if platform.system() != "Darwin":
        raise HTTPException(501, "Opening Finder works on macOS only.")
    target = Path(loc["path"])
    if not target.exists():
        raise HTTPException(404, "Not created yet")
    cmd = ["open", "-R", str(target)] if target.suffix == ".app" or target.is_file() else ["open", str(target)]
    subprocess.run(cmd, capture_output=True)
    return {"ok": True}


# ---- jobs -------------------------------------------------------------------------------

@app.get("/api/jobs")
def list_jobs() -> list[dict[str, Any]]:
    return [_view(j) for j in Job.all() if not j.manifest.get("bench")]


@app.post("/api/jobs")
def create_job(
    photo: UploadFile = File(...),
    subject: str = Form(""),
    name: str = Form(""),
    style: str = Form("clay-minimal"),
) -> dict[str, Any]:
    suffix = Path(photo.filename or "photo.jpg").suffix.lower() or ".jpg"
    if suffix not in pipeline.PHOTO_TYPES:
        raise HTTPException(400, f"Unsupported file type {suffix}. Use JPEG, PNG or WebP.")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d) / f"upload{suffix}"
        tmp.write_bytes(photo.file.read())
        blank = not subject.strip()
        subject = subject.strip() or Path(photo.filename or "object").stem.replace("_", " ").replace("-", " ")
        try:
            job = pipeline.create(tmp, name.strip() or subject, subject, style)
        except (KeyError, ValueError, OSError) as e:
            raise _bad(e)
    if blank:  # let Claude name it, when there's a key
        suggested = pipeline.suggest_name(settings_mod.context(), job)
        if suggested:
            job.update(subject=suggested, name=suggested if not name.strip() else job.manifest["name"])
    _warm_soon()
    return _view(job)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    return _view(_job(job_id))


class JobPatch(BaseModel):
    name: str | None = None
    subject: str | None = None


@app.patch("/api/jobs/{job_id}")
def patch_job(job_id: str, body: JobPatch) -> dict[str, Any]:
    with _job_locks[job_id]:
        job = _job(job_id)
        job.update(**body.model_dump(exclude_none=True))
    return _view(job)


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str) -> dict[str, bool]:
    with _locked(job_id) as job:
        job.delete()
    _job_locks.pop(job_id, None)
    return {"ok": True}


class StylizeBody(BaseModel):
    engine: str | None = None
    n: int | None = None
    seed: int | None = None  # start seed; default uses the style's fixed seeds
    quality: Literal["best", "quick", "lightest"] | None = None  # local SDXL only; default from Settings


@app.post("/api/jobs/{job_id}/stylize")
def stylize(job_id: str, body: StylizeBody) -> dict[str, Any]:
    job = _job(job_id)
    s = settings_mod.load()
    engine = body.engine or s["engines"]["stylize"]
    if engine not in STYLIZERS:
        raise HTTPException(400, "Unknown engine")
    style = get_style(job.manifest["style"]["id"])
    n = max(1, min(8, body.n or s.get("candidates", 4)))
    if body.seed is None:  # the style's seeds first, then fresh ones: every run adds new options
        start = len(job.manifest.get("candidates") or [])
        k = max(1, len(style.seeds))
        seeds = [style.seeds[(start + i) % k] + 1000 * ((start + i) // k) for i in range(n)]
    else:
        seeds = [body.seed + i for i in range(n)]
    ctx = settings_mod.context()
    if body.quality:
        ctx.settings = {**ctx.settings, "sdxl_quality": body.quality}
    q = quality_of(ctx.settings) if engine == "local-sdxl" else None
    return _queue(job, "stylize", lambda p: pipeline.stylize(job, ctx, engine, seeds, p,
                                                             on_candidate=lambda: _publish_state(job)),
                  meta={"engine": engine, "n": n, "quality": q, "device": ctx.device}, eta_s=_eta("stylize", engine, n, q))


@app.post("/api/jobs/{job_id}/cancel")
def cancel_task(job_id: str) -> dict[str, Any]:
    """Stop the running step. Options already made are kept."""
    job = _job(job_id)
    worker.cancel(job_id)
    return _view(job)


class UseNowBody(BaseModel):
    candidate: int
    engine: str | None = None


@app.post("/api/jobs/{job_id}/use-now")
def use_now(job_id: str, body: UseNowBody) -> dict[str, Any]:
    """Pick an option, even while others are still being made: stop the rest and build the 3D shape at once."""
    job = _job(job_id)
    engine = body.engine or settings_mod.load()["engines"]["reconstruct"]
    if engine not in RECONSTRUCTORS:
        raise HTTPException(400, "Unknown engine")
    if not 0 <= body.candidate < len(job.manifest.get("candidates") or []):
        raise HTTPException(400, "No such option")
    ctx = settings_mod.context()

    def run(progress):
        fresh = Job.load(job_id)  # the restyle that was just stopped may have added options
        pipeline.confirm(fresh, body.candidate)
        _publish_state(fresh)
        pipeline.reconstruct(fresh, ctx, engine, progress)

    return _queue(job, "reconstruct", run, replace=True, meta={"engine": engine, "device": ctx.device},
                  eta_s=_eta("reconstruct", engine))


class ConfirmBody(BaseModel):
    candidate: int | None = None  # None = use the photo as is


@app.delete("/api/jobs/{job_id}/candidates/{index}")
def remove_candidate(job_id: str, index: int) -> dict[str, Any]:
    with _locked(job_id) as job:
        try:
            pipeline.remove_candidate(job, index)
        except ValueError as e:
            raise _bad(e)
    return _view(job)


@app.delete("/api/jobs/{job_id}/candidates")
def clear_candidates(job_id: str) -> dict[str, Any]:
    with _locked(job_id) as job:
        pipeline.clear_candidates(job)
    return _view(job)


@app.post("/api/jobs/{job_id}/confirm")
def confirm(job_id: str, body: ConfirmBody) -> dict[str, Any]:
    with _locked(job_id) as job:
        try:
            if body.candidate is None:
                pipeline.use_photo_as_is(job)
            else:
                pipeline.confirm(job, body.candidate)
        except ValueError as e:
            raise _bad(e)
    _warm_soon(stylize=False)  # the 3D engine is next
    return _view(job)


class EngineBody(BaseModel):
    engine: str | None = None


@app.post("/api/jobs/{job_id}/reconstruct")
def reconstruct(job_id: str, body: EngineBody) -> dict[str, Any]:
    job = _job(job_id)
    engine = body.engine or settings_mod.load()["engines"]["reconstruct"]
    if engine not in RECONSTRUCTORS:
        raise HTTPException(400, "Unknown engine")
    if not job.reached("confirmed"):
        raise HTTPException(400, "Pick a style candidate first.")
    ctx = settings_mod.context()
    return _queue(job, "reconstruct", lambda p: pipeline.reconstruct(job, ctx, engine, p),
                  meta={"engine": engine, "device": ctx.device}, eta_s=_eta("reconstruct", engine))


class StyleBody(BaseModel):
    style: str


@app.post("/api/jobs/{job_id}/style")
def set_job_style(job_id: str, body: StyleBody) -> dict[str, Any]:
    with _locked(job_id) as job:
        try:
            pipeline.set_style(job, body.style)
        except KeyError:
            raise HTTPException(404, "Unknown style")
    return _view(job)


@app.post("/api/jobs/{job_id}/suggest-name")
def suggest_name(job_id: str) -> dict[str, Any]:
    from .. import claude

    job = _job(job_id)
    ctx = settings_mod.context()
    try:
        pipeline.ensure_cutout(job)
        name = claude.name_object(ctx.secrets.get("anthropic"), (ctx.settings.get("claude") or {}).get("model"),
                                  job.path("01_cutout.png"))
    except claude.ClaudeError as e:
        raise HTTPException(400, str(e))
    return {"subject": name}


@app.post("/api/jobs/{job_id}/rebuild-recipe")
def rebuild_recipe(job_id: str) -> dict[str, Any]:
    with _locked(job_id) as job:
        try:
            pipeline.rebuild_recipe(job)
        except (RuntimeError, ValueError) as e:
            raise _bad(e)
    return _view(job)


@app.post("/api/jobs/{job_id}/printify")
def printify(job_id: str, overrides: dict[str, Any]) -> dict[str, Any]:
    with _locked(job_id) as job:
        try:
            pipeline.printify(job, overrides)
        except (RuntimeError, ValueError) as e:
            raise _bad(e)
    return _view(job)


@app.post("/api/jobs/{job_id}/reset-print")
def reset_print(job_id: str) -> dict[str, Any]:
    with _locked(job_id) as job:
        fmt = (job.manifest.get("print_params") or {}).get("format", "stand")
        job.manifest["print_params"] = get_style(job.manifest["style"]["id"]).print_params(fmt)
        try:
            pipeline.printify(job, {})
        except (RuntimeError, ValueError) as e:
            raise _bad(e)
    return _view(job)


class ExportBody(BaseModel):
    formats: list[str] | None = None


@app.post("/api/jobs/{job_id}/export")
def export(job_id: str, body: ExportBody) -> dict[str, Any]:
    formats = body.formats or settings_mod.load().get("export_formats", ["3mf"])
    with _locked(job_id) as job:
        try:
            pipeline.export(job, formats)
        except (RuntimeError, ValueError) as e:
            raise _bad(e)
    return _view(job)


@app.get("/api/jobs/{job_id}/files/{rel:path}")
def job_file(job_id: str, rel: str, download: bool = False):
    job = _job(job_id)
    try:
        p = job.safe_file(rel)
    except FileNotFoundError:
        raise HTTPException(404, "File not found")
    headers = {"Cache-Control": "no-store"}
    if download:
        return FileResponse(p, filename=f"{job.id}-{p.name}", headers=headers)
    return FileResponse(p, headers=headers)


class OpenBody(BaseModel):
    app: str  # bambu | orca | prusa | finder
    file: str | None = None


SLICER_APPS = {"bambu": "Bambu Studio", "orca": "OrcaSlicer", "prusa": "PrusaSlicer"}


@app.post("/api/jobs/{job_id}/open")
def open_in(job_id: str, body: OpenBody) -> dict[str, Any]:
    job = _job(job_id)
    if platform.system() != "Darwin":
        raise HTTPException(501, "Opening other apps works on macOS only.")
    target = job.safe_file(body.file) if body.file else job.path(PRINT_DIR)
    if body.app == "finder":
        cmd = ["open", "-R", str(target)]
    elif body.app in SLICER_APPS:
        cmd = ["open", "-a", SLICER_APPS[body.app], str(target)]
    else:
        raise HTTPException(400, "Unknown app")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise HTTPException(400, f"Couldn't open {SLICER_APPS.get(body.app, 'Finder')}. Is it installed?")
    return {"ok": True}


@app.get("/api/jobs/{job_id}/events")
async def events(job_id: str, request: Request):
    job = _job(job_id)
    q = bus.subscribe(job.id)

    async def stream():
        try:
            yield f"data: {json.dumps({'type': 'job', 'job': _view(job)})}\n\n"
            while not await request.is_disconnected():
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(ev)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            bus.unsubscribe(job.id, q)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


# ---- web UI -----------------------------------------------------------------------

_dist = paths.web_dist()
if (_dist / "assets").exists():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(404)
    f = (_dist / full_path).resolve()
    if full_path and f.is_file() and _dist.resolve() in f.parents:
        return FileResponse(f)
    index = _dist / "index.html"
    if index.exists():
        return FileResponse(index, headers={"Cache-Control": "no-store"})
    return HTMLResponse("<h1>Form Studio</h1><p>The web UI isn't built yet. Run <code>npm run build</code> in <code>web/</code>.</p>")
