"""Runs the real local SDXL pipeline for 2 tiny steps.

Skipped unless FORMSTUDIO_SMOKE=1 and the SDXL models are already downloaded
(Settings → Local AI models). It caught "'tuple' object has no attribute 'shape'"
(attention slicing replacing the IP-Adapter's attention layers).
"""
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("FORMSTUDIO_SMOKE") != "1", reason="set FORMSTUDIO_SMOKE=1 to run")


def test_sdxl_restyle_runs_end_to_end(tmp_path, monkeypatch):
    pytest.importorskip("diffusers")
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    from huggingface_hub import constants

    monkeypatch.setattr(constants, "HF_HUB_OFFLINE", False, raising=False)
    from formstudio import models
    from formstudio.core import ingest
    from formstudio.core.style import get_style
    from formstudio.engines import STYLIZERS, EngineContext, StylizeInput, pick_device

    if models.status("sdxl")["status"] != "ready":
        pytest.skip("SDXL models aren't downloaded")
    from PIL import Image, ImageDraw

    photo = tmp_path / "tree.png"
    img = Image.new("RGB", (300, 360), (235, 225, 210))
    ImageDraw.Draw(img).polygon([(150, 20), (40, 300), (260, 300)], fill=(40, 120, 50))
    img.save(photo)
    ingest.cutout(photo, tmp_path / "01_cutout.png")
    style = get_style("clay-minimal")
    style.stylize.update(size=256, steps=2)
    out = tmp_path / "c"
    out.mkdir()
    res = STYLIZERS["local-sdxl"].stylize(
        StylizeInput(tmp_path / "01_cutout.png", tmp_path / "01_cutout_mask.png", style, "christmas tree", [11], out),
        EngineContext(settings={}, device=pick_device("auto"), secrets={}), lambda p, m: None)
    assert res and Image.open(out / res[0]["file"]).size == (1024, 1024)
