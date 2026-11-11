"""Clean up: find and remove files Form Studio no longer needs.

Nothing here touches your objects' pictures, 3D shapes or print files, your styles, or a model
your engines use. Everything listed is either a leftover, a cache that's made again when needed,
or a model none of your engines use (downloadable again from Settings → Local AI).
"""
from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path
from typing import Any

from .. import paths

TEMP_PATTERNS = ("02_candidates/.run*", "03_views.tmp", "04_raw.tmp.glb", "04_upload.png", "manifest.json.tmp")


def _size(p: Path) -> int:
    if not p.exists():
        return 0
    if p.is_file():
        return p.stat().st_size
    total = 0
    for root, _dirs, files in os.walk(p, followlinks=False):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def _remove(p: Path) -> None:
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p, ignore_errors=True)
    else:
        p.unlink(missing_ok=True)


def _temp_files() -> list[Path]:
    out: list[Path] = []
    jobs = paths.jobs_dir()
    if jobs.exists():
        for job in jobs.iterdir():
            for pattern in TEMP_PATTERNS:
                out += list(job.glob(pattern))
    return out


def _partial_downloads() -> list[Path]:
    from ..models import hub_cache

    return list(hub_cache().glob("models--*/blobs/*.incomplete"))


def _chunk_cache() -> list[Path]:
    from ..models import hub_cache

    d = hub_cache().parent / "xet"
    return [c for c in d.glob("*") if c.is_dir() and c.name != "logs"] if d.exists() else []


def _old_bench() -> list[Path]:
    reports = sorted(p for p in (paths.home() / "bench").glob("*") if p.is_dir())
    return reports[:-1]  # keep the latest speed test


def _pip_cache() -> Path:
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Caches" / "pip"
    return Path(os.environ.get("PIP_CACHE_DIR") or Path.home() / ".cache" / "pip")


def _unused_styles() -> list[Path]:
    """Your deleted styles that no object uses any more."""
    from .job import Job

    used = {j.manifest.get("style", {}).get("id") for j in Job.all()}
    d = paths.home() / "styles" / ".deleted"
    out = []
    for folder in d.glob("*") if d.exists() else []:
        try:
            import yaml

            sid = yaml.safe_load((folder / "style.yaml").read_text()).get("id")
        except Exception:
            sid = folder.name
        if sid not in used:
            out.append(folder)
    return out


def _unused_models(settings: dict[str, Any]) -> list[tuple[str, str, int]]:
    """(pack, label, bytes) for downloaded models none of your default engines use."""
    from .. import models

    need = set(models.packs_needed(settings))
    out = []
    for pack in models.all_packs():
        if pack in need:
            continue
        # measured on disk only: no asking Hugging Face what the files should be
        if pack in models.ENGINE_PACKS:
            size = models._dir_size(models._engine_path(pack))
        else:
            size = sum(models._repo_bytes(r) for r in models.PACKS[pack]["repos"])
        if size:
            label = (models.PACKS.get(pack) or models.ENGINE_PACKS.get(pack) or {}).get("label", pack)
            out.append((pack, label, int(size)))
    return out


def scan(settings: dict[str, Any]) -> list[dict[str, Any]]:
    """What can go, with sizes. Only things that are actually there."""
    items: list[dict[str, Any]] = []

    def add(id_: str, label: str, note: str, files: list[Path], extra: dict | None = None) -> None:
        size = sum(_size(f) for f in files)
        if size or files:
            items.append({"id": id_, "label": label, "note": note, "bytes": size, "count": len(files), **(extra or {})})

    add("temp", "Unfinished working files", "Left by steps that were stopped or crashed.", _temp_files())
    add("partial", "Unfinished model downloads", "Downloads that were stopped. Download starts them again.", _partial_downloads())
    add("chunk", "Download cache", "Copies Hugging Face keeps while downloading. Your models stay.", _chunk_cache())
    add("shape", "Saved 3D shapes", "Reused when you pick the same picture again; made again when needed.",
        [p for p in [paths.home() / "cache" / "shape"] if p.exists()])
    add("bench", "Older speed-test results", "The latest speed test is kept.", _old_bench())
    add("leftovers", "Files from the earlier version", "From before the app was reorganised. Safe to delete.",
        [p for p in [paths.home() / "old-version-leftovers"] if p.exists()])
    add("styles", "Deleted styles no object uses", "Styles you deleted that no object needs any more.", _unused_styles())
    pip = _pip_cache()
    add("pip", "Python download cache", "Only used when reinstalling the AI libraries.", [pip] if pip.exists() else [])
    unused = _unused_models(settings)
    if unused:
        items.append({
            "id": "models", "label": "Models your engines don't use",
            "note": ", ".join(label for _, label, _ in unused) + ". You can download them again in Settings → Local AI.",
            "bytes": sum(b for _, _, b in unused), "count": len(unused), "careful": True,
        })
    log_file = paths.home() / "timings.jsonl"
    if log_file.exists() and log_file.stat().st_size > 1024**2:
        items.append({"id": "timings", "label": "Old timing records", "note": "Keeps the latest 2,000 for time estimates.",
                      "bytes": log_file.stat().st_size, "count": 1})
    return [i for i in items if i["bytes"] > 0]


def clean(ids: list[str], settings: dict[str, Any]) -> int:
    """Remove the chosen groups. Returns bytes freed."""
    from .. import models

    freed = 0
    groups = {
        "temp": _temp_files, "partial": _partial_downloads, "chunk": _chunk_cache, "bench": _old_bench,
        "styles": _unused_styles,
        "shape": lambda: [p for p in [paths.home() / "cache" / "shape"] if p.exists()],
        "leftovers": lambda: [p for p in [paths.home() / "old-version-leftovers"] if p.exists()],
        "pip": lambda: [p for p in [_pip_cache()] if p.exists()],
    }
    for id_ in ids:
        if id_ in groups:
            for p in groups[id_]():
                freed += _size(p)
                _remove(p)
        elif id_ == "models":
            for pack, _, _ in _unused_models(settings):
                freed += models.delete(pack)
        elif id_ == "timings":
            f = paths.home() / "timings.jsonl"
            if f.exists():
                before = f.stat().st_size
                lines = f.read_text().splitlines()[-2000:]
                f.write_text("\n".join(lines) + ("\n" if lines else ""))
                freed += before - f.stat().st_size
    return freed
