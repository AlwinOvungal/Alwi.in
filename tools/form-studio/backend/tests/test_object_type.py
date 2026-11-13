"""The object type is not display-only: it shapes every picture prompt and the print (pocket, loop)."""
import time

import numpy as np
import pytest
import trimesh
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from formstudio.api.app import app
from formstudio.core import printify as pf
from formstudio.core.style import get_style
from formstudio.engines import STYLIZERS


@pytest.fixture()
def photo(tmp_path):
    img = Image.new("RGB", (600, 600), "white")
    ImageDraw.Draw(img).ellipse((100, 100, 500, 500), fill=(90, 120, 200))
    p = tmp_path / "ball.png"
    img.save(p)
    return p


def _wait(c, jid):
    for _ in range(3000):
        j = c.get(f"/api/jobs/{jid}").json()
        if j["task"] and j["task"]["status"] in ("done", "error"):
            assert j["task"]["status"] == "done", j["task"]["error"]
            return j
        time.sleep(0.05)
    raise AssertionError("timed out")


def test_object_type_goes_into_every_restyle_prompt(photo, monkeypatch):
    seen = []

    def fake(inp, ctx, progress):
        seen.append((inp.style.edit_prompt, inp.style.prompt, inp.style.negative_prompt))
        Image.open(inp.cutout).save(inp.out_dir / "candidate_1.png")
        return [{"file": "candidate_1.png", "seed": 1}]

    monkeypatch.setattr(STYLIZERS["preview"], "stylize", fake)
    with TestClient(app) as c:
        with photo.open("rb") as f:
            j = c.post("/api/jobs", files={"photo": ("ball.png", f, "image/png")},
                       data={"subject": "ball", "style": "clay-minimal", "format": "magnet"}).json()
        jid = j["id"]
        c.post(f"/api/jobs/{jid}/kind", json={"kind": "object"})
        expect = {
            "magnet": ["fridge magnet", "flat", "never a through-hole", "shallow relief"],
            "keychain": ["keychain", "reinforced", "loop or attachment hole", "fragile"],
            "ornament": ["hanging ornament", "hanging loop or hole at the top"],
            "stand": ["figurine", "front, back and side", "stand"],
        }
        for fmt, words in expect.items():
            if fmt != "magnet":
                j = c.post(f"/api/jobs/{jid}/format", json={"format": fmt}).json()
                assert j["print_params"]["format"] == fmt
            c.post(f"/api/jobs/{jid}/stylize", json={"engine": "preview", "n": 1})
            _wait(c, jid)
            edit, prompt, neg = seen[-1]
            assert all(w in edit for w in words), (fmt, edit)
            assert "colour" not in prompt.lower() or "own colours" in edit  # styles never set colours
        assert "side view" in seen[0][2]  # magnet: things to avoid, too
        assert c.post(f"/api/jobs/{jid}/format", json={"format": "wheel"}).status_code == 400

        # a shape built for one type is cleared when the type changes
        c.post(f"/api/jobs/{jid}/confirm", json={"candidate": 0})
        c.post(f"/api/jobs/{jid}/reconstruct", json={"engine": "extrude"})
        j = _wait(c, jid)
        assert j["stage"] == "printified"
        j = c.post(f"/api/jobs/{jid}/format", json={"format": "magnet"}).json()
        assert j["stage"] == "confirmed" and j["print_params"]["flat_back"]["enabled"]
        c.delete(f"/api/jobs/{jid}")


def _picture(tmp_path, draw, size=(600, 700)):
    img = Image.new("RGB", size, "white")
    draw(ImageDraw.Draw(img))
    rgb = np.asarray(img)
    mask = Image.fromarray(((rgb.min(axis=2) < 250) * 255).astype(np.uint8))
    p, m = tmp_path / "pic.png", tmp_path / "mask.png"
    img.save(p)
    mask.save(m)
    glb = tmp_path / "dummy.glb"
    trimesh.creation.box((10, 10, 10)).export(glb)
    return glb, m, p


def test_magnet_pocket_goes_where_the_shape_is_widest(tmp_path):
    # a tree: narrow top, wide bottom — the old centred pocket sat in the thin middle
    glb, m, p = _picture(tmp_path, lambda d: (d.polygon([(300, 20), (40, 600), (560, 600)], fill=(60, 140, 60)),
                                              d.rectangle((270, 600, 330, 680), fill=(110, 70, 40))))
    params = get_style("soft-clay").print_params("magnet")
    rep = pf.run(glb, tmp_path / "out", params, mask_path=m, image_path=p)
    pk = rep["pocket"]
    assert pk["y"] < -3 and pk["wall_mm"] >= 1.5  # low down in the wide part, with material all round
    assert any("not a through-hole" in c["text"] for c in rep["changes"])
    parts = [trimesh.load(f) for f in sorted((tmp_path / "out").glob("part_*.stl"))] or [trimesh.load(tmp_path / "out" / "body.stl")]
    assert all(x.is_watertight for x in parts)

    # too narrow for the magnet: said plainly, with a size that fits
    glb, m, p = _picture(tmp_path, lambda d: d.rectangle((280, 20, 320, 680), fill=(60, 60, 60)))
    rep = pf.run(glb, tmp_path / "out2", params, mask_path=m, image_path=p)
    assert any("magnet" in w and "material around" in w for w in rep["warnings"])


def test_keychain_uses_the_designs_own_loop(tmp_path):
    def draw(d):
        d.ellipse((100, 160, 500, 680), fill=(200, 80, 60))   # the charm
        d.ellipse((230, 40, 370, 200), fill=(200, 80, 60))    # its loop, joined to the body
        d.ellipse((270, 80, 330, 140), fill=(255, 255, 255))  # the hole
    glb, m, p = _picture(tmp_path, draw)
    params = get_style("soft-clay").print_params("keychain")
    rep = pf.run(glb, tmp_path / "out", params, mask_path=m, image_path=p)
    assert rep["own_loop"]["diameter_mm"] > 2.5
    assert rep["size_mm"][1] == pytest.approx(params["geometry"]["height_mm"], abs=1.0)  # no second loop on top
    assert any("own key-ring loop" in c["text"] for c in rep["changes"])

    # no hole in the design: a reinforced loop is added on top
    glb, m, p = _picture(tmp_path, lambda d: d.ellipse((100, 160, 500, 680), fill=(200, 80, 60)))
    rep = pf.run(glb, tmp_path / "out2", params, mask_path=m, image_path=p)
    assert "own_loop" not in rep and rep["size_mm"][1] > params["geometry"]["height_mm"] + 3
