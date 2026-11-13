from .base import EngineContext, ReconstructInput, StylizeInput, describe
from .claude_sculpt import ClaudeSculpt
from .cloud import FalHunyuan, FalHunyuan3, FalKontext, FalMeshy, FalNanoBanana, FalTrellis, FalTrellis2, FalTripo
from .meshy import MeshyImageTo3D
from .openai_image import OpenAIImage
from .local import LocalHunyuanMini, LocalHunyuanMV, LocalSDXL, LocalTripoSG, LocalTripoSR, pick_device, unload_all
from .offline import Extrude, Lathe, LogoPreview, LogoShape, PalettePreview, PictureRelief

STYLIZERS = {e.id: e for e in (LocalSDXL(), OpenAIImage(), FalNanoBanana(), FalKontext(), PalettePreview(), LogoPreview())}
RECONSTRUCTORS = {
    e.id: e
    for e in (LocalTripoSR(), LocalTripoSG(), LocalHunyuanMini(), LocalHunyuanMV(), FalHunyuan3(), FalTripo(), FalTrellis2(),
                  FalMeshy(), MeshyImageTo3D(), FalTrellis(), FalHunyuan(), ClaudeSculpt(), Lathe(), Extrude(), PictureRelief(), LogoShape())
}

DEFAULTS = {"stylize": "local-sdxl", "reconstruct": "triposr", "views": "openai-views"}

from .views import VIEWERS  # noqa: E402  (needs the engines above)

__all__ = [
    "STYLIZERS", "RECONSTRUCTORS", "VIEWERS", "DEFAULTS", "EngineContext", "StylizeInput", "ReconstructInput",
    "describe", "pick_device", "unload_all",
]
