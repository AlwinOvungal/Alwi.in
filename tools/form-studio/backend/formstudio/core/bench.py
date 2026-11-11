"""Speed test: time the whole photo → 3MF flow on this Mac, so speed changes are judged by numbers.

Each run: cut-out → first option → the rest of the options → pick the first → 3D shape → print parts →
3MF → one print-slider change. The first run of each variant starts with no models in memory (cold);
the rest are warm. Results: median and p90 per measure, each print file checked, and a sheet of the
options each variant made, so speed never hides a worse look.

Variants:
  before  how Form Studio worked before: one model in memory at a time, Best quality, no print cache
  best    now, Best quality
  quick   now, Quick quality (SDXL-Lightning), local SDXL only
"""
from __future__ import annotations

import json
import logging
import re
import statistics
import time
import zipfile
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageFont

from .. import paths
from . import printify as printify_mod
from . import timing
from .job import Job

log = logging.getLogger(__name__)

Progress = Callable[[float | None, str], None]

VARIANTS: dict[str, dict[str, Any]] = {
    "before": {"label": "Before", "keep_loaded": "one", "quality": "best", "caches": False},
    "best": {"label": "Now · Best", "keep_loaded": "auto", "quality": "best", "caches": True},
    "quick": {"label": "Now · Quick", "keep_loaded": "auto", "quality": "quick", "caches": True},
}

MEASURES = [
    ("first_option", "Photo → first option"),
    ("first_to_3mf", "Photo → 3MF, using the first option right away"),
    ("all_to_3mf", "Photo → 3MF after all options"),
    ("slider", "One print-slider change"),
]


def bench_dir() -> Path:
    return paths.home() / "bench"


def pick_photos(limit: int = 3) -> list[tuple[Path, str]]:
    """Photos (and what they show) of your most recent objects. The test works on copies: your objects never change."""
    out: list[tuple[Path, str]] = []
    for j in Job.all():
        if j.manifest.get("bench"):
            continue
        p = j.input_path()
        if p and p.exists():
            out.append((p, j.manifest.get("subject") or "object"))
        if len(out) >= limit:
            break
    return out


def photos_in(folder: Path) -> list[tuple[Path, str]]:
    exts = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
    return [(p, p.stem.replace("_", " ").replace("-", " ")) for p in sorted(folder.iterdir()) if p.suffix.lower() in exts]


def _check(job: Job) -> dict[str, Any]:
    """Is the print right? Watertight, the right height, and two parts on two filaments in the 3MF."""
    report = job.manifest.get("report") or {}
    params = job.manifest.get("print_params") or {}
    want = float((params.get("geometry") or {}).get("height_mm", 0))
    got = float((report.get("size_mm") or [0, 0, 0])[2])
    out = {"watertight": bool(report.get("watertight")), "height_ok": abs(got - want) <= 0.3 if want else True,
           "height_mm": got, "parts_ok": None}
    threemf = next((job.path(e) for e in job.manifest.get("exports") or [] if e.endswith(".3mf")), None)
    if threemf and threemf.exists():
        with zipfile.ZipFile(threemf) as z:
            cfg = z.read("Metadata/model_settings.config").decode() if "Metadata/model_settings.config" in z.namelist() else ""
        extruders = set(re.findall(r'key="extruder" value="(\d+)"', cfg))
        uses_base = bool((params.get("base") or {}).get("enabled")) and not (params.get("flat_back") or {}).get("enabled")
        out["parts_ok"] = extruders >= ({"1", "2"} if uses_base else {"1"})
    out["ok"] = out["watertight"] and out["height_ok"] and out["parts_ok"] is not False
    return out


def _one(photo: Path, subject: str, ctx, style_id: str, stylize_engine: str, reconstruct_engine: str, seeds: list[int],
         say: Callable[[str], None]) -> tuple[dict[str, float], dict[str, Any], list[Path], list[dict], Job | None]:
    from .. import pipeline

    t: dict[str, float] = {}
    quiet: Progress = lambda p, m: say(m)  # noqa: E731
    with timing.collect() as rows:
        t0 = time.perf_counter()
        say("Cutting out the photo")
        job = pipeline.create(photo, "Speed test", subject, style_id)
        job.manifest["bench"] = True
        job.save()
        t["cutout"] = time.perf_counter() - t0
        try:
            _steps(job, t, ctx, stylize_engine, reconstruct_engine, seeds, quiet, say)
        except BaseException:
            job.delete()
            raise
    t["first_to_3mf"] = t["first_option"] + t["shape"] + t["export"]
    t["all_to_3mf"] = t["first_to_3mf"] + t["more_options"]
    images = [job.path(c["file"]) for c in job.manifest.get("candidates") or []]
    return t, _check(job), images, list(rows), job


def _steps(job: Job, t: dict[str, float], ctx, stylize_engine: str, reconstruct_engine: str, seeds: list[int],
           quiet: Progress, say: Callable[[str], None]) -> None:
    from .. import pipeline

    t0 = time.perf_counter()
    pipeline.stylize(job, ctx, stylize_engine, seeds[:1], quiet)
    t["first_option"] = time.perf_counter() - t0 + t["cutout"]
    t["more_options"] = 0.0
    if len(seeds) > 1:
        t0 = time.perf_counter()
        pipeline.stylize(job, ctx, stylize_engine, seeds[1:], quiet)
        t["more_options"] = time.perf_counter() - t0

    pipeline.confirm(job, 0)
    t0 = time.perf_counter()
    pipeline.reconstruct(job, ctx, reconstruct_engine, quiet)  # includes the print parts
    t["shape"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    say("Writing the 3MF")
    pipeline.export(job, ["3mf"])
    t["export"] = time.perf_counter() - t0

    margin = float(((job.manifest.get("print_params") or {}).get("base") or {}).get("margin_mm", 3))
    t0 = time.perf_counter()
    pipeline.printify(job, {"base": {"margin_mm": margin + 1}})
    t["slider"] = time.perf_counter() - t0


def _summary(values: list[float]) -> dict[str, float | None]:
    return {
        "median": round(statistics.median(values), 1) if values else None,
        "p90": round(timing.p90(values), 1) if values else None,
        "n": len(values),
    }


def run(ctx, photos: list[tuple[Path, str]], *, style_id: str, stylize_engine: str, reconstruct_engine: str,
        variants: list[str], runs: int = 2, options: int = 4, progress: Progress | None = None,
        out_dir: Path | None = None) -> dict[str, Any]:
    from .. import pipeline
    from ..engines import local

    say_p = progress or (lambda p, m: None)
    if not photos:
        raise RuntimeError("Add at least one object first: the speed test uses your photos.")
    variants = [v for v in variants if v in VARIANTS]
    skipped = []
    if stylize_engine != "local-sdxl":  # quality only applies to local SDXL
        skipped = [v for v in variants if v == "quick"]
        variants = [v for v in variants if v != "quick"]
    seeds = [11, 23, 37, 51, 61, 73, 89, 97][: max(1, options)]
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    out_dir = out_dir or bench_dir() / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    total = len(variants) * runs * len(photos)
    done = 0
    results: dict[str, Any] = {}
    shape_cache = pipeline.SHAPE_CACHE["enabled"]
    print_cache = printify_mod.CACHE["enabled"]
    keep = ctx.settings.get("keep_loaded", "auto")
    try:
        pipeline.SHAPE_CACHE["enabled"] = False  # every run builds its shape for real
        for vid in variants:
            v = VARIANTS[vid]
            vctx = type(ctx)(settings={**ctx.settings, "sdxl_quality": v["quality"], "keep_loaded": v["keep_loaded"]},
                             device=ctx.device, secrets=ctx.secrets)
            local.configure(vctx.settings)
            printify_mod.CACHE["enabled"] = v["caches"]
            printify_mod.clear_cache()
            local.unload_all()  # the first run of each variant starts cold
            per_run: list[dict[str, Any]] = []
            stage_rows: list[dict] = []
            sheet: list[Path] = []
            for r in range(runs):
                for i, (photo, subject) in enumerate(photos):
                    label = f"{v['label']} · run {r + 1} of {runs} · photo {i + 1} of {len(photos)}"

                    def say(m: str, label=label) -> None:
                        say_p(done / total if total else None, f"{label}: {m}")

                    job = None
                    try:
                        t, check, images, rows, job = _one(photo, subject, vctx, style_id, stylize_engine, reconstruct_engine, seeds, say)
                        if r == 0 and i == 0:
                            for k, img in enumerate(images):
                                dst = out_dir / f"options_{vid}_{k + 1}.png"
                                Image.open(img).convert("RGB").resize((256, 256)).save(dst)
                                sheet.append(dst)
                        per_run.append({"run": r, "photo": i, "cold": r == 0 and i == 0, "times": t, "check": check})
                        stage_rows += rows
                    finally:
                        if job is not None:
                            job.delete()
                    done += 1
                    say_p(done / total, f"{label}: done")
            warm = [x for x in per_run if not x["cold"]]
            cold = next((x for x in per_run if x["cold"]), None)
            stages: dict[str, list[float]] = {}
            for row in stage_rows:
                if row.get("outcome", "ok") == "ok" and not row["stage"].startswith("task."):
                    stages.setdefault(row["stage"], []).append(float(row["seconds"]))
            results[vid] = {
                "label": v["label"],
                "settings": {k: v[k] for k in ("keep_loaded", "quality", "caches")},
                "warm": {m: _summary([x["times"][m] for x in warm]) for m, _ in MEASURES},
                "cold": {m: round(cold["times"][m], 1) for m, _ in MEASURES} if cold else None,
                "stages": {k: _summary(vs) for k, vs in sorted(stages.items())},
                "checks_ok": all(x["check"]["ok"] for x in per_run),
                "checks": [x["check"] for x in per_run],
                "sheet": [p.name for p in sheet],
            }
    finally:
        pipeline.SHAPE_CACHE["enabled"] = shape_cache
        printify_mod.CACHE["enabled"] = print_cache
        local.configure({**ctx.settings, "keep_loaded": keep})

    from ..engines import local as _local

    report = {
        "when": stamp,
        "mac": {"ram_gb": round(_local.ram_gb() or 0), "device": ctx.device},
        "setup": {"style": style_id, "restyle": stylize_engine, "shape": reconstruct_engine,
                  "photos": len(photos), "runs": runs, "options": len(seeds)},
        "variants": results,
        "skipped": skipped,
        "folder": str(out_dir),
    }
    if results:
        _write_sheet(out_dir, results)
        report["sheet"] = "options.png" if (out_dir / "options.png").exists() else None
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))
    (out_dir / "report.md").write_text(to_markdown(report))
    return report


def _write_sheet(out_dir: Path, results: dict[str, Any]) -> None:
    rows = [(r["label"], [out_dir / f for f in r["sheet"]]) for r in results.values() if r["sheet"]]
    if not rows:
        return
    cols = max(len(fs) for _, fs in rows)
    cell, pad, label_w = 256, 12, 170
    sheet = Image.new("RGB", (label_w + cols * (cell + pad) + pad, len(rows) * (cell + pad) + pad), (246, 245, 241))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.load_default(size=22)
    except TypeError:  # older Pillow
        font = ImageFont.load_default()
    for y, (label, files) in enumerate(rows):
        top = pad + y * (cell + pad)
        draw.text((pad, top + cell // 2 - 12), label, fill=(27, 27, 24), font=font)
        for x, f in enumerate(files):
            sheet.paste(Image.open(f), (label_w + pad + x * (cell + pad), top))
    sheet.save(out_dir / "options.png")


def _fmt(s: float | None) -> str:
    if s is None:
        return "—"
    return f"{s:.1f} s" if s < 90 else f"{int(s // 60)} min {int(round(s % 60)):02d} s"


def to_markdown(report: dict[str, Any]) -> str:
    vs = report["variants"]
    ids = list(vs)
    setup = report["setup"]
    lines = [
        f"# Form Studio speed test · {report['when'].replace('_', ' ')}",
        "",
        f"Mac: {report['mac']['ram_gb']} GB, `{report['mac']['device']}` · restyle `{setup['restyle']}` · 3D "
        f"`{setup['shape']}` · style `{setup['style']}` · {setup['photos']} photo(s) × {setup['runs']} run(s) · "
        f"{setup['options']} options per object",
        "",
        "## Warm runs (models already in memory): median / p90",
        "",
        "| Measure | " + " | ".join(vs[v]["label"] for v in ids) + " |",
        "|---|" + "---:|" * len(ids),
    ]
    for m, title in MEASURES:
        cells = []
        for v in ids:
            w = vs[v]["warm"][m]
            cells.append(f"{_fmt(w['median'])} / {_fmt(w['p90'])}" if w["n"] else "—")
        lines.append(f"| {title} | " + " | ".join(cells) + " |")
    lines += ["", "## First run of the session (cold)", "", "| Measure | " + " | ".join(vs[v]["label"] for v in ids) + " |",
              "|---|" + "---:|" * len(ids)]
    for m, title in MEASURES:
        lines.append(f"| {title} | " + " | ".join(_fmt((vs[v]["cold"] or {}).get(m)) for v in ids) + " |")
    if len(ids) > 1:
        base = ids[0]
        lines += ["", f"## Speed-up compared with {vs[base]['label']}", ""]
        for v in ids[1:]:
            parts = []
            for m, title in MEASURES[:3]:
                a = vs[base]["warm"][m]["median"] or (vs[base]["cold"] or {}).get(m)
                b = vs[v]["warm"][m]["median"] or (vs[v]["cold"] or {}).get(m)
                if a and b:
                    parts.append(f"{title.lower()}: **{a / b:.1f}×**")
            lines.append(f"- {vs[v]['label']}: " + "; ".join(parts))
    lines += ["", "## Print checks", ""]
    for v in ids:
        bad = [c for c in vs[v]["checks"] if not c["ok"]]
        lines.append(f"- {vs[v]['label']}: " + ("all print files watertight, right height, parts on the right filaments ✓"
                                               if not bad else f"{len(bad)} problem(s): {bad}"))
    lines += ["", "## Where the time goes (median seconds per step)", "",
              "| Step | " + " | ".join(vs[v]["label"] for v in ids) + " |", "|---|" + "---:|" * len(ids)]
    names = sorted({k for v in ids for k in vs[v]["stages"]})
    for k in names:
        lines.append(f"| {k} | " + " | ".join(_fmt((vs[v]["stages"].get(k) or {}).get("median")) for v in ids) + " |")
    if report.get("skipped"):
        lines += ["", f"Skipped {', '.join(report['skipped'])}: Quick quality applies to the local SDXL engine only."]
    if report.get("sheet"):
        lines += ["", "The options each variant made are in `options.png` next to this report: check the look too."]
    return "\n".join(lines) + "\n"


def latest() -> dict[str, Any] | None:
    reports = sorted(bench_dir().glob("*/report.json"))
    if not reports:
        return None
    try:
        return json.loads(reports[-1].read_text())
    except (OSError, json.JSONDecodeError):
        return None
