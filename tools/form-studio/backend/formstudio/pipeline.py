"""The steps, shared by the app's API and the CLI."""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from . import paths
from .core import export as export_mod
from .core import timing
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
        t0 = time.perf_counter()
        with timing.stage("cutout"):
            info = ingest.cutout(dest, job.path(CUTOUT))
        _note_time(job, "cutout", time.perf_counter() - t0)
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


def _next_candidate_number(job: Job) -> int:
    nums = [int(c.get("n") or 0) for c in job.manifest.get("candidates") or []]
    files = [int(p.stem.split("_")[-1]) for p in job.path(CANDIDATES).glob("candidate_*.png") if p.stem.split("_")[-1].isdigit()]
    return max(nums + files + [0]) + 1


def _note_time(job: Job, step: str, seconds: float, **facts: Any) -> None:
    """Remember how long a step took on this object, so the app can say "took 48 s"."""
    job.manifest.setdefault("timings", {})[step] = {"seconds": round(seconds, 1), **facts}


def stylize(job: Job, ctx: EngineContext, engine_id: str, seeds: list[int], progress: Progress,
            on_candidate: Callable[[], None] | None = None) -> None:
    """Make more options. They're added to the ones already there, and each appears as soon as it's ready.

    Engines that make several pictures in one request (batch) get all seeds at once; cloud engines that
    take one picture per request (parallel) run side by side; local engines go one at a time on the GPU.
    Cancelling keeps every option already made.
    """
    style = get_style(job.manifest["style"]["id"])
    engine = STYLIZERS[engine_id]
    ok, why = engine.available(ctx)
    if not ok:
        raise RuntimeError(why)
    ensure_cutout(job)
    log.info("%s: restyling with %s (%s), seeds %s", job.id, engine_id, style.id, seeds)
    out_dir = job.path(CANDIDATES)
    out_dir.mkdir(parents=True, exist_ok=True)
    cut = job.path(CUTOUT)
    n = len(seeds)
    t0 = time.perf_counter()
    made = [0]
    first: list[float] = []

    def inp(seed_list: list[int], tmp: Path) -> StylizeInput:
        return StylizeInput(cut, cut.with_name(cut.stem + "_mask.png"), style, job.manifest["subject"], seed_list, tmp)

    def fresh(tmp: Path) -> Path:
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        return tmp

    def add(tmp: Path, r: dict) -> None:
        num = _next_candidate_number(job)
        name = f"candidate_{num}.png"
        (tmp / r["file"]).replace(out_dir / name)
        job.manifest.setdefault("candidates", []).append({
            **r, "n": num, "file": f"{CANDIDATES}/{name}", "engine": engine_id,
            "style_id": style.id, "style_name": style.name,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        made[0] += 1
        if not first:
            first.append(time.perf_counter() - t0)
        job.manifest["style"]["hash"] = style.hash
        if not job.reached("stylized"):
            job.set_stage("stylized")
        else:
            job.save()
        if on_candidate:
            on_candidate()  # show it in the app straight away

    try:
        if getattr(engine, "batch", False):
            tmp = fresh(out_dir / ".run")
            for r in engine.stylize(inp(seeds, tmp), ctx, progress):
                add(tmp, r)
        elif getattr(engine, "parallel", 0) > 1 and n > 1:
            fracs = [0.0] * n

            def one(i: int, seed: int):
                tmp = fresh(out_dir / f".run{i}")

                def prog(p: float | None, m: str) -> None:
                    if p is not None:
                        fracs[i] = p
                    progress(sum(fracs) / n, f"{made[0]} of {n} ready · {m}")

                return tmp, engine.stylize(inp([seed], tmp), ctx, prog)

            with ThreadPoolExecutor(max_workers=min(n, int(engine.parallel))) as pool:
                futures = [pool.submit(one, i, seed) for i, seed in enumerate(seeds)]
                try:
                    for f in as_completed(futures):
                        tmp, results = f.result()
                        for r in results:
                            add(tmp, r)
                except BaseException:
                    for f in futures:
                        f.cancel()
                    raise
        else:
            for i, seed in enumerate(seeds):
                tmp = fresh(out_dir / ".run")
                results = engine.stylize(
                    inp([seed], tmp), ctx,
                    lambda p, m, i=i: progress(None if p is None else (i + p) / n, f"Option {i + 1} of {n}: {m}"),
                )
                for r in results:
                    add(tmp, r)
    finally:
        for tmp in out_dir.glob(".run*"):
            shutil.rmtree(tmp, ignore_errors=True)
        if made[0]:
            _note_time(job, "stylize", time.perf_counter() - t0, n=made[0], engine=engine_id,
                       first_option=round(first[0], 1) if first else None)
            job.save()
    log.info("%s: %d new options (%d in total)", job.id, made[0], len(job.manifest["candidates"]))


def _reset_keeping_options(job: Job) -> None:
    """Clear the chosen option and everything after it, but keep all generated options."""
    job.reset_after("stylized" if job.manifest.get("candidates") else "cutout")


def remove_candidate(job: Job, index: int) -> None:
    cands = job.manifest.get("candidates") or []
    if not 0 <= index < len(cands):
        raise ValueError("No such option")
    gone = cands.pop(index)
    job.path(gone["file"]).unlink(missing_ok=True)
    if not cands and job.manifest["stage"] == "stylized":
        job.manifest["stage"] = "cutout"
    job.save()


def clear_candidates(job: Job) -> None:
    shutil.rmtree(job.path(CANDIDATES), ignore_errors=True)
    job.manifest["candidates"] = []
    if job.manifest["stage"] == "stylized":
        job.manifest["stage"] = "cutout"
    job.save()


def confirm(job: Job, index: int) -> None:
    """Pick an option for the 3D step. The other options stay, so you can come back and pick again."""
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
    _reset_keeping_options(job)
    shutil.copyfile(job.path(CUTOUT), job.path(CONFIRMED))
    cut = job.path(CUTOUT)
    shutil.copyfile(cut.with_name(cut.stem + "_mask.png"), job.path("03_confirmed_mask.png"))
    job.manifest["confirmed"] = {"index": None, "file": CUTOUT, "seed": None, "engine": "photo"}
    job.set_stage("confirmed")


SHAPE_CACHE = {"enabled": True, "max_bytes": 2 * 1024**3}


def _shape_key(job: Job, engine_id: str, ctx: EngineContext, seed: int) -> str | None:
    """Everything that decides the AI mesh. Offline engines are instant and Claude Sculpt keeps its own recipe,
    so they aren't cached."""
    engine = RECONSTRUCTORS[engine_id]
    if not SHAPE_CACHE["enabled"] or engine.kind == "offline" or engine_id == "claude-sculpt":
        return None
    h = hashlib.sha256()
    for f in (CONFIRMED, "03_confirmed_mask.png"):
        p = job.path(f)
        if not p.exists():
            return None
        h.update(p.read_bytes())
    s = ctx.settings
    facts = {
        "engine": engine_id, "seed": seed, "version": getattr(engine, "version", 1),
        "device": ctx.device if engine.kind == "local" else None,
        "meshy": s.get("meshy") if engine_id == "meshy" else None,
        "endpoint": ((s.get("fal") or {}).get("endpoints") or {}).get(engine_id),
    }
    h.update(json.dumps(facts, sort_keys=True, default=str).encode())
    return h.hexdigest()[:32]


def _shape_dir() -> Path:
    return paths.home() / "cache" / "shape"


def _prune_shapes() -> None:
    files = sorted(_shape_dir().glob("*.glb"), key=lambda f: f.stat().st_mtime)
    total = sum(f.stat().st_size for f in files)
    while files and total > SHAPE_CACHE["max_bytes"]:
        f = files.pop(0)
        total -= f.stat().st_size
        f.unlink(missing_ok=True)
        f.with_suffix(".json").unlink(missing_ok=True)


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
    style = get_style(job.manifest["style"]["id"])
    key = _shape_key(job, engine_id, ctx, seed)
    hit = _shape_dir() / f"{key}.glb" if key else None
    t0 = time.perf_counter()
    if hit and hit.exists():
        progress(0.75, "Using the shape made earlier from this picture")
        shutil.copyfile(hit, job.path(RAW_MESH))
        os.utime(hit)
        try:
            info = json.loads(hit.with_suffix(".json").read_text())
        except (OSError, json.JSONDecodeError):
            info = {}
        info["cached"] = True
        timing.record("reconstruct", time.perf_counter() - t0, engine=engine_id, cached=True)
    else:
        inp = ReconstructInput(img, img.with_name(img.stem + "_mask.png"), seed, tmp, style=style, subject=job.manifest["subject"])
        with timing.stage("reconstruct", engine=engine_id, device=ctx.device, cached=False):
            info = engine.reconstruct(inp, ctx, lambda p, m: progress(None if p is None else p * 0.8, m)) or {}
        tmp.replace(job.path(RAW_MESH))
        if hit:
            try:
                hit.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(job.path(RAW_MESH), hit)
                hit.with_suffix(".json").write_text(json.dumps(info, default=str))
                _prune_shapes()
            except OSError as e:
                log.warning("couldn't keep the shape for later: %s", e)
    job.manifest["reconstruct"] = {"engine": engine_id, "seed": seed, **info}
    _note_time(job, "reconstruct", time.perf_counter() - t0, engine=engine_id, cached=bool(info.get("cached")))
    job.set_stage("reconstructed")
    printify(job, None, lambda p, m: progress(None if p is None else 0.8 + 0.2 * (p or 0), m))


def rebuild_recipe(job: Job, progress: Progress | None = None) -> None:
    """Rebuild the shape from Claude's saved recipe: free, and identical every time."""
    from .engines.claude_sculpt import rebuild_from_recipe

    if not job.path("04_recipe.json").exists():
        raise RuntimeError("This object has no Claude recipe. Build it with Claude Sculpt first.")
    info = rebuild_from_recipe(job.path(RAW_MESH))
    job.manifest["reconstruct"] = {**(job.manifest.get("reconstruct") or {}), **info}
    if not job.reached("reconstructed"):
        job.set_stage("reconstructed")
    printify(job, None, progress)


def set_style(job: Job, style_id: str) -> None:
    """Switch an object to another style. Earlier options stay (labelled with their style); the 3D shape is redone."""
    style = get_style(style_id)
    _reset_keeping_options(job)
    job.manifest["style"] = {"id": style.id, "hash": style.hash}
    job.manifest["print_params"] = style.print_params((job.manifest.get("print_params") or {}).get("format", "stand"))
    job.save()
    log.info("%s: style set to %s", job.id, style.id)


def suggest_name(ctx: EngineContext, job: Job) -> str | None:
    """Ask Claude what the object is (only with an Anthropic key and auto-naming on)."""
    from . import claude

    if not ctx.secrets.get("anthropic") or not (ctx.settings.get("claude") or {}).get("auto_name", True):
        return None
    try:
        return claude.name_object(ctx.secrets["anthropic"], (ctx.settings.get("claude") or {}).get("model"), job.path(CUTOUT))
    except claude.ClaudeError as e:
        log.warning("%s: couldn't name the object: %s", job.id, e)
        return None


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
    t0 = time.perf_counter()
    with timing.stage("printify", format=params["format"], finish=params["geometry"].get("finish")):
        report = printify_mod.run(job.path(RAW_MESH), job.path(PRINT_DIR), params, progress)
    _note_time(job, "printify", time.perf_counter() - t0)
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
    t0 = time.perf_counter()
    with timing.stage("export"):
        written = export_mod.export(job.path(PRINT_DIR), job.manifest["name"], style.palette, formats)
    _note_time(job, "export", time.perf_counter() - t0)
    job.manifest["exports"] = [f"{PRINT_DIR}/{w}" for w in written]
    job.set_stage("exported")
    return job.manifest["exports"]
