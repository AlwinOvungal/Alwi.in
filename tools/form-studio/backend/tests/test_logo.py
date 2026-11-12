"""Logos and text: read from the artwork, built exactly, one filament per colour."""
import io
import time
import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

from formstudio import paths
from formstudio.api.app import app
from formstudio.core import logo
from formstudio.core.printify import to_manifold

TEXT = "factors.ai"


@pytest.fixture(scope="module")
def logo_png(tmp_path_factory):
    """A two-colour logo like the one people upload: a red mark and black lettering on white."""
    img = Image.new("RGB", (1800, 520), "white")
    d = ImageDraw.Draw(img)
    red = (238, 46, 36)
    d.rounded_rectangle((120, 110, 330, 190), 60, fill=red)
    d.rounded_rectangle((120, 215, 330, 295), 60, fill=red)
    d.rounded_rectangle((120, 320, 220, 410), 50, fill=red)
    d.text((420, 120), TEXT, font=ImageFont.load_default(size=230), fill=(15, 15, 15))
    p = tmp_path_factory.mktemp("logo") / "factors.png"
    img.save(p)
    # how many separate pieces the lettering has (every letter, the dot and the i's dot)
    black = (np.asarray(img).max(axis=2) < 100)
    return p, ndimage.label(black)[1]


def _photo_like() -> Image.Image:
    """A shaded ball on white, like a product photo."""
    y, x = np.mgrid[0:600, 0:600]
    r = np.hypot(x - 300, y - 300)
    z = np.sqrt(np.clip(200 ** 2 - r ** 2, 0, None)) / 200
    shade = 0.3 + 0.7 * np.clip(((x - 300) * -0.4 + (y - 300) * -0.5) / 200 * 0.5 + z * 0.8, 0, 1)
    img = np.ones((600, 600, 3)) * 245
    img[r < 200] = np.array([230, 120, 40]) * shade[r < 200][:, None]
    img += np.random.default_rng(0).normal(0, 2, img.shape)
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))


def test_detects_logos_not_photos(logo_png):
    path, _ = logo_png
    assert logo.is_graphic(path)
    buf = io.BytesIO()
    Image.open(path).save(buf, "JPEG", quality=75)  # a compressed copy is still a logo
    assert logo.is_graphic(Image.open(buf))
    assert not logo.is_graphic(_photo_like())


def test_layers_keep_every_letter_and_colour(logo_png):
    path, pieces = logo_png
    data = logo.layers(path)
    cols = data["layers"]
    assert len(cols) == 2
    red, black = cols  # left to right: the mark, then the lettering
    assert red["name"] == "Red" and black["name"] == "Black"
    assert ndimage.label(red["mask"])[1] == 3
    assert ndimage.label(black["mask"])[1] == pieces  # no letter merged or lost
    holes = ndimage.binary_fill_holes(black["mask"]) & ~black["mask"]
    assert ndimage.label(holes)[1] == 3  # the insides of a, o and a stay open
    # saved and loaded unchanged
    p = path.with_suffix(".npz")
    logo.save_layers(data, p)
    back = logo.load_layers(p)
    assert all((a["mask"] == b["mask"]).all() for a, b in zip(cols, back["layers"]))


@pytest.mark.parametrize("fmt,backer", [("magnet", "plaque"), ("stand", "plaque"), ("keychain", "outline"),
                                        ("stand", "bar")])
def test_build_parts_fit_together(logo_png, fmt, backer):
    data = logo.layers_cached(logo_png[0])
    params = {"format": fmt, "logo": {"backer": backer, "width_mm": 70}, "geometry": {"finish": "soft", "soften_mm": 2},
              "magnet": {"enabled": fmt == "magnet", "diameter_mm": 10, "depth_mm": 2, "clearance_mm": 0.15},
              "ring": {"enabled": fmt == "keychain", "outer_radius_mm": 4.5, "hole_mm": 4.5, "thickness_mm": 3.5}}
    res = logo.build(data, params)
    parts = res["parts"]
    assert [p["id"] for p in parts] == ["logo_1", "logo_2", "plaque"]
    assert all(p["mesh"].is_watertight and p["mesh"].volume > 0 for p in parts)
    ms = [to_manifold(p["mesh"]) for p in parts]
    for i in range(len(ms)):
        for j in range(i + 1, len(ms)):
            assert (ms[i] ^ ms[j]).volume() < 0.5  # mm³: they touch, they don't overlap
    plaque = parts[-1]["mesh"]
    if fmt == "stand":
        assert plaque.bounds[0][2] == pytest.approx(0, abs=0.05)  # the foot is on the bed
        assert all(p["mesh"].bounds[0][1] < plaque.bounds[1][1] for p in parts[:-1])
    else:
        assert plaque.bounds[0][2] == pytest.approx(0, abs=0.05)
        assert all(p["mesh"].bounds[0][2] >= logo.DEFAULTS["plaque_mm"] - logo.SINK_MM - 0.05 for p in parts[:-1])
    m = logo.DEFAULTS["margin_mm"]
    wide = 70 + (4 * m if fmt == "stand" and backer != "plaque" else 2 * m)  # a bar's foot reaches past the outline
    assert res["report"]["size_mm"][0] == pytest.approx(wide, abs=6 if backer != "plaque" else 1.5)
    if fmt == "magnet":
        solid = to_manifold(plaque)
        assert len(ms) and solid.volume() < (plaque.convex_hull.volume)  # a pocket was cut
    assert res["report"]["watertight"]


def test_finishes(logo_png):
    data = logo.layers_cached(logo_png[0])
    for finish in ("voxel", "faceted"):
        res = logo.build(data, {"format": "magnet", "logo": {"width_mm": 60}, "geometry": {"finish": finish}})
        assert res["report"]["watertight"]


def _wait(c, jid):
    for _ in range(600):
        j = c.get(f"/api/jobs/{jid}").json()
        if j["task"] and j["task"]["status"] in ("done", "error"):
            assert j["task"]["status"] == "done", j["task"]["error"]
            return j
        time.sleep(0.1)
    raise AssertionError("timed out")


def test_logo_job_end_to_end(logo_png):
    with TestClient(app) as c:
        with logo_png[0].open("rb") as f:
            j = c.post("/api/jobs", files={"photo": ("factors.png", f, "image/png")},
                       data={"subject": "factors", "style": "soft-clay"}).json()
        jid = j["id"]
        assert j["kind"] == "logo"
        c.post(f"/api/jobs/{jid}/stylize", json={})  # logos default to the exact preview
        j = _wait(c, jid)
        assert len(j["candidates"]) == 1 and j["candidates"][0]["engine"] == "logo"
        r = c.post(f"/api/jobs/{jid}/use-now", json={"candidate": 0})
        assert r.status_code == 200, r.text
        j = _wait(c, jid)
        assert j["reconstruct"]["engine"] == "logo" and j["stage"] == "printified"
        assert [p["id"] for p in j["parts"]] == ["logo_1", "logo_2", "plaque"]
        assert j["parts"][0]["name"] == "Red" and j["parts"][1]["name"] == "Black"
        assert [p["filament"] for p in j["parts"]] == [1, 2, 3]

        j = c.post(f"/api/jobs/{jid}/colours", json={"logo_2": {"hex": "#1d3fbb"}, "plaque": {"hex": "#ffffff"}}).json()
        assert j["colours"]["logo_2"]["hex"] == "#1D3FBB" and j["colours"]["plaque"]["hex"] == "#FFFFFF"
        assert c.post(f"/api/jobs/{jid}/colours", json={"wheel": {"hex": "#ffffff"}}).status_code == 400
        sug = c.post(f"/api/jobs/{jid}/colours/suggest").json()
        assert sug["logo_1"]["name"] == "Red"

        r = c.post(f"/api/jobs/{jid}/export", json={"formats": ["3mf"]})
        assert r.status_code == 200, r.text
        with zipfile.ZipFile(paths.jobs_dir() / jid / "05_print" / "model.3mf") as z:
            cfg = z.read("Metadata/model_settings.config").decode()
            model = z.read("3D/3dmodel.model").decode()
        assert all(f'key="extruder" value="{n}"' in cfg for n in (1, 2, 3))  # each part on its own filament
        assert "1D3FBB" in model and "FFFFFF" in model

        # a slider change rebuilds from the artwork
        j = c.post(f"/api/jobs/{jid}/printify", json={"logo": {"backer": "outline"}, "format": "keychain"}).json()
        assert j["report"]["logo"] and j["print_params"]["logo"]["backer"] == "outline"
        assert j["colours"]["plaque"]["label"] == "Backing"

        # not a logo after all: back to the AI path (options stay, the shape is redone)
        j = c.post(f"/api/jobs/{jid}/kind", json={"kind": "object"}).json()
        assert j["kind"] == "object" and j["stage"] == "stylized" and len(j["candidates"]) == 1
        assert list(j["colours"]) == ["body", "base"]
        r = c.post(f"/api/jobs/{jid}/stylize", json={"engine": "logo"})
        _ = r  # the queue runs it and reports why it can't
        for _ in range(100):
            t = c.get(f"/api/jobs/{jid}").json()["task"]
            if t and t["status"] in ("done", "error"):
                break
            time.sleep(0.1)
        assert t["status"] == "error" and "Logo or text" in t["error"]
        c.delete(f"/api/jobs/{jid}")
