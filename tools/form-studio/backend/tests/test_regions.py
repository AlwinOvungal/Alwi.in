"""Picture colours (AMS): the picture's colour regions become parts, one filament each. Off by default."""
import itertools
import time
import zipfile

import numpy as np
import pytest
import trimesh
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from formstudio import paths
from formstudio.api.app import app
from formstudio.core import logo
from formstudio.core.printify import to_manifold


@pytest.fixture(scope="module")
def penguin(tmp_path_factory):
    """A four-colour mascot: purple body, white belly, charcoal eyes, orange beak."""
    img = Image.new("RGB", (700, 700), "white")
    d = ImageDraw.Draw(img)
    d.ellipse((120, 120, 580, 620), fill=(167, 139, 250))
    d.ellipse((230, 260, 470, 600), fill=(247, 243, 235))
    d.polygon([(310, 360), (390, 360), (350, 425)], fill=(245, 158, 11))
    d.ellipse((260, 280, 310, 350), fill=(40, 40, 40))
    d.ellipse((390, 280, 440, 350), fill=(40, 40, 40))
    p = tmp_path_factory.mktemp("penguin") / "penguin.png"
    img.save(p)
    return p


def test_regions_quantise_and_cover(penguin):
    img = Image.open(penguin)
    mask = np.asarray(img.convert("L")) < 250
    labels, cols = logo.regions(img, mask, 4)
    assert [c["name"] for c in cols][:2] == ["Lavender", "Bone White"]  # biggest first
    assert len(cols) == 4 and (labels[mask] >= 0).all() and (labels[~mask] == -1).all()
    labels2, cols2 = logo.regions(img, mask, 2)  # the colour-reduction control
    assert len(cols2) == 2


def _wait(c, jid):
    for _ in range(3000):
        j = c.get(f"/api/jobs/{jid}").json()
        if j["task"] and j["task"]["status"] in ("done", "error"):
            assert j["task"]["status"] == "done", j["task"]["error"]
            return j
        time.sleep(0.1)
    raise AssertionError("timed out")


def test_picture_colours_magnet_end_to_end(penguin):
    with TestClient(app) as c:
        with penguin.open("rb") as f:
            j = c.post("/api/jobs", files={"photo": ("penguin.png", f, "image/png")},
                       data={"subject": "penguin", "style": "soft-clay"}).json()
        jid = j["id"]
        c.post(f"/api/jobs/{jid}/kind", json={"kind": "object"})  # flat art would take the logo path
        c.post(f"/api/jobs/{jid}/auto", json={"format": "magnet"})
        j = _wait(c, jid)
        assert [p["id"] for p in j["parts"]] == ["body"]  # OFF by default: one part, as before

        j = c.post(f"/api/jobs/{jid}/printify", json={"regions": {"enabled": True, "max_colours": 4}}).json()
        parts = j["parts"]
        assert [p["id"] for p in parts] == ["colour_1", "colour_2", "colour_3", "colour_4"]
        assert [p["name"] for p in parts][:2] == ["Lavender", "White"] or parts[0]["name"] == "Lavender"
        assert [p["filament"] for p in parts] == [1, 2, 3, 4]
        assert all("Picture colour" in p["label"] for p in parts)

        d = paths.jobs_dir() / jid / "05_print"
        meshes = [trimesh.load(d / f"part_{i}.stl") for i in range(1, 5)]
        assert all(m.is_watertight and m.volume > 1 for m in meshes)
        ms = [to_manifold(m) for m in meshes]
        for a, b in itertools.combinations(ms, 2):
            assert (a ^ b).volume() < 0.5  # parts key together, never overlap
        total = sum(m.volume for m in meshes)

        # a part can be recoloured; export carries every filament
        j = c.post(f"/api/jobs/{jid}/colours", json={"colour_4": {"hex": "#1D3FBB"}}).json()
        assert j["colours"]["colour_4"]["hex"] == "#1D3FBB"
        sug = c.post(f"/api/jobs/{jid}/colours/suggest").json()
        assert sug["colour_4"]["name"] == "Orange"  # "from the picture" restores the artwork colour
        assert c.post(f"/api/jobs/{jid}/export", json={"formats": ["3mf"]}).status_code == 200
        cfg = zipfile.ZipFile(d / "model.3mf").read("Metadata/model_settings.config").decode()
        assert all(f'key="extruder" value="{n}"' in cfg for n in (1, 2, 3, 4))

        # off again: back to one body, same overall shape
        j = c.post(f"/api/jobs/{jid}/printify", json={"regions": {"enabled": False}}).json()
        assert [p["id"] for p in j["parts"]] == ["body"]
        body = trimesh.load(d / "body.stl")
        assert body.volume == pytest.approx(total, rel=0.02)  # the parts were exactly the body, split
        c.delete(f"/api/jobs/{jid}")
