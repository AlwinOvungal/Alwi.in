"""Engines do the two AI steps. Each step has local, cloud and offline options.

Stylizer:      cutout photo → styled candidate images
Reconstructor: confirmed image → raw mesh (GLB, glTF y-up)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from ..core.style import StyleSpec

Progress = Callable[[float, str], None]


QUALITIES = ("best", "quick", "lightest")


def quality_of(settings: dict[str, Any]) -> str:
    """Local restyle quality: best (30 steps), quick (SDXL-Lightning, 8 steps) or lightest (SDXL-Turbo)."""
    q = settings.get("sdxl_quality")
    if q in QUALITIES:
        return q
    return "lightest" if settings.get("fast_mode") else "best"


class EngineError(RuntimeError):
    """A failure the person can act on: a plain message, the service's own words, and where to fix it."""

    def __init__(self, message: str, detail: str = "", links: list[tuple[str, str]] | None = None,
                 steps: list[str] | None = None):
        super().__init__(message)
        self.detail = detail
        self.links = links or []  # (label, url)
        self.steps = steps or []


class Cancelled(Exception):
    """The person pressed Cancel (or picked an option, which stops the rest). Raised from progress callbacks."""


@dataclass
class EngineContext:
    """What every engine call gets: user settings and helpers."""
    settings: dict[str, Any]
    device: str = "cpu"
    secrets: dict[str, str] = field(default_factory=dict)


@dataclass
class StylizeInput:
    cutout: Path
    mask: Path
    style: StyleSpec
    subject: str
    seeds: list[int]
    out_dir: Path
    source: Path | None = None  # the original upload (logos are read from it at full resolution)


@dataclass
class ReconstructInput:
    image: Path      # RGB on white
    mask: Path       # L, object = white
    seed: int
    out: Path        # where to write the GLB (y-up)
    style: StyleSpec | None = None
    subject: str = ""
    # front / left / back pictures of the same object (from "Make 3 views"), each (image on white, mask).
    # Engines that can use several views use them; the others use `image` (the front).
    views: dict[str, tuple[Path, Path]] | None = None
    source: Path | None = None  # the original upload (the logo engine builds from it)


VIEW_NAMES = ("front", "left", "back")

# AI image models love to answer with a product sheet; the 3D step needs one object in one view
ONE_OBJECT = ("Exactly one object, shown once in a single view - never a turnaround or product sheet, "
              "no labels, arrows, measurements or callout circles. ")
# styles never set colours: the object keeps its own (they're mapped to filaments in the Print step)
OWN_COLOURS = ("Keep the object's own colours from the first image, in the same places: the style changes "
               "shape, surface, texture and level of detail, never the colours. ")
SHEET_NEGATIVE = "multiple views, turnaround sheet, product sheet, annotations, measurements, dimension lines, text"
VIEW_LABELS = {"front": "Front", "left": "Side", "back": "Back"}


@dataclass
class ViewInput:
    image: Path      # the chosen picture, on white
    mask: Path
    style: StyleSpec
    subject: str
    out_dir: Path    # write front.png / left.png / back.png (or one sheet.png to be split)
    seed: int = 0


class Engine(Protocol):
    id: str
    label: str
    kind: str        # "local" | "cloud" | "offline"
    note: str

    def available(self, ctx: EngineContext) -> tuple[bool, str]: ...


class Stylizer(Engine, Protocol):
    def stylize(self, inp: StylizeInput, ctx: EngineContext, progress: Progress) -> list[dict]: ...


class Reconstructor(Engine, Protocol):
    def reconstruct(self, inp: ReconstructInput, ctx: EngineContext, progress: Progress) -> dict: ...


def describe(e: Engine, ctx: EngineContext) -> dict:
    ok, why = e.available(ctx)
    return {"id": e.id, "label": e.label, "kind": e.kind, "note": e.note, "available": ok, "reason": why,
            "multi_view": bool(getattr(e, "multi_view", False)), "logo_only": bool(getattr(e, "logo_only", False)),
            "flat_only": bool(getattr(e, "flat_only", False))}


def missing(*modules: str) -> str | None:
    import importlib.util

    gone = [m for m in modules if importlib.util.find_spec(m) is None]
    return f"Not installed: {', '.join(gone)}. Run scripts/setup-mac.sh." if gone else None
