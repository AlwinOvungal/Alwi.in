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

import numpy as np
from PIL import Image

from . import paths
from .core import export as export_mod
from .core import timing
from .core import ingest, printify as printify_mod
from .core import views as views_mod
from .core import logo as logo_mod
from .core.job import CANDIDATES, CONFIRMED, CUTOUT, PRINT_DIR, RAW_MESH, VIEWS_DIR, Job
from .core.style import get_style
from .engines import RECONSTRUCTORS, STYLIZERS, VIEWERS, EngineContext, ReconstructInput, StylizeInput
from .engines.base import VIEW_NAMES, ViewInput

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


def create(photo: Path, name: str, subject: str, style_id: str, fmt: str | None = None) -> Job:
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
    if fmt and fmt not in style.formats:
        raise ValueError(f"Unknown format '{fmt}'")
    job.manifest["print_params"] = style.print_params(fmt or "stand")  # what it becomes guides every step
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
    try:  # logos and text are built from the artwork itself, so their letters stay exact
        kind = "logo" if logo_mod.is_graphic(dest) else "object"
        # a 3-view turnaround sheet of flat art is an object, not a logo (2 pieces side by side is
        # usually a mark + wordmark, so that stays a logo)
        if kind == "logo" and len(views_mod.panels_of(dest) or ()) >= 3:
            kind = "object"
        job.manifest["kind"] = kind
    except Exception as e:  # never stop an upload over this
        log.warning("%s: couldn't check for a logo: %s", job.id, e)
        job.manifest["kind"] = "object"
    job.set_stage("cutout")
    return job


# ---- logos and text ----------------------------------------------------------------

def is_logo(job: Job) -> bool:
    return job.manifest.get("kind") == "logo"


def logo_parts(job: Job) -> bool:
    """Is the shape (or will it be) built from the artwork, as one part per logo colour plus a plaque?"""
    return is_logo(job) and ((job.manifest.get("reconstruct") or {}).get("engine") or "logo") == "logo"


def region_parts(job: Job) -> list[dict[str, str]]:
    """The picture-colour parts of the last print build ([{id, name, hex}]), or []."""
    if logo_parts(job):
        return []
    return list((job.manifest.get("report") or {}).get("regions") or [])


def is_flat(job: Job) -> bool:
    """Is the object a flat format (magnet, keychain, ornament): a relief of its picture?"""
    return bool(((job.manifest.get("print_params") or {}).get("flat_back") or {}).get("enabled"))


def logo_data(job: Job) -> dict[str, Any]:
    src = job.input_path()
    if not src or not src.exists():
        raise FileNotFoundError("This object's original picture is missing. Create it again from the logo file.")
    return logo_mod.layers_cached(src)


def set_kind(job: Job, kind: str) -> None:
    """Logo or text (built from the artwork) or an object (AI restyle and 3D). The chosen option and
    shape are redone; options already made stay."""
    if kind not in ("logo", "object"):
        raise ValueError("kind is 'logo' or 'object'")
    if job.manifest.get("kind") == kind:
        return
    _reset_keeping_options(job)
    job.manifest["kind"] = kind
    job.save()


def default_engine(job: Job, step: str, settings: dict[str, Any]) -> str:
    if is_logo(job):
        return "logo"
    return settings["engines"][step]


AUTO_ORDER = {  # "Make it now": your default engine first, then the best one that's ready right now
    "stylize": ["local-sdxl", "openai-image", "fal-nano-banana", "fal-kontext"],
    "reconstruct": ["triposr", "hunyuan-mini", "meshy", "fal-trellis", "fal-hunyuan", "extrude"],
}


LOCAL_PACKS = {"triposr": ["triposr-code", "triposr"], "hunyuan-mini": ["hunyuan"], "hunyuan-mv": ["hunyuan-mv"]}


def pick_engine(job: Job, step: str, ctx: EngineContext, settings: dict[str, Any]) -> str | None:
    """The engine the one-click flow uses: the logo path for logos, your default when it's ready,
    else the best engine that's ready right now. A local engine counts as ready only when its models
    are already downloaded — one click never starts a 12 GB download. None (stylize only) means
    keep the photo as it is."""
    if is_logo(job):
        return "logo"
    if step == "reconstruct" and is_flat(job):
        return "picture"  # magnets, keychains, ornaments: built from the picture itself, in seconds
    from .engines.local import sdxl_packs
    from .models import status

    def ready(eid: str, e: Any) -> bool:
        if getattr(e, "logo_only", False) or not e.available(ctx)[0]:
            return False
        if getattr(e, "kind", "") != "local":
            return True  # cloud engines check their key in available(); offline ones need nothing
        packs = sdxl_packs(ctx.settings) if eid == "local-sdxl" else LOCAL_PACKS.get(eid, [])
        try:
            return all(status(p)["status"] == "ready" for p in packs)
        except Exception:  # can't tell (offline): don't guess
            return False

    table = STYLIZERS if step == "stylize" else RECONSTRUCTORS
    for eid in [settings["engines"][step], *AUTO_ORDER[step]]:
        e = table.get(eid)
        if e is not None and ready(eid, e):
            return eid
    return None


def auto(job: Job, ctx: EngineContext, settings: dict[str, Any], fmt: str | None, progress: Progress,
         publish: Callable[[], None] = lambda: None) -> None:
    """One click: photo in, printable parts out. Restyles with the best engine that's ready (keeping
    the photo when none is, or when restyling fails), picks the first option, builds the 3D shape and
    prepares the print in the chosen format. Logos take their exact-artwork path throughout."""
    from .engines.base import Cancelled

    style = get_style(job.manifest["style"]["id"])
    if fmt:
        if fmt not in style.formats:
            raise ValueError(f"Unknown format '{fmt}'")
        if (job.manifest.get("print_params") or {}).get("format") != fmt:
            job.manifest["print_params"] = style.print_params(fmt)
            job.save()
    ensure_cutout(job)

    def part(a: float, b: float) -> Progress:
        return lambda p, m: progress(None if p is None else a + (b - a) * min(max(p, 0.0), 1.0), m)

    if not job.reached("confirmed"):
        if not (job.manifest.get("candidates") or []):
            st = pick_engine(job, "stylize", ctx, settings)
            if st:
                try:
                    stylize(job, ctx, st, [style.seeds[0]], part(0.0, 0.45), on_candidate=publish)
                except Cancelled:
                    raise
                except Exception as e:  # a cloud hiccup shouldn't stop the one-click flow
                    log.warning("%s: one-click restyle with %s failed (%s); keeping the photo", job.id, st, e)
        if job.manifest.get("candidates"):
            confirm(job, 0)
        else:
            progress(0.45, "Keeping your photo as it is")
            use_photo_as_is(job)
        publish()
    rc = pick_engine(job, "reconstruct", ctx, settings) or "extrude"
    reconstruct(job, ctx, rc, part(0.5, 1.0))


LOGO_SAFE = [  # the house prompts forbid text; for a logo the AI should try to keep it
    ("no text, no fine detail, ", ""), ("no text or fine detail. ", ""), ("no text or fine detail", ""),
    ("remove all fine detail and text", "keep every letter exactly as written"), ("no text, ", ""),
    (", no text", ""), ("text, logo, ", ""), ("text, ", ""), ("logo, ", ""),
]


def _logo_style(style):
    import dataclasses

    def clean(t: str) -> str:
        for a, b in LOGO_SAFE:
            t = t.replace(a, b)
        return t

    return dataclasses.replace(
        style,
        prompt=clean(style.prompt) + ", keep the exact lettering and logo shapes",
        edit_prompt=clean(style.edit_prompt) + " Keep every letter and the logo's shapes exactly as they are.",
        negative_prompt=clean(style.negative_prompt) + ", misspelt letters, extra letters",
    )


def print_parts(job: Job) -> list[dict[str, Any]]:
    """The print's parts in filament order: [{id, label, file, filament, name, hex}]."""
    d = job.path(PRINT_DIR)
    colours = colours_of(job)
    if (d / "parts.json").exists():
        try:
            listed = json.loads((d / "parts.json").read_text())
        except (OSError, ValueError):
            listed = []
    else:
        listed = [{"id": "body", "file": "body.stl"}, {"id": "base", "file": "base.stl"}]
    out = []
    for p in listed:
        if not (d / p["file"]).exists():
            continue
        c = colours.get(p["id"]) or {"name": "Grey", "hex": "#8A8A85", "label": p.get("label", p["id"])}
        out.append({**c, "id": p["id"], "file": f"{PRINT_DIR}/{p['file']}", "filament": len(out) + 1})
    return out


def _printify_logo(job: Job, params: dict[str, Any], progress: Progress | None) -> dict[str, Any]:
    res = logo_mod.build(logo_data(job), params, progress)
    out = job.path(PRINT_DIR)
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*.stl"):
        f.unlink()
    listed = []
    for i, p in enumerate(res["parts"]):
        name = "plaque.stl" if p["id"] == "plaque" else f"part_{i + 1}.stl"
        p["mesh"].export(out / name)
        listed.append({"id": p["id"], "file": name})
    (out / "parts.json").write_text(json.dumps(listed))
    return res["report"]


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


REDUCED = "02_reduced.png"  # the cut-out in its reduced palette (Photo step, "Reduce colours")


def palette_state(job: Job) -> dict[str, Any]:
    """The Photo step's colour reduction: off by default. {enabled, max, colours:[{hex, name, original}]}."""
    return {"enabled": False, "max": 4, "colours": [], **(job.manifest.get("palette") or {})}


def palette_active(job: Job) -> bool:
    return bool(palette_state(job)["enabled"]) and job.path(REDUCED).exists()


def set_palette(job: Job, enabled: bool, max_colours: int | None = None,
                colours: list[str | None] | None = None, restore: bool = False) -> dict[str, Any]:
    """Turn colour reduction on or off, choose how many colours to keep, recolour or restore them.

    On: the cut-out is reduced to at most `max` of its own paints (shading kept as one paint), saved as
    a preview; restyles start from it and the print uses the same colours. Off: nothing is reduced.
    `colours[i]` is a new hex for colour i (None leaves it); `restore` puts back the picture's own."""
    from .core import colours as colours_mod

    ensure_cutout(job)
    pal = palette_state(job)
    old_max = pal["max"]
    if max_colours is not None:
        pal["max"] = max(1, min(8, int(max_colours)))
    pal["enabled"] = bool(enabled)
    if not pal["enabled"]:
        job.path(REDUCED).unlink(missing_ok=True)
    else:
        cut = job.path(CUTOUT)
        img = Image.open(cut).convert("RGB")
        mask = np.asarray(Image.open(cut.with_name(cut.stem + "_mask.png")).convert("L")) > 24
        labels, found = logo_mod.regions(img, mask, pal["max"])
        if restore or pal["max"] != old_max or len(pal["colours"]) != len(found) or not job.path(REDUCED).exists():
            pal["colours"] = [{**c, "original": c["hex"]} for c in found]
        for i, h in enumerate(colours or []):
            if h and i < len(pal["colours"]):
                hexv = h.upper()
                rgb = tuple(int(hexv[k:k + 2], 16) for k in (1, 3, 5))
                pal["colours"][i] = {**pal["colours"][i], "hex": hexv, "name": colours_mod.name_for(rgb)}
        lut = np.array([[int(c["hex"][k:k + 2], 16) for k in (1, 3, 5)] for c in pal["colours"]], np.uint8)
        out = np.asarray(img).copy()
        out[mask] = lut[np.clip(labels[mask], 0, len(lut) - 1)]
        out[~mask] = 255  # the cut-out's white background
        Image.fromarray(out).save(job.path(REDUCED))
    job.manifest["palette"] = pal
    params = job.manifest.get("print_params")
    if params is not None:  # the print's colour parts follow: at most this many, or every real colour
        reg = params.setdefault("regions", {})
        reg["max_colours"] = pal["max"] if pal["enabled"] else None
        reg.pop("palette", None)
        if pal["enabled"]:
            reg["palette"] = [{"hex": c["hex"], "name": c["name"]} for c in pal["colours"]]
    job.save()
    return pal


def _palette_hexes(job: Job) -> list[dict[str, str]]:
    return list(palette_state(job)["colours"]) if palette_active(job) else []


def _palette_style(style, colours: list[dict[str, str]]):
    """Restyles keep to the approved palette."""
    import dataclasses

    names = ", ".join(f"{c['name'].lower()} ({c['hex']})" for c in colours)
    rule = f" Use only these colours, in the same places as the reference picture: {names}."
    return dataclasses.replace(style, prompt=style.prompt + f", only these colours: {names}",
                               edit_prompt=style.edit_prompt + rule)


def _snap_to_palette(job: Job, report: dict[str, Any]) -> None:
    """With an approved palette, each printed colour part takes the nearest approved colour."""
    pal = _palette_hexes(job)
    if not pal or not report.get("regions"):
        return
    from skimage import color

    def lab(h: str) -> np.ndarray:
        return color.rgb2lab(np.array([[[int(h[k:k + 2], 16) / 255 for k in (1, 3, 5)]]]))[0, 0]

    targets = [lab(c["hex"]) for c in pal]
    for r in report["regions"]:
        i = int(np.argmin([np.linalg.norm(lab(r["hex"]) - t) for t in targets]))
        r["hex"], r["name"] = pal[i]["hex"], pal[i]["name"]


def stylize(job: Job, ctx: EngineContext, engine_id: str, seeds: list[int], progress: Progress,
            on_candidate: Callable[[], None] | None = None, *, source: Path | None = None,
            look_override: Any = None, extra: dict[str, Any] | None = None) -> None:
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
    if getattr(engine, "logo_only", False) and not is_logo(job):
        raise RuntimeError("This is for logos and text. Turn on \"Logo or text\" in the Photo step first.")
    ensure_cutout(job)
    look = _logo_style(style) if is_logo(job) and not getattr(engine, "logo_only", False) else style
    if (pal := _palette_hexes(job)) and not is_logo(job):
        look = _palette_style(look, pal)
    if look_override is not None:
        look = look_override
    log.info("%s: restyling with %s (%s), seeds %s", job.id, engine_id, style.id, seeds)
    out_dir = job.path(CANDIDATES)
    out_dir.mkdir(parents=True, exist_ok=True)
    cut = source or (job.path(REDUCED) if _palette_hexes(job) and not is_logo(job) else job.path(CUTOUT))
    mask_of_cut = job.path(CUTOUT).with_name(Path(CUTOUT).stem + "_mask.png")
    n = len(seeds)
    t0 = time.perf_counter()
    made = [0]
    first: list[float] = []

    def inp(seed_list: list[int], tmp: Path) -> StylizeInput:
        return StylizeInput(cut, mask_of_cut, look, job.manifest["subject"], seed_list, tmp,
                            source=job.input_path())

    def fresh(tmp: Path) -> Path:
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        return tmp

    def add(tmp: Path, r: dict) -> None:
        num = _next_candidate_number(job)
        name = f"candidate_{num}.png"
        (tmp / r["file"]).replace(out_dir / name)
        if not is_logo(job):  # an AI that answered with a turnaround sheet: show its front object instead
            try:
                if views_mod.panels_of(out_dir / name):
                    sheet = f"candidate_{num}_sheet.png"
                    shutil.copyfile(out_dir / name, out_dir / sheet)
                    info = views_mod.main_object(out_dir / sheet, out_dir / name)
                    (out_dir / name).with_name(f"candidate_{num}_mask.png").unlink(missing_ok=True)
                    r = {**r, "sheet": info["panels"], "sheet_file": f"{CANDIDATES}/{sheet}"}
                    log.info("%s: option %d came back as a sheet of %d; kept its front object", job.id, num, info["panels"])
            except Exception as e:
                log.warning("%s: sheet check on option %d failed: %s", job.id, num, e)
        job.manifest.setdefault("candidates", []).append({
            **r, **(extra or {}), "n": num, "file": f"{CANDIDATES}/{name}", "engine": engine_id,
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


REFINERS = ("openai-image", "fal-nano-banana", "fal-kontext")  # engines that edit a picture from words


def refine_engine(job: Job, ctx: EngineContext, index: int) -> str | None:
    """The engine to refine an option with: the one that made it if it can edit by words, else the
    first that's set up."""
    cands = job.manifest.get("candidates") or []
    made_by = cands[index].get("engine") if 0 <= index < len(cands) else None
    for eid in ([made_by] if made_by in REFINERS else []) + list(REFINERS):
        e = STYLIZERS.get(eid)
        if e is not None and e.available(ctx)[0]:
            return eid
    return None


def refine(job: Job, ctx: EngineContext, index: int, message: str, progress: Progress,
           on_candidate: Callable[[], None] | None = None) -> None:
    """Change one option by asking in words ("make the trunk thicker"). The result is a new option
    next to it, linked to the one it came from, so the conversation can go on from either."""
    cands = job.manifest.get("candidates") or []
    if not 0 <= index < len(cands):
        raise IndexError("No such option")
    message = " ".join(message.split())[:600]
    if not message:
        raise ValueError("Say what to change.")
    eid = refine_engine(job, ctx, index)
    if not eid:
        raise RuntimeError("Refining in words needs an OpenAI or fal.ai key (Settings → Keys). "
                           "You can also make new options, or chat with Form Studio from ChatGPT.")
    import dataclasses

    style = get_style(job.manifest["style"]["id"])
    pal = _palette_hexes(job)
    keep = (" Keep only these colours: " + ", ".join(f"{c['name'].lower()} ({c['hex']})" for c in pal) + ".") if pal else ""
    fmt = (job.manifest.get("print_params") or {}).get("format", "stand")
    use = {"magnet": "a fridge magnet", "keychain": "a keychain", "ornament": "a hanging ornament"}.get(fmt, "a figurine")
    edit = (f"Change only this: {message}. Keep everything else exactly as in the first image: the same {{subject}}, "
            f"its shape, pose, colours and style.{keep} It will be 3D printed as {use}, so keep shapes bold and "
            "chunky with no tiny details. One object, centred, on a plain white background.")
    look = dataclasses.replace(style, references=[], edit_prompt=edit, prompt=f"{{subject}}, {message}, {style.prompt}")
    seed = int(cands[index].get("seed") or 0) + 101
    log.info("%s: refining option %s with %s: %s", job.id, cands[index].get("n"), eid, message)
    stylize(job, ctx, eid, [seed], progress, on_candidate, source=job.path(cands[index]["file"]),
            look_override=look, extra={"parent": cands[index].get("n", index + 1), "message": message})


def advice(job: Job, index: int) -> list[dict[str, Any]]:
    """Design suggestions for one option, at the size and type this object will be printed."""
    from .core import advice as advice_mod

    cands = job.manifest.get("candidates") or []
    if not 0 <= index < len(cands):
        raise IndexError("No such option")
    params = job.manifest.get("print_params") or get_style(job.manifest["style"]["id"]).print_params()
    pal = palette_state(job)
    return advice_mod.advise(job.path(cands[index]["file"]), params, pal["max"] if pal["enabled"] else None)


def _handle_sheet(job: Job, src: Path) -> dict[str, Any]:
    """A turnaround / product sheet went into the 3D step: keep only its FRONT object as the
    confirmed picture, and with three panels keep the side and back as views, so the multi-view
    3D engines see the whole object for free. {} when the picture is one object."""
    try:
        info = views_mod.main_object(src, job.path(CONFIRMED))
    except Exception as e:  # never lose the pick over this
        log.warning("%s: sheet check failed: %s", job.id, e)
        return {}
    if not info:
        return {}
    n = info["panels"]
    log.info("%s: the picture is a sheet of %d panels; building from the first one", job.id, n)
    if n == 3:
        try:
            out = job.path(VIEWS_DIR)
            shutil.rmtree(out, ignore_errors=True)
            if views_mod.split_panels(src, out):
                job.manifest["views"] = {"engine": "sheet", "files": [f"{VIEWS_DIR}/{v}.png" for v in VIEW_NAMES],
                                         "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
        except Exception as e:
            log.warning("%s: couldn't keep the sheet's views: %s", job.id, e)
    return {"sheet": n}


def _reset_keeping_options(job: Job) -> None:
    """Clear the chosen option and everything after it, but keep all generated options."""
    job.reset_after("stylized" if job.manifest.get("candidates") else "cutout")


def remove_candidate(job: Job, index: int) -> None:
    cands = job.manifest.get("candidates") or []
    if not 0 <= index < len(cands):
        raise ValueError("No such option")
    gone = cands.pop(index)
    job.path(gone["file"]).unlink(missing_ok=True)
    if gone.get("sheet_file"):
        job.path(gone["sheet_file"]).unlink(missing_ok=True)
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
    extra = {} if is_logo(job) else _handle_sheet(job, job.path(cands[index].get("sheet_file") or cands[index]["file"]))
    job.manifest["confirmed"] = {"index": index, **cands[index], **extra}
    job.set_stage("confirmed")


def use_photo_as_is(job: Job) -> None:
    """Skip restyling: send the cutout itself to the 3D step (for things already in style, or 2D art)."""
    ensure_cutout(job)
    _reset_keeping_options(job)
    shutil.copyfile(job.path(REDUCED) if palette_active(job) else job.path(CUTOUT), job.path(CONFIRMED))
    cut = job.path(CUTOUT)
    shutil.copyfile(cut.with_name(cut.stem + "_mask.png"), job.path("03_confirmed_mask.png"))
    extra = {} if is_logo(job) else _handle_sheet(job, cut)
    job.manifest["confirmed"] = {"index": None, "file": CUTOUT, "seed": None, "engine": "photo", **extra}
    job.set_stage("confirmed")


def _views(job: Job) -> dict[str, tuple[Path, Path]] | None:
    """The object's front / side / back pictures, if it has them."""
    if not job.manifest.get("views"):
        return None
    out = {}
    for name in VIEW_NAMES:
        img = job.path(VIEWS_DIR, f"{name}.png")
        mask = img.with_name(f"{name}_mask.png")
        if not (img.exists() and mask.exists()):
            return None
        out[name] = (img, mask)
    return out


def make_views(job: Job, ctx: EngineContext, engine_id: str, progress: Progress) -> None:
    """Draw front, side and back of the chosen picture, so multi-view 3D engines can see the whole object."""
    if not job.reached("confirmed"):
        raise RuntimeError("Pick an option first.")
    engine = VIEWERS[engine_id]
    ok, why = engine.available(ctx)
    if not ok:
        raise RuntimeError(why)
    out = job.path(VIEWS_DIR)
    tmp = job.path(VIEWS_DIR + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    img = job.path(CONFIRMED)
    style = get_style(job.manifest["style"]["id"])
    seed = int((job.manifest.get("confirmed") or {}).get("seed") or 0)
    t0 = time.perf_counter()
    try:
        with timing.stage("views", engine=engine_id, device=ctx.device):
            engine.make_views(ViewInput(img, img.with_name(img.stem + "_mask.png"), style, job.manifest["subject"], tmp, seed),
                              ctx, progress)
        missing_views = [n for n in VIEW_NAMES if not (tmp / f"{n}.png").exists()]
        if missing_views:
            raise RuntimeError(f"The view maker didn't return the {', '.join(missing_views)} view. Try again.")
        shutil.rmtree(out, ignore_errors=True)
        tmp.replace(out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    job.manifest["views"] = {"engine": engine_id, "files": [f"{VIEWS_DIR}/{n}.png" for n in VIEW_NAMES],
                             "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
    _note_time(job, "views", time.perf_counter() - t0, engine=engine_id)
    job.save()
    log.info("%s: 3 views made with %s", job.id, engine_id)


def remove_views(job: Job) -> None:
    shutil.rmtree(job.path(VIEWS_DIR), ignore_errors=True)
    job.manifest["views"] = None
    job.save()


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
    views = _views(job) if getattr(engine, "multi_view", False) else None
    for _, (img, mask) in sorted((views or {}).items()):
        h.update(img.read_bytes())
        h.update(mask.read_bytes())
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
    if getattr(engine, "logo_only", False) and not is_logo(job):
        raise RuntimeError("This is for logos and text. Turn on \"Logo or text\" in the Photo step first.")
    job.reset_after("confirmed")
    if is_flat(job):  # a flat object takes its depth from what you built: the picture, or the AI 3D shape
        job.manifest["print_params"]["flat_back"]["depth_from"] = "picture" if engine_id == "picture" else "shape"
        job.save()
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
        views = _views(job) if getattr(engine, "multi_view", False) else None
        inp = ReconstructInput(img, img.with_name(img.stem + "_mask.png"), seed, tmp, style=style, subject=job.manifest["subject"],
                               views=views, source=job.input_path())
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
        colours = current.get("colours")
        backer = (current.get("logo") or {}).get("backer")
        current = style.print_params(fmt)  # a new format starts from its own defaults…
        if colours:
            current["colours"] = colours  # …but keeps the colours you chose
        if backer:
            current["logo"]["backer"] = backer  # …and the plaque shape
    params = _deep_merge(style.print_params(current["format"]), current)  # fills keys older objects lack
    params = _deep_merge(params, overrides)
    if palette_active(job) and "max_colours" not in (overrides.get("regions") or {}):
        pal = palette_state(job)  # the approved palette: its size and its colours
        params.setdefault("regions", {}).update(
            max_colours=pal["max"], palette=[{"hex": c["hex"], "name": c["name"]} for c in pal["colours"]])
    elif not palette_active(job):
        params.setdefault("regions", {}).pop("palette", None)
    log.info("%s: preparing print (%s)", job.id, params["format"])
    t0 = time.perf_counter()
    with timing.stage("printify", format=params["format"], finish=params["geometry"].get("finish")):
        if logo_parts(job):
            report = _printify_logo(job, params, progress)
        else:
            report = printify_mod.run(job.path(RAW_MESH), job.path(PRINT_DIR), params, progress,
                                      mask_path=job.path("03_confirmed_mask.png"), image_path=job.path(CONFIRMED))
    _note_time(job, "printify", time.perf_counter() - t0)
    _snap_to_palette(job, report)
    job.manifest["print_params"] = params
    job.manifest["report"] = report
    job.manifest["exports"] = []
    for stale in ("model.3mf", "model.step"):
        job.path(PRINT_DIR, stale).unlink(missing_ok=True)
    if job.manifest["stage"] == "exported":
        job.manifest["stage"] = "printified"
    job.set_stage("printified")
    return report


def colours_of(job: Job) -> dict[str, dict[str, str]]:
    """Each part's filament colour, in filament order: the ones you chose, else the defaults.
    Objects: body and base (the style's colours). Logos: each logo colour (the artwork's own) and the plaque."""
    palette = get_style(job.manifest["style"]["id"]).palette
    params = job.manifest.get("print_params") or {}
    chosen = params.get("colours") or {}
    defaults: list[tuple[str, str, dict[str, str]]] = []
    if logo_parts(job):
        try:
            for i, c in enumerate(logo_mod.palette(logo_data(job))):
                defaults.append((f"logo_{i + 1}", f"Logo colour {i + 1}", {**c, "artwork": c["name"]}))
        except (OSError, ValueError) as e:
            log.warning("%s: couldn't read the logo: %s", job.id, e)
        backer = (params.get("logo") or {}).get("backer", "plaque")
        defaults.append(("plaque", "Plaque" if backer == "plaque" else "Backing", dict(palette["base"])))
    elif regions := region_parts(job):  # picture colours: one filament per colour region
        for i, r in enumerate(regions):
            defaults.append((r["id"], f"Picture colour {i + 1}", {"name": r["name"], "hex": r["hex"], "artwork": r["name"]}))
    else:
        defaults += [("body", "Body", dict(palette["body"])), ("base", "Base / back", dict(palette["base"]))]
    return {pid: {**d, **(chosen.get(pid) or {}), "label": label} for pid, label, d in defaults}


def set_colours(job: Job, colours: dict[str, dict[str, str]] | None) -> None:
    """Choose the object's colours (None: back to the style's). No rebuild: colour isn't geometry.
    Files already exported had the old colours, so they're cleared."""
    from .core import colours as colours_mod

    params = job.manifest.get("print_params") or get_style(job.manifest["style"]["id"]).print_params()
    if colours is None:
        params.pop("colours", None)
    else:
        merged = dict(params.get("colours") or {})
        known = colours_of(job)
        for part, c in colours.items():
            if not c or part not in known:
                continue
            hexv = c["hex"].upper()
            rgb = tuple(int(hexv[i:i + 2], 16) for i in (1, 3, 5))
            merged[part] = {"hex": hexv, "name": (c.get("name") or "").strip() or colours_mod.name_for(rgb)}
        params["colours"] = merged
    job.manifest["print_params"] = params
    for stale in ("model.3mf", "model.step"):  # the files that carry colours; the print STLs stay
        job.path(PRINT_DIR, stale).unlink(missing_ok=True)
    job.manifest["exports"] = []
    if job.manifest["stage"] == "exported":
        job.manifest["stage"] = "printified"
    job.save()


def export(job: Job, formats: list[str]) -> list[str]:
    if not job.reached("printified"):
        raise RuntimeError("Prepare the print first.")
    t0 = time.perf_counter()
    with timing.stage("export"):
        written = export_mod.export(job.path(PRINT_DIR), job.manifest["name"], colours_of(job), formats)
    _note_time(job, "export", time.perf_counter() - t0)
    job.manifest["exports"] = [f"{PRINT_DIR}/{w}" for w in written]
    job.set_stage("exported")
    return job.manifest["exports"]
