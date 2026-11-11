from .base import EngineContext, ReconstructInput, StylizeInput, describe
from .claude_sculpt import ClaudeSculpt
from .cloud import FalHunyuan, FalKontext, FalNanoBanana, FalTrellis
from .local import LocalHunyuanMini, LocalSDXL, LocalTripoSR, pick_device, unload_all
from .offline import Extrude, Lathe, PalettePreview

STYLIZERS = {e.id: e for e in (LocalSDXL(), FalNanoBanana(), FalKontext(), PalettePreview())}
RECONSTRUCTORS = {
    e.id: e
    for e in (LocalTripoSR(), LocalHunyuanMini(), FalTrellis(), FalHunyuan(), ClaudeSculpt(), Lathe(), Extrude())
}

DEFAULTS = {"stylize": "local-sdxl", "reconstruct": "triposr"}

__all__ = [
    "STYLIZERS", "RECONSTRUCTORS", "DEFAULTS", "EngineContext", "StylizeInput", "ReconstructInput",
    "describe", "pick_device", "unload_all",
]
