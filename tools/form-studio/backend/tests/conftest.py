import math
import os
import tempfile
from pathlib import Path

import pytest

# isolate every test run from the real ~/FormStudio
os.environ["FORMSTUDIO_HOME"] = tempfile.mkdtemp(prefix="formstudio-test-")
os.environ["FORMSTUDIO_MATTING"] = "threshold"


@pytest.fixture(scope="session")
def mug_photo(tmp_path_factory) -> Path:
    """A flat drawing of a mug on a white background."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (800, 700), (250, 250, 248))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((240, 180, 520, 560), radius=30, fill=(180, 60, 50))
    d.ellipse((470, 260, 640, 460), outline=(180, 60, 50), width=34)
    p = tmp_path_factory.mktemp("photos") / "coffee_mug.png"
    img.save(p)
    return p


@pytest.fixture(scope="session")
def mug_glb(tmp_path_factory) -> Path:
    """A simple mug mesh in glTF y-up, like an AI engine would produce."""
    import trimesh

    outer = trimesh.creation.cylinder(radius=40, height=90, sections=48)
    inner = trimesh.creation.cylinder(radius=34, height=90, sections=48)
    inner.apply_translation([0, 0, 8])
    handle = trimesh.creation.torus(major_radius=20, minor_radius=6)
    handle.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0]))
    handle.apply_translation([44, 0, 0])
    mug = trimesh.boolean.union([trimesh.boolean.difference([outer, inner], engine="manifold"), handle], engine="manifold")
    mug.apply_transform(trimesh.transformations.rotation_matrix(-math.pi / 2, [1, 0, 0]))
    p = tmp_path_factory.mktemp("meshes") / "mug.glb"
    mug.export(p)
    return p
