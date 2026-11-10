"""Stage 4: confirmed image -> raw mesh (.glb, y-up, arbitrary scale)."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import trimesh


class Reconstructor(Protocol):
    name: str

    def reconstruct(self, image: Path, out_glb: Path) -> Path: ...


def _pick_device(preferred: str | None = None) -> str:
    import torch

    if preferred:
        return preferred
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


class StubReconstructor:
    """No model. Returns a plain cylinder so the whole pipeline can be
    exercised without a GPU or model weights."""

    name = "stub"

    def reconstruct(self, image: Path, out_glb: Path) -> Path:
        mesh = trimesh.creation.cylinder(radius=0.5, height=1.0, sections=48)
        mesh.apply_translation([0, 0.5, 0])  # y-up, sitting on y=0
        out_glb.parent.mkdir(parents=True, exist_ok=True)
        mesh.export(out_glb)
        return out_glb


class TripoSR:
    """TripoSR (MIT). Default on the Mac. Requires the [ai] extra and the
    TripoSR package from git."""

    name = "triposr"

    def __init__(self, repo: str = "stabilityai/TripoSR", device: str | None = None):
        self.repo = repo
        self.device = _pick_device(device)

    def reconstruct(self, image: Path, out_glb: Path) -> Path:
        import torch
        from PIL import Image
        from tsr.system import TSR
        from tsr.utils import remove_background, resize_foreground

        model = TSR.from_pretrained(
            self.repo, config_name="config.yaml", weight_name="model.ckpt"
        )
        model.renderer.set_chunk_size(8192)
        model.to(self.device)

        img = Image.open(image).convert("RGBA")
        img = resize_foreground(remove_background(img), 0.85)
        arr = Image.new("RGBA", img.size, (255, 255, 255, 255))
        arr.alpha_composite(img)
        rgb = arr.convert("RGB")
        with torch.no_grad():
            codes = model([rgb], device=self.device)
            meshes = model.extract_mesh(codes, has_vertex_color=False, resolution=256)
        meshes[0].export(out_glb)
        return out_glb


class Hunyuan3DMini:
    """Hunyuan3D-2mini (shape only). Tried on MPS, falls back to CPU."""

    name = "hunyuan"

    def __init__(self, repo: str = "tencent/Hunyuan3D-2mini", device: str | None = None):
        self.repo = repo
        self.device = device or "mps"

    def reconstruct(self, image: Path, out_glb: Path) -> Path:
        from PIL import Image
        from hy3dgen.shapegen import Hunyuan3DDiTFlowMatchingPipeline

        def run(device: str) -> trimesh.Trimesh:
            pipe = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(self.repo, device=device)
            return pipe(image=Image.open(image).convert("RGBA"))[0]

        try:
            mesh = run(self.device)
        except (RuntimeError, NotImplementedError):
            mesh = run("cpu")
        mesh.export(out_glb)
        return out_glb


BACKENDS = {"stub": StubReconstructor, "triposr": TripoSR, "hunyuan": Hunyuan3DMini}


def get_reconstructor(name: str, device: str | None = None) -> Reconstructor:
    if name not in BACKENDS:
        raise ValueError(f"Unknown reconstructor '{name}'. Choose from: {', '.join(BACKENDS)}")
    cls = BACKENDS[name]
    return cls() if cls is StubReconstructor else cls(device=device)
