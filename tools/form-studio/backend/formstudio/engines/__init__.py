from .base import EngineContext, ReconstructInput, StylizeInput, describe
from .claude_sculpt import ClaudeSculpt
from .cloud import FalHunyuan, FalKontext, FalNanoBanana, FalTrellis
from .meshy import MeshyImageTo3D
from .openai_image import OpenAIImage
from .local import LocalHunyuanMini, LocalHunyuanMV, LocalSDXL, LocalTripoSR, pick_device, unload_all
from .offline import Extrude, Lathe, LogoPreview, LogoShape, PalettePreview

STYLIZERS = {e.id: e for e in (LocalSDXL(), OpenAIImage(), FalNanoBanana(), FalKontext(), PalettePreview(), LogoPreview())}
RECONSTRUCTORS = {
    e.id: e
    for e in (LocalTripoSR(), LocalHunyuanMini(), LocalHunyuanMV(), MeshyImageTo3D(), FalTrellis(), FalHunyuan(), ClaudeSculpt(), Lathe(), Extrude(), LogoShape())
}

DEFAULTS = {"stylize": "local-sdxl", "reconstruct": "triposr", "views": "openai-views"}

from .views import VIEWERS  # noqa: E402  (needs the engines above)

__all__ = [
    "STYLIZERS", "RECONSTRUCTORS", "VIEWERS", "DEFAULTS", "EngineContext", "StylizeInput", "ReconstructInput",
    "describe", "pick_device", "unload_all",
]
