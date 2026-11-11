"""Style presets: everything that decides how an object looks, loaded from style.yaml."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .. import paths


@dataclass
class StyleSpec:
    id: str
    name: str
    version: int
    description: str
    folder: Path
    prompt: str
    negative_prompt: str
    edit_prompt: str
    seeds: list[int]
    stylize: dict[str, Any]
    palette: dict[str, dict[str, str]]
    geometry: dict[str, Any]
    base: dict[str, Any]
    magnet: dict[str, Any]
    reference: Path | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def hash(self) -> str:
        """Content hash of the spec and its reference image, recorded in each job's manifest."""
        h = hashlib.sha256(json.dumps(self.raw, sort_keys=True).encode())
        if self.reference and self.reference.exists():
            h.update(self.reference.read_bytes())
        return h.hexdigest()[:12]

    def prompt_for(self, subject: str) -> str:
        return self.prompt.format(subject=subject or "object")

    def edit_prompt_for(self, subject: str) -> str:
        return self.edit_prompt.format(subject=subject or "object")

    @property
    def formats(self) -> dict[str, dict[str, Any]]:
        f = self.raw.get("formats") or {}
        return f if "stand" in f else {"stand": {"label": "Stand"}, **f}

    def print_params(self, fmt: str = "stand") -> dict[str, Any]:
        """Default printify parameters for a format (the sliders in the Print step start here)."""
        if fmt not in self.formats:
            raise KeyError(f"Unknown format '{fmt}'")
        params: dict[str, Any] = {
            "format": fmt,
            "geometry": copy.deepcopy(self.geometry),
            "base": copy.deepcopy(self.base),
            "magnet": copy.deepcopy(self.magnet),
            "flat_back": {"enabled": False, "thickness_mm": 8},
            "ring": {"enabled": False, "outer_radius_mm": 4.5, "hole_mm": 4.5, "thickness_mm": 3.5},
        }
        for key, value in self.formats[fmt].items():
            if isinstance(value, dict) and isinstance(params.get(key), dict):
                params[key].update(copy.deepcopy(value))
        return params

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "hash": self.hash,
            "palette": self.palette,
            "seeds": self.seeds,
            "has_reference": bool(self.reference and self.reference.exists()),
            "defaults": self.print_params(),
            "formats": [{"id": k, "label": v.get("label", k.title())} for k, v in self.formats.items()],
        }


def load_style(folder: Path) -> StyleSpec:
    raw = yaml.safe_load((folder / "style.yaml").read_text())
    ref = raw.get("reference")
    return StyleSpec(
        id=raw["id"],
        name=raw.get("name", raw["id"]),
        version=int(raw.get("version", 1)),
        description=raw.get("description", ""),
        folder=folder,
        prompt=raw["prompt"].strip(),
        negative_prompt=raw.get("negative_prompt", "").strip(),
        edit_prompt=raw.get("edit_prompt", raw["prompt"]).strip(),
        seeds=[int(s) for s in raw.get("seeds", [11, 23, 37, 51])],
        stylize=raw.get("stylize", {}),
        palette=raw["palette"],
        geometry=raw.get("geometry", {}),
        base=raw.get("base", {"enabled": False}),
        magnet=raw.get("magnet", {"enabled": False}),
        reference=(folder / ref) if ref else None,
        raw=raw,
    )


def list_styles() -> list[StyleSpec]:
    styles: dict[str, StyleSpec] = {}
    for root in paths.style_dirs():
        if not root.exists():
            continue
        for d in sorted(root.iterdir()):
            if (d / "style.yaml").exists():
                s = load_style(d)
                styles.setdefault(s.id, s)  # user styles override bundled ones
    return list(styles.values())


def get_style(style_id: str) -> StyleSpec:
    for s in list_styles():
        if s.id == style_id:
            return s
    raise KeyError(f"Unknown style '{style_id}'")
