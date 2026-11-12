"""Magnets and keychains are bas-reliefs: full front detail, the picture's own outline."""
import math

import numpy as np
import pytest
import trimesh
from PIL import Image, ImageDraw
from scipy import ndimage

from formstudio.core import printify as pf

THICK = 9.0


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    """A deep tree mesh whose trunk sits well behind the canopy's front — the shape that used to
    lose its trunk — plus the picture's mask (canopy AND trunk)."""
    tiers = []
    for i, (r, z) in enumerate(((16, 10), (13, 20), (9, 30))):
        c = trimesh.creation.cone(radius=r, height=16, sections=48)
        c.apply_translation([0, 0, z])
        tiers.append(c)
    trunk = trimesh.creation.cylinder(radius=4, height=12, sections=32)
    trunk.apply_translation([0, 0, 4])  # inside the canopy's footprint: recessed behind its front
    mesh = trimesh.boolean.union(tiers + [trunk], engine="manifold")
    mesh.apply_transform(trimesh.transformations.rotation_matrix(-math.pi / 2, [1, 0, 0]))  # z-up → glTF y-up
    glb = tmp_path_factory.mktemp("relief") / "tree.glb"
    mesh.export(glb)

    img = Image.new("L", (512, 512), 0)
    d = ImageDraw.Draw(img)
    for cy, r in ((150, 110), (260, 140), (360, 170)):
        d.polygon([(256, cy - 150), (256 - r, cy + 40), (256 + r, cy + 40)], fill=255)
    d.rectangle((226, 400, 286, 480), fill=255)  # the trunk
    mask = tmp_path_factory.mktemp("relief") / "mask.png"
    img.save(mask)
    return glb, mask


def _params(fmt="magnet"):
    return {
        "format": fmt,
        "geometry": {"height_mm": 35, "finish": "soft", "soften_mm": 0.6, "voxel_mm": 0.4, "min_feature_mm": 1.6,
                     "smooth_iters": 4, "target_faces": 60000, "flat_cut": 0.03},
        "base": {"enabled": False},
        "flat_back": {"enabled": True, "thickness_mm": THICK},
        "magnet": {"enabled": fmt == "magnet", "diameter_mm": 8, "depth_mm": 2, "clearance_mm": 0.15},
        "ring": {"enabled": fmt == "keychain", "outer_radius_mm": 4.5, "hole_mm": 4.5, "thickness_mm": 3.5},
    }


def _run(tree, out, fmt="magnet", with_mask=True):
    glb, mask = tree
    return pf.run(glb, out, _params(fmt), mask_path=mask if with_mask else None)


def _grid(body, pitch=0.4):
    vol = body.voxelized(pitch).matrix
    return vol, pitch


def test_magnet_keeps_trunk_and_ridges(tree, tmp_path):
    report = _run(tree, tmp_path)
    assert report["watertight"] and report["relief"] and report["outline"] == "picture"
    body = trimesh.load(tmp_path / "body.stl")
    w, h, t = body.extents
    assert t == pytest.approx(THICK, abs=0.8)
    # the trunk region exists: material well below the lowest canopy tier
    vol, pitch = _grid(body)
    heights = vol.sum(axis=2) * pitch  # thickness per (x, y) column, body lying on the bed
    ys = np.nonzero(heights.any(axis=0))[0]
    bottom_rows = heights[:, ys.max() - int(3 / pitch):ys.max() + 1]  # the lowest ~3 mm: trunk only
    assert bottom_rows.max() >= 2.0 - 0.5  # the plate is there (the old path cut this away entirely)
    # detail: the relief really uses the thickness, and the tiers read as separate ridges
    assert report["relief_mm"] >= 0.55 * (THICK - report["plate_mm"])
    mid = heights[np.nonzero(heights.any(axis=1))[0], :]
    centre = mid[len(mid) // 2]  # thickness profile up the middle of the tree
    prof = ndimage.gaussian_filter1d(centre[centre > 0.5], 1.2)
    peaks = ((prof[1:-1] > prof[:-2]) & (prof[1:-1] > prof[2:]) & (prof[1:-1] > prof.max() * 0.5)).sum()
    assert peaks >= 2  # tier ridges survived (the old slab gave one blob)


def test_magnet_outline_matches_the_picture(tree, tmp_path):
    _run(tree, tmp_path)
    body = trimesh.load(tmp_path / "body.stl")
    vol, pitch = _grid(body)
    got = vol.any(axis=2)  # (x, y) outline on the bed; the object's top points to +y
    mask = np.asarray(Image.open(tree[1]).convert("L")) > 127
    ys, xs = np.nonzero(mask)
    crop = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    arr = np.asarray(Image.fromarray(crop.astype(np.uint8) * 255)
                     .resize((got.shape[0], got.shape[1]), Image.BILINEAR)) > 127  # (y rows down, x)
    m = arr[::-1].T  # → (x, y) with y up, like the body
    iou = (got & m).sum() / (got | m).sum()
    assert iou >= 0.85, iou


def test_keychain_and_fallback_without_mask(tree, tmp_path):
    r1 = _run(tree, tmp_path / "k", fmt="keychain")
    assert r1["watertight"] and r1["relief"]
    body = trimesh.load(tmp_path / "k" / "body.stl")
    assert len(body.split(only_watertight=False)) == 1  # the ring is attached
    r2 = _run(tree, tmp_path / "m", with_mask=False)
    assert r2["watertight"] and r2["relief"] and r2["outline"] == "3D shape"


def test_magnet_pocket_cut(tree, tmp_path):
    glb, mask = tree
    _run(tree, tmp_path)
    with_pocket = trimesh.load(tmp_path / "body.stl").volume
    params = _params("magnet")
    params["magnet"]["enabled"] = False
    pf.run(glb, tmp_path / "solid", params, mask_path=mask)
    solid = trimesh.load(tmp_path / "solid" / "body.stl").volume
    cut = solid - with_pocket
    want = math.pi * (8 / 2 + 0.15) ** 2 * 2  # the pocket's cylinder
    assert cut == pytest.approx(want, rel=0.5) and cut > 50
