"""One balanced, print-friendly detail standard for every style: in the prompts and in the 3D step."""
import time

import numpy as np
import pytest
import trimesh
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from scipy import ndimage

from formstudio.api.app import app
from formstudio.core import printify as pf
from formstudio.core import relief
from formstudio.core.style import DETAIL_LEVELS, get_style, list_styles
from formstudio.engines import STYLIZERS


def _wait(c, jid):
    for _ in range(3000):
        j = c.get(f"/api/jobs/{jid}").json()
        if j["task"] and j["task"]["status"] in ("done", "error"):
            assert j["task"]["status"] == "done", j["task"]["error"]
            return j
        time.sleep(0.05)
    raise AssertionError("timed out")


def test_every_style_gets_the_detail_standard(tmp_path, monkeypatch):
    seen = []

    def fake(inp, ctx, progress):
        seen.append((inp.style.id, inp.style.edit_prompt, inp.style.negative_prompt))
        Image.open(inp.cutout).save(inp.out_dir / "candidate_1.png")
        return [{"file": "candidate_1.png", "seed": 1}]

    monkeypatch.setattr(STYLIZERS["preview"], "stylize", fake)
    img = Image.new("RGB", (400, 400), "white")
    ImageDraw.Draw(img).ellipse((80, 80, 320, 320), fill=(200, 60, 40))
    photo = tmp_path / "ball.png"
    img.save(photo)
    with TestClient(app) as c:
        new = c.post("/api/styles", json={"name": "Brand New Look", "description": "glossy toys"}).json()
        ids = [s.id for s in list_styles()]
        assert new["id"] in ids  # a style made later gets the same standard
        for sid in ids:
            with photo.open("rb") as f:
                j = c.post("/api/jobs", files={"photo": ("ball.png", f, "image/png")},
                           data={"subject": "ball", "style": sid, "format": "magnet"}).json()
            c.post(f"/api/jobs/{j['id']}/kind", json={"kind": "object"})
            c.post(f"/api/jobs/{j['id']}/stylize", json={"engine": "preview", "n": 1})
            _wait(c, j["id"])
            _, edit, neg = seen[-1]
            assert DETAIL_LEVELS["balanced"]["brief"] in edit, sid
            assert "tiny text" in neg and "thin spikes" in neg
            assert j["print_params"]["detail"]["level"] == "balanced"
            c.delete(f"/api/jobs/{j['id']}")
        c.delete(f"/api/styles/{new['id']}")


def test_style_floors_no_hair_thin_parts():
    for s in list_styles():
        for fmt in s.formats:
            p = s.print_params(fmt)
            assert p["geometry"]["min_feature_mm"] >= 1.2
            assert p["detail"]["level"] == "balanced" and p["edges"]["style"] == "auto"


def _vehicle_depth():
    """A 'vehicle': a big body with a cab (main features) and 40 tiny bolts (fine repetitive detail)."""
    S = 600
    yy, xx = np.mgrid[:S, :S]
    mask = (np.abs(yy - 330) < 150) & (np.abs(xx - 300) < 240)
    cab = (np.abs(yy - 240) < 60) & (np.abs(xx - 220) < 90)
    depth = ndimage.gaussian_filter(mask.astype(float), 25) + 0.5 * ndimage.gaussian_filter(cab.astype(float), 3)
    bolts = np.zeros_like(mask)
    rng = np.random.default_rng(3)
    for _ in range(40):
        y, x = rng.integers(220, 450), rng.integers(100, 500)
        bolts |= (yy - y) ** 2 + (xx - x) ** 2 < 4 ** 2
    plain = depth.copy()
    depth = depth + 0.4 * ndimage.gaussian_filter(bolts.astype(float), 1)
    rgb = np.full((S, S, 3), 255, np.uint8)
    rgb[mask] = (90, 110, 160)
    return rgb, mask, cab, bolts, depth.astype(np.float32), plain.astype(np.float32)


@pytest.mark.parametrize("level", ["balanced", "fine"])
def test_small_details_are_simplified(level):
    rgb, mask, cab, bolts, depth, plain = _vehicle_depth()
    box, mm_px = relief.fit(mask, 35)
    stats = {}
    lvl = {"id": level, **DETAIL_LEVELS[level]}
    h = relief.heightmap(rgb, mask, None, box, mm_px, 9.0, {}, depth=depth,
                         edges={"style": "soft", "outer_mm": 1.5, "detail_mm": 0.3}, stats=stats, detail=lvl)
    g = lambda a: relief._resample(a, box, h.shape, Image.NEAREST) > 127  # noqa: E731
    cabg, boltg = g(cab), g(bolts)
    without = relief.heightmap(rgb, mask, None, box, mm_px, 9.0, {}, depth=plain,
                               edges={"style": "soft", "outer_mm": 1.5, "detail_mm": 0.3}, detail=lvl)
    core = ndimage.binary_erosion(cabg, iterations=3)
    ring = ndimage.binary_dilation(cabg, iterations=12) & ~ndimage.binary_dilation(cabg, iterations=4) & g(mask)
    assert np.median(h[core]) > np.median(h[ring]) + 0.4  # the cab stays a clear, raised shape
    bolt_relief = float(np.abs(h - without)[boltg].mean())  # what the bolts add to the print
    if level == "balanced":
        assert bolt_relief < 0.2 and stats["simplified"]["kept"] <= 16  # bolts: less than one 0.2 mm print layer
        assert stats["simplified"]["small"] >= 10


def test_balanced_removes_more_than_fine():
    rgb, mask, cab, bolts, depth, plain = _vehicle_depth()
    box, mm_px = relief.fit(mask, 35)
    out = {}
    e = {"style": "soft", "outer_mm": 1.5, "detail_mm": 0.3}
    for level in ("balanced", "fine"):
        lvl = {"id": level, **DETAIL_LEVELS[level]}
        h = relief.heightmap(rgb, mask, None, box, mm_px, 9.0, {}, depth=depth, edges=e, detail=lvl)
        h0 = relief.heightmap(rgb, mask, None, box, mm_px, 9.0, {}, depth=plain, edges=e, detail=lvl)
        boltg = relief._resample(bolts, box, h.shape, Image.NEAREST) > 127
        out[level] = float(np.abs(h - h0)[boltg].mean())
    assert out["balanced"] < out["fine"]


def _spiky_glb(tmp_path):
    body = trimesh.creation.capsule(height=20, radius=10)
    spike = trimesh.creation.cylinder(radius=0.6, height=14)  # a 1.2 mm wire: fine keeps it, balanced trims it
    spike.apply_translation([14, 0, 0])
    spike.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0], point=[14, 0, 0]))
    mesh = trimesh.boolean.union([body, spike], engine="manifold")
    p = tmp_path / "spiky.glb"
    mesh.export(p)
    return p


def test_figurine_detail_floor_edges_and_review(tmp_path):
    glb = _spiky_glb(tmp_path)
    params = get_style("clay-minimal").print_params("stand")
    rep = pf.run(glb, tmp_path / "bal", params)
    assert rep["detail_level"] == "balanced" and rep["min_feature_mm"] >= 1.8
    assert {c["id"] for c in rep["changes"]} >= {"detail", "edges", "base"}
    assert rep["edges_used"]["style"] in ("soft", "bevel", "crisp")
    params_f = {**params, "detail": {"level": "fine"}}
    rep_f = pf.run(glb, tmp_path / "fine", params_f)
    assert rep_f["min_feature_mm"] < rep["min_feature_mm"]  # fine allows thinner parts than balanced
    assert rep_f["thin_removed_pct"] <= rep["thin_removed_pct"]
    # edges change the real geometry: crisp keeps more surface detail than soft
    soft = pf.run(glb, tmp_path / "soft", {**params, "edges": {"style": "soft", "outer_mm": 2.5, "detail_mm": 1.0}})
    crisp = pf.run(glb, tmp_path / "crisp", {**params, "edges": {"style": "crisp", "outer_mm": 0, "detail_mm": 0.1}})
    a_soft = trimesh.load(tmp_path / "soft" / "body.stl").area
    a_crisp = trimesh.load(tmp_path / "crisp" / "body.stl").area
    assert soft["edges_used"]["style"] == "soft" and crisp["edges_used"]["style"] == "crisp"
    assert abs(a_soft - a_crisp) / a_crisp > 0.005
