from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw


@pytest.fixture
def sample_photo(tmp_path: Path) -> Path:
    """A brown 'mug' on a flat grey background."""
    im = Image.new("RGB", (800, 600), (200, 200, 200))
    d = ImageDraw.Draw(im)
    d.rectangle([250, 150, 500, 480], fill=(120, 60, 40))
    d.ellipse([480, 220, 600, 360], outline=(120, 60, 40), width=25)
    p = tmp_path / "mug.jpg"
    im.save(p)
    return p


@pytest.fixture(scope="module")
def cylinder_y_up():
    import trimesh

    m = trimesh.creation.cylinder(radius=0.5, height=1.0, sections=48)
    m.apply_translation([0, 0.5, 0])
    return m
