from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from formstudio.engines.local import style_reference


def test_style_without_references_gets_a_neutral_image_at_strength_zero():
    img, scale = style_reference(SimpleNamespace(reference=None, stylize={"ip_adapter_scale": 0.6}))
    assert scale == 0.0 and img.getpixel((0, 0)) == (255, 255, 255)


def test_style_with_a_reference_uses_it(tmp_path: Path):
    ref = tmp_path / "r.png"
    Image.new("RGB", (40, 40), (10, 200, 30)).save(ref)
    img, scale = style_reference(SimpleNamespace(reference=ref, stylize={"ip_adapter_scale": 0.7}))
    assert scale == 0.7 and img.getpixel((5, 5)) == (10, 200, 30)
