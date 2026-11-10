"""Style specs: loading and a content hash for reproducibility."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

STYLES_DIR = Path(__file__).resolve().parent.parent / "styles"


@dataclass(frozen=True)
class StyleSpec:
    name: str
    version: int
    prompt: str
    negative_prompt: str
    reference_image: Path
    seed_base: int
    candidates: int
    steps: int
    guidance_scale: float
    ip_adapter_scale: float
    controlnet_scale: float
    image_size: int
    palette: dict[str, str]
    height_mm: float
    base_height_mm: float
    base_margin_mm: float
    base_fillet_mm: float
    cut_fraction: float
    min_feature_mm: float
    smooth_iters: int
    target_faces: int
    min_wall_mm: float
    magnet: str | None
    models: dict[str, str] = field(default_factory=dict)
    source_dir: Path = Path(".")

    def content_hash(self) -> str:
        """Hash of every value that affects output. Paths are excluded so a
        moved style folder keeps the same hash; the reference image's bytes
        are included instead."""
        data = asdict(self)
        data.pop("source_dir")
        data.pop("reference_image")
        data["reference_sha256"] = hashlib.sha256(
            self.reference_path.read_bytes()
        ).hexdigest()
        blob = json.dumps(data, sort_keys=True, default=str).encode()
        return hashlib.sha256(blob).hexdigest()

    @property
    def reference_path(self) -> Path:
        return self.source_dir / self.reference_image


def load_style(name: str, styles_dir: Path | None = None) -> StyleSpec:
    folder = (styles_dir or STYLES_DIR) / name
    yaml_path = folder / "style.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(f"No style '{name}' at {yaml_path}")
    raw: dict[str, Any] = yaml.safe_load(yaml_path.read_text())
    spec = StyleSpec(
        name=raw["name"],
        version=int(raw["version"]),
        prompt=raw["prompt"].strip(),
        negative_prompt=raw["negative_prompt"].strip(),
        reference_image=Path(raw["reference_image"]),
        seed_base=int(raw["seed_base"]),
        candidates=int(raw["candidates"]),
        steps=int(raw["steps"]),
        guidance_scale=float(raw["guidance_scale"]),
        ip_adapter_scale=float(raw["ip_adapter_scale"]),
        controlnet_scale=float(raw["controlnet_scale"]),
        image_size=int(raw["image_size"]),
        palette={k: str(v) for k, v in raw["palette"].items()},
        height_mm=float(raw["height_mm"]),
        base_height_mm=float(raw["base_height_mm"]),
        base_margin_mm=float(raw["base_margin_mm"]),
        base_fillet_mm=float(raw["base_fillet_mm"]),
        cut_fraction=float(raw["cut_fraction"]),
        min_feature_mm=float(raw["min_feature_mm"]),
        smooth_iters=int(raw["smooth_iters"]),
        target_faces=int(raw["target_faces"]),
        min_wall_mm=float(raw["min_wall_mm"]),
        magnet=raw.get("magnet"),
        models=dict(raw.get("models", {})),
        source_dir=folder,
    )
    if not spec.reference_path.exists():
        raise FileNotFoundError(f"Style reference image missing: {spec.reference_path}")
    if not 0 <= spec.cut_fraction < 0.5:
        raise ValueError("cut_fraction must be in [0, 0.5)")
    if spec.base_height_mm >= spec.height_mm:
        raise ValueError("base_height_mm must be smaller than height_mm")
    return spec
