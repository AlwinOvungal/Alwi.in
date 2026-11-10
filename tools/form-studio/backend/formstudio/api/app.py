"""Form Studio's local server: the REST API, live progress, and the web UI."""
from __future__ import annotations

import asyncio
import json
import logging
import platform
import subprocess
import tempfile
import threading
from collections import defaultdict
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__, paths, pipeline
from ..core.job import PRINT_DIR, Job
from ..core.style import get_style, list_styles
from ..engines import RECONSTRUCTORS, STYLIZERS, describe
from ..engines.cloud import FalClient
from . import settings as settings_mod
from .worker import Bus, Worker

log = logging.getLogger(__name__)

bus = Bus()
worker = Worker(bus)


@asynccontextmanager
async def lifespan(app: FastAPI):
    bus.bind(asyncio.get_running_loop())
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


def _queue(job: Job, kind: str, fn) -> dict[str, Any]:
    def run(progress):
        try:
            with _job_locks[job.id]:
                fn(progress)
        finally:
            _publish_state(job)

    try:
        worker.submit(job.id, kind, run)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return _view(job)


def _bad(e: Exception) -> HTTPException:
    return HTTPException(400, str(e))


# ---- meta ------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "version": __version__, "platform": platform.system()}


@app.get("/api/styles")
def styles() -> list[dict[str, Any]]:
    return [s.summary() for s in list_styles()]


@app.get("/api/styles/{style_id}/reference")
def style_reference(style_id: str):
    try:
        s = get_style(style_id)
    except KeyError:
        raise HTTPException(404, "Unknown style")
    if not s.reference or not s.reference.exists():
        raise HTTPException(404, "No reference image")
    return FileResponse(s.reference)


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
    fast_mode: bool | None = None
    candidates: int | None = None
    export_formats: list[str] | None = None
    fal: dict[str, str] | None = None
    slicer: str | None = None
    fal_key: str | None = None  # "" removes it


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return settings_mod.public(settings_mod.load())


@app.put("/api/settings")
def put_settings(body: SettingsUpdate) -> dict[str, Any]:
    update = body.model_dump(exclude_none=True)
    key = update.pop("fal_key", None)
    if key is not None:
        settings_mod.set_secret("fal", key.strip() or None)
    if "engines" in update:
        for step, eid in update["engines"].items():
            reg = STYLIZERS if step == "stylize" else RECONSTRUCTORS if step == "reconstruct" else None
            if reg is None or eid not in reg:
                raise HTTPException(400, f"Unknown engine {eid} for {step}")
    if "candidates" in update:
        update["candidates"] = max(1, min(8, update["candidates"]))
    return settings_mod.public(settings_mod.save(update))


@app.post("/api/settings/test-key")
def test_key() -> dict[str, Any]:
    key = settings_mod.get_secret("fal")
    if not key:
        return {"ok": False, "message": "No key saved."}
    try:
        ok = FalClient(key).check()
    except Exception as e:
        return {"ok": False, "message": f"Couldn't reach fal.ai: {e}"}
    return {"ok": ok, "message": "Key works." if ok else "fal.ai rejected this key."}


# ---- jobs -------------------------------------------------------------------------------

@app.get("/api/jobs")
def list_jobs() -> list[dict[str, Any]]:
    return [_view(j) for j in Job.all()]


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
        subject = subject.strip() or Path(photo.filename or "object").stem.replace("_", " ").replace("-", " ")
        try:
            job = pipeline.create(tmp, name.strip() or subject, subject, style)
        except (KeyError, ValueError, OSError) as e:
            raise _bad(e)
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


@app.post("/api/jobs/{job_id}/stylize")
def stylize(job_id: str, body: StylizeBody) -> dict[str, Any]:
    job = _job(job_id)
    s = settings_mod.load()
    engine = body.engine or s["engines"]["stylize"]
    if engine not in STYLIZERS:
        raise HTTPException(400, "Unknown engine")
    style = get_style(job.manifest["style"]["id"])
    n = max(1, min(8, body.n or s.get("candidates", 4)))
    if body.seed is None:
        seeds = (style.seeds * ((n // max(1, len(style.seeds))) + 1))[:n]
        seeds = [sd + 1000 * (i // len(style.seeds)) for i, sd in enumerate(seeds)]
    else:
        seeds = [body.seed + i for i in range(n)]
    ctx = settings_mod.context()
    return _queue(job, "stylize", lambda p: pipeline.stylize(job, ctx, engine, seeds, p))


class ConfirmBody(BaseModel):
    candidate: int | None = None  # None = use the photo as is


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
    return _queue(job, "reconstruct", lambda p: pipeline.reconstruct(job, ctx, engine, p))


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
        job.manifest["print_params"] = get_style(job.manifest["style"]["id"]).print_params()
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
