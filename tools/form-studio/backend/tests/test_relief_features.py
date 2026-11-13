"""Picture reliefs follow the picture: raised and recessed features, clean edges in the chosen style."""
import math

import numpy as np
import pytest
import trimesh
from PIL import Image
from scipy import ndimage

from formstudio.core import printify as pf
from formstudio.core import relief
from formstudio.core.style import get_style

S = 600


def _ellipse(cy, cx, ry, rx):
    yy, xx = np.mgrid[:S, :S]
    return ((yy - cy) / ry) ** 2 + ((xx - cx) / rx) ** 2 <= 1


def _bird():
    """A one-colour clay 'bird' rendered like the user's reference: a domed body, a raised face panel,
    two recessed eyes and a raised beak, lit from the top-left, with soft contact shadows in the creases."""
    body = _ellipse(300, 300, 240, 220)
    face = _ellipse(280, 300, 120, 140)
    eyes = _ellipse(245, 240, 36, 26) | _ellipse(245, 360, 36, 26)
    beak = _ellipse(318, 300, 30, 40)
    d = ndimage.distance_transform_edt(body)
    h = np.sqrt(np.clip(d / 40, 0, 1)) * 30 + d * 0.05             # rounded edge + gentle dome
    def step(mask, amount):
        return ndimage.gaussian_filter(mask.astype(float), 3) * amount
    h = h + step(face, 8) - step(eyes, 10) + step(beak, 9)
    gy, gx = np.gradient(h)
    n = np.stack([-gx, -gy, np.ones_like(h)], -1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    L = np.array([-0.55, -0.55, 0.63])                              # (x, y, z): from the top-left
    shade = np.clip(n @ (L / np.linalg.norm(L)), 0, 1)
    edges = (ndimage.morphological_gradient(face.astype(np.uint8) + eyes + beak, size=5) > 0)
    shade = shade * (1 - 0.35 * ndimage.gaussian_filter(edges.astype(float), 1.5))  # contact shadow in creases
    col = np.array([150, 160, 110])
    rgb = np.clip(col[None, None] * (0.35 + 0.8 * shade[..., None]), 0, 255).astype(np.uint8)
    rgb[~body] = 255
    return rgb, body, {"face": face & ~eyes & ~beak, "eyes": eyes, "beak": beak, "body": body & ~face}


@pytest.fixture(scope="module")
def bird():
    return _bird()


def _grid(mask, box, mm_px, shape):
    return relief._resample(mask, box, shape, Image.NEAREST) > 127


def test_features_found_without_depth_model(bird):
    """No depth model: features are read from the picture's creases and shading. It's a heuristic, so
    this checks it finds separate raised/recessed shapes (the depth-model test below checks exact
    placement), and that it doesn't invent features on the smooth body."""
    rgb, mask, parts = bird
    box, mm_px = relief.fit(mask, 35)
    stats = {}
    h = relief.heightmap(rgb, mask, None, box, mm_px, 9.0, {"soften_mm": 0.9},
                         edges={"style": "soft", "outer_mm": 1.5, "detail_mm": 0.3}, stats=stats)
    assert stats["source"] == "shading"
    reg, signs = stats["regions"]
    assert 2 <= sum(1 for x in signs if x) <= 6
    assert -1 in signs and 1 in signs  # some shapes stand out, some sink in
    check = relief.relief_check(h, stats)
    assert check["features"]["distinct"] >= 2
    g = _grid(parts["body"], box, mm_px, h.shape)
    outer = ndimage.binary_erosion(g, iterations=10)
    ids_on_body = np.unique(reg[outer])
    assert len(ids_on_body) <= 3


def test_smooth_surface_keeps_steps(bird):
    rgb, mask, parts = bird
    noisy = np.clip(rgb.astype(int) + np.random.default_rng(0).integers(-14, 14, rgb.shape), 0, 255).astype(np.uint8)
    noisy[~mask] = 255
    box, mm_px = relief.fit(mask, 35)
    h = relief.heightmap(noisy, mask, None, box, mm_px, 9.0, {"soften_mm": 0.9},
                         edges={"style": "soft", "outer_mm": 1.5, "detail_mm": 0.3})
    g = _grid(parts["body"], box, mm_px, h.shape)
    flat = ndimage.binary_erosion(g, iterations=8)
    lap = np.abs(ndimage.laplace(h))
    assert float(np.percentile(lap[flat], 95)) < 0.08  # no grain, ripples or voxel steps on the body


def test_edge_styles_change_the_geometry(bird):
    rgb, mask, _ = bird
    box, mm_px = relief.fit(mask, 35)
    m_out, _ = relief.outline(mask, box, mm_px, 1.6)
    hs = {st: relief.heightmap(rgb, mask, None, box, mm_px, 9.0, {}, edges={"style": st, "outer_mm": 2.0, "detail_mm": 0.3},
                               outline_grid=m_out) for st in ("soft", "bevel", "crisp")}
    g = relief._resample(mask, box, hs["soft"].shape, Image.BILINEAR) > 127
    d = ndimage.distance_transform_edt(g) * relief.GRID_MM
    ring = lambda a, b: g & (d >= a) & (d < b)  # noqa: E731
    near, mid = ring(0.2, 0.4), ring(0.9, 1.1)
    for st in ("soft", "bevel"):
        assert hs[st][near].mean() < hs["crisp"][near].mean() - 1.0  # the outline is rounded / chamfered
    # quarter-round rises faster than a straight chamfer at the same distance
    assert hs["soft"][mid].mean() > hs["bevel"][mid].mean() + 0.2
    crisp_ring = ring(0.3, 0.5)  # crisp: full height just inside the outline (only a hairline round-over)
    assert hs["crisp"][crisp_ring].mean() >= relief.plate_mm(9.0) - 1e-3


def test_auto_edges_follow_the_picture(bird):
    rgb, mask, _ = bird
    _, mm_px = relief.fit(mask, 35)
    assert relief.suggest_edges(rgb, mask, mm_px)["style"] == "soft"   # a rounded clay render
    flat = np.full_like(rgb, 255)
    flat[mask] = (150, 160, 110)
    assert relief.suggest_edges(flat, mask, mm_px)["style"] == "crisp"  # a flat cut-out


def test_outline_is_smooth(bird):
    rgb, mask, _ = bird
    box, mm_px = relief.fit(mask, 35)
    m_out, _ = relief.outline(mask, box, mm_px, 1.6)
    poly = np.asarray(relief.prism(m_out, 5).slice(2.0).to_polygons()[0])
    v1 = np.roll(poly, -1, 0) - poly
    v2 = np.roll(v1, -1, 0)
    turn = np.degrees(np.abs(np.arctan2(v1[:, 0] * v2[:, 1] - v1[:, 1] * v2[:, 0], (v1 * v2).sum(1))))
    assert float(np.percentile(turn, 99)) < 25  # no pixel staircase (which turns 90° at every step)


def test_depth_model_detail_is_followed(bird, monkeypatch):
    rgb, mask, parts = bird
    box, mm_px = relief.fit(mask, 35)
    depth = ndimage.gaussian_filter(mask.astype(float), 20) + 0.6 * ndimage.gaussian_filter(parts["beak"].astype(float), 3) \
        - 0.6 * ndimage.gaussian_filter(parts["eyes"].astype(float), 3)
    stats = {}
    h = relief.heightmap(rgb, mask, None, box, mm_px, 9.0, {}, depth=depth.astype(np.float32),
                         edges={"style": "soft", "outer_mm": 1.5, "detail_mm": 0.3}, stats=stats)
    assert stats["source"] == "depth"
    g = {k: ndimage.binary_erosion(_grid(v, box, mm_px, h.shape), iterations=2) for k, v in parts.items()}
    assert np.median(h[g["beak"]]) > np.median(h[g["face"]]) + 0.4
    assert np.median(h[g["eyes"]]) < np.median(h[g["face"]]) - 0.4


def test_review_has_front_preview_and_relief_numbers(bird, tmp_path):
    rgb, mask, _ = bird
    pic, m = tmp_path / "pic.png", tmp_path / "mask.png"
    Image.fromarray(rgb).save(pic)
    Image.fromarray((mask * 255).astype(np.uint8)).save(m)
    glb = tmp_path / "x.glb"
    trimesh.creation.box((10, 10, 10)).export(glb)
    params = get_style("soft-clay").print_params("magnet")
    rep = pf.run(glb, tmp_path / "out", params, mask_path=m, image_path=pic)
    assert (tmp_path / "out" / "preview_front.png").exists()
    assert rep["edges_used"]["style"] == "soft" and rep["edges_used"]["auto"]
    assert rep["fidelity"]["features"]["distinct"] >= 2 and "relief_match" in rep["fidelity"]
    assert any(c["id"] == "edges" for c in rep["changes"]) and any(c["id"] == "features" for c in rep["changes"])
    body = trimesh.load(tmp_path / "out" / "body.stl")
    assert body.is_watertight
    # crisp edges, by request: rebuilt geometry, taller at the outline
    params["edges"] = {"style": "crisp", "outer_mm": 0, "detail_mm": 0.3}
    rep2 = pf.run(glb, tmp_path / "out2", params, mask_path=m, image_path=pic)
    assert rep2["edges_used"]["style"] == "crisp" and rep2["body_volume_cm3"] > rep["body_volume_cm3"]
