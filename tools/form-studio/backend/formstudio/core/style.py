"""Style presets: everything that decides how an object looks, loaded from style.yaml.

Bundled styles live in the repo (tools/form-studio/styles/) and are read-only.
Your own styles live in ~/FormStudio/styles/<id>/ and can be edited in the app.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

from .. import paths

MAX_REFERENCES = 4
TEMPLATE_STYLE = "clay-minimal"


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
    sculpt_guide: str
    seeds: list[int]
    stylize: dict[str, Any]
    palette: dict[str, dict[str, str]]
    geometry: dict[str, Any]
    base: dict[str, Any]
    magnet: dict[str, Any]
    references: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def editable(self) -> bool:
        return paths.home() / "styles" in self.folder.parents

    @property
    def reference_paths(self) -> list[Path]:
        return [p for p in (self.folder / r for r in self.references) if p.exists()]

    @property
    def reference(self) -> Path | None:
        """The first reference image (local SDXL uses one)."""
        refs = self.reference_paths
        return refs[0] if refs else None

    @property
    def hash(self) -> str:
        """Content hash of the spec and its reference images, recorded in each job's manifest."""
        h = hashlib.sha256(json.dumps(self.raw, sort_keys=True).encode())
        for p in self.reference_paths:
            h.update(p.read_bytes())
        return h.hexdigest()[:12]

    def prompt_for(self, subject: str) -> str:
        return self.prompt.replace("{subject}", subject or "object")

    def edit_prompt_for(self, subject: str) -> str:
        return self.edit_prompt.replace("{subject}", subject or "object")

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
            "editable": self.editable,
            "references": len(self.reference_paths),
            "has_reference": bool(self.reference_paths),
            "finish": self.geometry.get("finish", "soft"),
            "defaults": self.print_params(),
            "formats": [{"id": k, "label": v.get("label", k.title())} for k, v in self.formats.items()],
        }

    def detail(self) -> dict[str, Any]:
        """Everything the style editor shows."""
        return {
            **self.summary(),
            "prompt": self.prompt,
            "edit_prompt": self.edit_prompt,
            "negative_prompt": self.negative_prompt,
            "sculpt_guide": self.sculpt_guide,
            "geometry": {
                **{k: self.geometry.get(k) for k in ("height_mm", "soften_mm", "min_feature_mm")},
                "finish": self.geometry.get("finish", "soft"),
                "voxel_size_mm": self.geometry.get("voxel_size_mm", 2.5),
                "facets": self.geometry.get("facets", 400),
            },
        }


def load_style(folder: Path) -> StyleSpec:
    raw = yaml.safe_load((folder / "style.yaml").read_text())
    refs = raw.get("references")
    if refs is None:
        refs = [raw["reference"]] if raw.get("reference") else []
    return StyleSpec(
        id=raw["id"],
        name=raw.get("name", raw["id"]),
        version=int(raw.get("version", 1)),
        description=raw.get("description", ""),
        folder=folder,
        prompt=raw["prompt"].strip(),
        negative_prompt=raw.get("negative_prompt", "").strip(),
        edit_prompt=raw.get("edit_prompt", raw["prompt"]).strip(),
        sculpt_guide=(raw.get("sculpt_guide") or raw.get("description") or "").strip(),
        seeds=[int(s) for s in raw.get("seeds", [11, 23, 37, 51])],
        stylize=raw.get("stylize", {}),
        palette=raw["palette"],
        geometry=raw.get("geometry", {}),
        base=raw.get("base", {"enabled": False}),
        magnet=raw.get("magnet", {"enabled": False}),
        references=[str(r) for r in refs],
        raw=raw,
    )


def user_styles_dir() -> Path:
    d = paths.home() / "styles"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_styles() -> list[StyleSpec]:
    styles: dict[str, StyleSpec] = {}
    for root in reversed(paths.style_dirs()):  # bundled first in the list, then yours
        if not root.exists():
            continue
        for d in sorted(root.iterdir()):
            if (d / "style.yaml").exists():
                try:
                    s = load_style(d)
                except (yaml.YAMLError, KeyError, TypeError, ValueError):
                    continue  # a broken style file shouldn't break the app
                styles[s.id] = s
    return list(styles.values())


def get_style(style_id: str) -> StyleSpec:
    for s in list_styles():
        if s.id == style_id:
            return s
    raise KeyError(f"Unknown style '{style_id}'")


# ---- editing your own styles -------------------------------------------------------

class Colour(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    hex: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")


class StylePalette(BaseModel):
    body: Colour
    base: Colour


class StyleGeometry(BaseModel):
    height_mm: float = Field(ge=10, le=300)
    soften_mm: float = Field(ge=0, le=3)
    min_feature_mm: float = Field(ge=0.6, le=6)
    finish: Literal["soft", "voxel", "faceted"] = "soft"
    voxel_size_mm: float = Field(default=2.5, ge=0.8, le=10)
    facets: int = Field(default=400, ge=60, le=5000)


class StyleEdit(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = ""
    prompt: str | None = None
    edit_prompt: str | None = None
    negative_prompt: str | None = None
    sculpt_guide: str | None = None
    palette: StylePalette | None = None
    geometry: StyleGeometry | None = None
    based_on: str | None = None  # create only: start from this style's words, finish and formats


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "style"


def _unique_id(name: str) -> str:
    base = _slug(name)
    taken = {s.id for s in list_styles()} | {"new"}  # "new" is the New style page's address
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def _write(folder: Path, raw: dict[str, Any]) -> StyleSpec:
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / "style.yaml.tmp"
    tmp.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    tmp.replace(folder / "style.yaml")
    return load_style(folder)


def _apply(raw: dict[str, Any], edit: StyleEdit) -> None:
    raw["name"] = edit.name.strip()
    raw["description"] = edit.description.strip()
    for key in ("prompt", "edit_prompt", "negative_prompt", "sculpt_guide"):
        value = getattr(edit, key)
        if value is not None:
            value = value.strip()
            if key in ("prompt", "edit_prompt") and "{subject}" not in value:
                value = "a {subject}, " + value
            raw[key] = value
    if edit.palette:
        raw["palette"] = edit.palette.model_dump()
    if edit.geometry:
        raw.setdefault("geometry", {}).update(edit.geometry.model_dump())


def _require_editable(style: StyleSpec) -> None:
    if not style.editable:
        raise PermissionError("Built-in styles can't be changed. Duplicate it to make your own version.")


def create_style(edit: StyleEdit) -> StyleSpec:
    raw = copy.deepcopy(get_style(edit.based_on or TEMPLATE_STYLE).raw)
    raw.pop("reference", None)
    raw["references"] = []
    raw["id"] = _unique_id(edit.name)
    raw["version"] = 1
    _apply(raw, edit)
    return _write(user_styles_dir() / raw["id"], raw)


def update_style(style_id: str, edit: StyleEdit) -> StyleSpec:
    style = get_style(style_id)
    _require_editable(style)
    raw = copy.deepcopy(style.raw)
    _apply(raw, edit)
    raw["version"] = style.version + 1
    return _write(style.folder, raw)


def duplicate_style(style_id: str) -> StyleSpec:
    src = get_style(style_id)
    raw = copy.deepcopy(src.raw)
    raw.pop("reference", None)
    raw["name"] = f"{src.name} copy"
    raw["id"] = _unique_id(raw["name"])
    raw["version"] = 1
    folder = user_styles_dir() / raw["id"]
    folder.mkdir(parents=True)
    raw["references"] = []
    for i, p in enumerate(src.reference_paths):
        name = f"reference_{i + 1}{p.suffix.lower()}"
        shutil.copyfile(p, folder / name)
        raw["references"].append(name)
    return _write(folder, raw)


def delete_style(style_id: str) -> None:
    style = get_style(style_id)
    _require_editable(style)
    shutil.rmtree(style.folder)


def add_reference(style_id: str, data: bytes) -> StyleSpec:
    style = get_style(style_id)
    _require_editable(style)
    if len(style.references) >= MAX_REFERENCES:
        raise ValueError(f"A style can have up to {MAX_REFERENCES} reference images.")
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    except Exception:
        raise ValueError("That file isn't an image Form Studio can read. Use JPEG, PNG or WebP.")
    img.thumbnail((1600, 1600))
    n = 1
    while (style.folder / f"reference_{n}.png").exists():
        n += 1
    name = f"reference_{n}.png"
    img.save(style.folder / name)
    raw = copy.deepcopy(style.raw)
    raw.pop("reference", None)
    raw["references"] = style.references + [name]
    raw["version"] = style.version + 1
    return _write(style.folder, raw)


def add_references(style_id: str, files: list[bytes]) -> StyleSpec:
    """Several pictures at once; refuses the whole upload if it would pass the limit."""
    style = get_style(style_id)
    _require_editable(style)
    room = MAX_REFERENCES - len(style.references)
    if len(files) > room:
        raise ValueError(
            f"A style can have up to {MAX_REFERENCES} reference pictures; there's room for {max(room, 0)} more.")
    for data in files:
        style = add_reference(style_id, data)
    return style


def make_reference_first(style_id: str, index: int) -> StyleSpec:
    style = get_style(style_id)
    _require_editable(style)
    if not 0 <= index < len(style.references):
        raise IndexError("No such reference image.")
    refs = list(style.references)
    refs.insert(0, refs.pop(index))
    raw = copy.deepcopy(style.raw)
    raw.pop("reference", None)
    raw["references"] = refs
    raw["version"] = style.version + 1
    return _write(style.folder, raw)


def remove_reference(style_id: str, index: int) -> StyleSpec:
    style = get_style(style_id)
    _require_editable(style)
    if not 0 <= index < len(style.references):
        raise IndexError("No such reference image.")
    name = style.references[index]
    (style.folder / name).unlink(missing_ok=True)
    raw = copy.deepcopy(style.raw)
    raw.pop("reference", None)
    raw["references"] = [r for i, r in enumerate(style.references) if i != index]
    raw["version"] = style.version + 1
    return _write(style.folder, raw)
