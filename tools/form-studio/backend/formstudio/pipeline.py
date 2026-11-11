"""The steps, shared by the app's API and the CLI."""
from __future__ import annotations

import copy
import logging
import shutil
from pathlib import Path
from typing import Any, Callable

from .core import export as export_mod
from .core import ingest, printify as printify_mod
from .core.job import CANDIDATES, CONFIRMED, CUTOUT, PRINT_DIR, RAW_MESH, Job
from .core.style import get_style
from .engines import RECONSTRUCTORS, STYLIZERS, EngineContext, ReconstructInput, StylizeInput

Progress = Callable[[float | None, str], None]

log = logging.getLogger("formstudio.pipeline")

PHOTO_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp", ".tif", ".tiff"}


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def create(photo: Path, name: str, subject: str, style_id: str) -> Job:
    if photo.suffix.lower() == ".heic":
        try:
            import pillow_heif  # type: ignore

            pillow_heif.register_heif_opener()
        except ImportError:
            raise ValueError("HEIC photos need pillow-heif; export the photo as JPEG or PNG instead.")
    style = get_style(style_id)
    job = Job.create(name or subject or photo.stem, subject or name or photo.stem, style.id, style.hash)
    dest = job.path(f"00_input{photo.suffix.lower()}")
    shutil.copyfile(photo, dest)
    job.manifest["input"] = dest.name
    job.manifest["print_params"] = style.print_params()
    job.save()
    log.info("%s: created from %s", job.id, photo.name)
    try:
        info = ingest.cutout(dest, job.path(CUTOUT))
        if not _cutout_files_exist(job):
            raise OSError("the cut-out image wasn't saved")
    except Exception:
        log.exception("%s: background removal failed; removing the object", job.id)
        job.delete()
        raise
    log.info("%s: cut-out saved (%s)", job.id, info.get("matting"))
    job.manifest["cutout"] = info
    job.set_stage("cutout")
    return job


def _cutout_files_exist(job: Job) -> bool:
    cut = job.path(CUTOUT)
    return cut.exists() and cut.with_name(cut.stem + "_mask.png").exists()


def ensure_cutout(job: Job) -> None:
    """Recreate the background-removed photo from the original if it's missing."""
    if _cutout_files_exist(job):
        return
    src = job.input_path()
    if not src or not src.exists():
        raise FileNotFoundError("This object's photo is missing. Create it again from the photo.")
    log.warning("%s: cut-out missing; recreating it from %s", job.id, src.name)
    job.manifest["cutout"] = ingest.cutout(src, job.path(CUTOUT))
    job.save()


def stylize(job: Job, ctx: EngineContext, engine_id: str, seeds: list[int], progress: Progress) -> None:
    style = get_style(job.manifest["style"]["id"])
    engine = STYLIZERS[engine_id]
    ok, why = engine.available(ctx)
    if not ok:
        raise RuntimeError(why)
    ensure_cutout(job)
    log.info("%s: restyling with %s, seeds %s", job.id, engine_id, seeds)
    job.reset_after("cutout")
    out_dir = job.path(CANDIDATES)
    out_dir.mkdir(parents=True, exist_ok=True)
    cut = job.path(CUTOUT)
    results = engine.stylize(
        StylizeInput(cut, cut.with_name(cut.stem + "_mask.png"), style, job.manifest["subject"], seeds, out_dir),
        ctx, progress,
    )
    for r in results:
        r["engine"] = engine_id
        r["file"] = f"{CANDIDATES}/{r['file']}"
    job.manifest["candidates"] = results
    job.manifest["style"]["hash"] = style.hash
    job.set_stage("stylized")
    log.info("%s: %d options ready", job.id, len(results))


def confirm(job: Job, index: int) -> None:
    cands = job.manifest["candidates"]
    if not 0 <= index < len(cands):
        raise ValueError("No such candidate")
    job.reset_after("stylized")
    ingest.cutout(job.path(cands[index]["file"]), job.path(CONFIRMED))
    job.manifest["confirmed"] = {"index": index, **cands[index]}
    job.set_stage("confirmed")


def use_photo_as_is(job: Job) -> None:
    """Skip restyling: send the cutout itself to the 3D step (for things already in style, or 2D art)."""
    ensure_cutout(job)
    job.reset_after("cutout")
    shutil.copyfile(job.path(CUTOUT), job.path(CONFIRMED))
    cut = job.path(CUTOUT)
    shutil.copyfile(cut.with_name(cut.stem + "_mask.png"), job.path("03_confirmed_mask.png"))
    job.manifest["confirmed"] = {"index": None, "file": CUTOUT, "seed": None, "engine": "photo"}
    job.set_stage("confirmed")


def reconstruct(job: Job, ctx: EngineContext, engine_id: str, progress: Progress) -> None:
    if not job.reached("confirmed"):
        raise RuntimeError("Pick a style candidate first.")
    engine = RECONSTRUCTORS[engine_id]
    ok, why = engine.available(ctx)
    if not ok:
        raise RuntimeError(why)
    job.reset_after("confirmed")
    log.info("%s: building 3D with %s", job.id, engine_id)
    seed = int((job.manifest.get("confirmed") or {}).get("seed") or 0)
    img = job.path(CONFIRMED)
    tmp = job.path("04_raw.tmp.glb")
    info = engine.reconstruct(ReconstructInput(img, img.with_name(img.stem + "_mask.png"), seed, tmp), ctx,
                              lambda p, m: progress(None if p is None else p * 0.8, m))
    tmp.replace(job.path(RAW_MESH))
    job.manifest["reconstruct"] = {"engine": engine_id, "seed": seed, **(info or {})}
    job.set_stage("reconstructed")
    printify(job, None, lambda p, m: progress(None if p is None else 0.8 + 0.2 * (p or 0), m))


def printify(job: Job, overrides: dict[str, Any] | None, progress: Progress | None = None) -> dict[str, Any]:
    if not job.reached("reconstructed"):
        raise RuntimeError("Build the 3D shape first.")
    style = get_style(job.manifest["style"]["id"])
    overrides = dict(overrides or {})
    current = job.manifest.get("print_params") or style.print_params()
    current.setdefault("format", "stand")
    fmt = overrides.pop("format", None)
    if fmt and fmt not in style.formats:
        raise ValueError(f"Unknown format '{fmt}'")
    if fmt and fmt != current["format"]:
        current = style.print_params(fmt)  # a new format starts from its own defaults
    params = _deep_merge(style.print_params(current["format"]), current)  # fills keys older objects lack
    params = _deep_merge(params, overrides)
    log.info("%s: preparing print (%s)", job.id, params["format"])
    report = printify_mod.run(job.path(RAW_MESH), job.path(PRINT_DIR), params, progress)
    job.manifest["print_params"] = params
    job.manifest["report"] = report
    job.manifest["exports"] = []
    for stale in ("model.3mf", "model.step"):
        job.path(PRINT_DIR, stale).unlink(missing_ok=True)
    if job.manifest["stage"] == "exported":
        job.manifest["stage"] = "printified"
    job.set_stage("printified")
    return report


def export(job: Job, formats: list[str]) -> list[str]:
    if not job.reached("printified"):
        raise RuntimeError("Prepare the print first.")
    style = get_style(job.manifest["style"]["id"])
    written = export_mod.export(job.path(PRINT_DIR), job.manifest["name"], style.palette, formats)
    job.manifest["exports"] = [f"{PRINT_DIR}/{w}" for w in written]
    job.set_stage("exported")
    return job.manifest["exports"]
