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


@dataclass
class ReconstructInput:
    image: Path      # RGB on white
    mask: Path       # L, object = white
    seed: int
    out: Path        # where to write the GLB (y-up)
    style: StyleSpec | None = None
    subject: str = ""


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
    return {"id": e.id, "label": e.label, "kind": e.kind, "note": e.note, "available": ok, "reason": why}


def missing(*modules: str) -> str | None:
    import importlib.util

    gone = [m for m in modules if importlib.util.find_spec(m) is None]
    return f"Not installed: {', '.join(gone)}. Run scripts/setup-mac.sh." if gone else None
