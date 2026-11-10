"""Job folders: one folder per object, with stage outputs and a manifest."""

from __future__ import annotations

import datetime as dt
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .style import StyleSpec

MANIFEST = "manifest.json"


@dataclass
class Job:
    root: Path

    # Stage paths
    @property
    def input(self) -> Path: return self.root / "00_input.jpg"
    @property
    def cutout(self) -> Path: return self.root / "01_cutout.png"
    @property
    def candidates(self) -> Path: return self.root / "02_candidates"
    @property
    def confirmed(self) -> Path: return self.root / "03_confirmed.png"
    @property
    def raw_glb(self) -> Path: return self.root / "04_raw.glb"
    @property
    def print_dir(self) -> Path: return self.root / "05_print"
    @property
    def manifest_path(self) -> Path: return self.root / MANIFEST

    def candidate(self, index: int) -> Path:
        return self.candidates / f"{index}.png"

    # Manifest
    def read(self) -> dict[str, Any]:
        return json.loads(self.manifest_path.read_text())

    def write(self, data: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
        tmp.replace(self.manifest_path)

    def record(self, stage: str, **info: Any) -> None:
        data = self.read()
        data.setdefault("stages", {})[stage] = {
            "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            **info,
        }
        self.write(data)

    def stage_done(self, stage: str) -> bool:
        return stage in self.read().get("stages", {})

    def reset_from(self, stage: str) -> None:
        """Forget a stage and everything after it, so it reruns on resume."""
        order = ["ingest", "stylize", "confirm", "reconstruct", "printify", "export"]
        data = self.read()
        for s in order[order.index(stage):]:
            data.get("stages", {}).pop(s, None)
        self.write(data)


def create_job(root: Path, photo: Path, style: StyleSpec) -> Job:
    if (root / MANIFEST).exists():
        raise FileExistsError(f"Job already exists: {root}")
    root.mkdir(parents=True, exist_ok=True)
    job = Job(root)
    shutil.copyfile(photo, job.input)
    job.write(
        {
            "job": root.name,
            "created": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "source_photo": str(photo.resolve()),
            "style": {"name": style.name, "version": style.version, "hash": style.content_hash()},
            "params": {
                "seed_base": style.seed_base,
                "candidates": style.candidates,
                "steps": style.steps,
                "guidance_scale": style.guidance_scale,
                "ip_adapter_scale": style.ip_adapter_scale,
                "controlnet_scale": style.controlnet_scale,
                "image_size": style.image_size,
                "models": style.models,
            },
            "stages": {},
        }
    )
    return job


def open_job(root: Path) -> Job:
    job = Job(root)
    if not job.manifest_path.exists():
        raise FileNotFoundError(f"Not a job folder (no {MANIFEST}): {root}")
    return job
