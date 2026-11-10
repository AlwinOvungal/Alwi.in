"""A job is one object's folder on disk plus a manifest.json describing every step.

jobs/<id>/
  00_input.<ext>        the photo as uploaded
  01_cutout.png         background removed, centred, square on white
  02_candidates/*.png   styled options
  03_confirmed.png      the candidate you picked
  04_raw.glb            the AI mesh
  05_print/             body.stl, base.stl, model.3mf (+ model.step)
  manifest.json         style hash, seeds, engines, parameters → reproducible
"""
from __future__ import annotations

import json
import re
import secrets
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import paths

STAGES = ["new", "cutout", "stylized", "confirmed", "reconstructed", "printified", "exported"]

INPUT_STEM = "00_input"
CUTOUT = "01_cutout.png"
CANDIDATES = "02_candidates"
CONFIRMED = "03_confirmed.png"
RAW_MESH = "04_raw.glb"
PRINT_DIR = "05_print"
MANIFEST = "manifest.json"

_lock = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:40] or "object"


class Job:
    def __init__(self, folder: Path):
        self.folder = folder
        self.id = folder.name
        self.manifest: dict[str, Any] = json.loads((folder / MANIFEST).read_text())

    # ---- creation / lookup -------------------------------------------------
    @classmethod
    def create(cls, name: str, subject: str, style_id: str, style_hash: str) -> "Job":
        job_id = f"{slugify(name)}-{secrets.token_hex(3)}"
        folder = paths.jobs_dir() / job_id
        folder.mkdir(parents=True)
        manifest = {
            "id": job_id,
            "name": name,
            "subject": subject,
            "style": {"id": style_id, "hash": style_hash},
            "stage": "new",
            "created": _now(),
            "updated": _now(),
            "input": None,
            "candidates": [],
            "confirmed": None,
            "reconstruct": None,
            "print_params": None,
            "report": None,
            "exports": [],
            "history": [],
        }
        (folder / MANIFEST).write_text(json.dumps(manifest, indent=2))
        return cls(folder)

    @classmethod
    def load(cls, job_id: str) -> "Job":
        if not re.fullmatch(r"[a-z0-9-]+", job_id):
            raise KeyError(job_id)
        folder = paths.jobs_dir() / job_id
        if not (folder / MANIFEST).exists():
            raise KeyError(job_id)
        return cls(folder)

    @classmethod
    def all(cls) -> list["Job"]:
        jobs = []
        for d in paths.jobs_dir().iterdir():
            if (d / MANIFEST).exists():
                try:
                    jobs.append(cls(d))
                except (json.JSONDecodeError, OSError):
                    continue
        return sorted(jobs, key=lambda j: j.manifest.get("updated", ""), reverse=True)

    # ---- state ---------------------------------------------------------------
    def save(self) -> None:
        with _lock:
            self.manifest["updated"] = _now()
            tmp = self.folder / (MANIFEST + ".tmp")
            tmp.write_text(json.dumps(self.manifest, indent=2))
            tmp.replace(self.folder / MANIFEST)

    def update(self, **fields: Any) -> None:
        with _lock:
            self.manifest.update(fields)
            self.save()

    def set_stage(self, stage: str) -> None:
        self.manifest["stage"] = stage
        self.manifest["history"].append({"stage": stage, "at": _now()})
        self.save()

    def reached(self, stage: str) -> bool:
        return STAGES.index(self.manifest["stage"]) >= STAGES.index(stage)

    def reset_after(self, stage: str) -> None:
        """Going back to an earlier step clears what came after it."""
        idx = STAGES.index(stage)
        if idx < STAGES.index("stylized"):
            shutil.rmtree(self.path(CANDIDATES), ignore_errors=True)
            self.manifest["candidates"] = []
        if idx < STAGES.index("confirmed"):
            self.path(CONFIRMED).unlink(missing_ok=True)
            self.manifest["confirmed"] = None
        if idx < STAGES.index("reconstructed"):
            self.path(RAW_MESH).unlink(missing_ok=True)
            self.manifest["reconstruct"] = None
        if idx < STAGES.index("printified"):
            shutil.rmtree(self.path(PRINT_DIR), ignore_errors=True)
            self.manifest["report"] = None
            self.manifest["exports"] = []
        if STAGES.index(self.manifest["stage"]) > idx:
            self.manifest["stage"] = stage
        self.save()

    # ---- files ---------------------------------------------------------------
    def path(self, *parts: str) -> Path:
        return self.folder.joinpath(*parts)

    def input_path(self) -> Path | None:
        name = self.manifest.get("input")
        return self.path(name) if name else None

    def safe_file(self, rel: str) -> Path:
        """Resolve a user-supplied relative path, refusing anything outside the job folder."""
        p = (self.folder / rel).resolve()
        if self.folder.resolve() not in p.parents or not p.is_file():
            raise FileNotFoundError(rel)
        return p

    def delete(self) -> None:
        shutil.rmtree(self.folder)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.manifest)
        d["thumbnail"] = next(
            (f for f in (CONFIRMED, CUTOUT) if self.path(f).exists()), None
        )
        d["files"] = sorted(
            str(p.relative_to(self.folder))
            for p in self.folder.rglob("*")
            if p.is_file() and p.name != MANIFEST and not p.name.endswith(".tmp")
        )
        return d
