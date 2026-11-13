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


def _shade(base, size=700, cx=300, cy=280):
    """A clay-render light falloff: the same paint from bright highlight to deep shadow."""
    yy, xx = np.mgrid[:size, :size]
    f = np.clip(1.15 - 0.75 * np.hypot(xx - cx, yy - cy) / 400, 0.35, 1.15)[..., None]
    return Image.fromarray(np.clip(np.array(base)[None, None] * f, 0, 255).astype(np.uint8))


def test_regions_are_paints_not_light():
    """The bug in the screenshot: a shaded one-colour clay object split into Moss / Grey / Olive bands."""
    mk = Image.new("L", (700, 700), 0)
    ImageDraw.Draw(mk).ellipse((120, 120, 580, 620), fill=255)
    mask = np.asarray(mk) > 0
    olive = Image.new("RGB", (700, 700), "white")
    olive.paste(_shade((110, 120, 70)), (0, 0), mk)
    noisy = np.asarray(olive).astype(int) + np.random.default_rng(0).integers(-12, 12, (700, 700, 1))  # clay texture
    _, cols = logo.regions(Image.fromarray(np.clip(noisy, 0, 255).astype(np.uint8)), mask, None, mm_per_px=0.07)
    assert len(cols) == 1

    # shaded penguin: still exactly its four paints, eyes kept (they're wider than a nozzle line)
    img = Image.new("RGB", (700, 700), "white")
    img.paste(_shade((167, 139, 250)), (0, 0), mk)
    bm = Image.new("L", (700, 700), 0)
    ImageDraw.Draw(bm).ellipse((230, 260, 470, 600), fill=255)
    img.paste(_shade((247, 243, 235), cx=350, cy=330), (0, 0), bm)
    d = ImageDraw.Draw(img)
    d.polygon([(310, 360), (390, 360), (350, 425)], fill=(245, 158, 11))
    d.ellipse((260, 280, 310, 350), fill=(40, 40, 40))
    d.ellipse((390, 280, 440, 350), fill=(40, 40, 40))
    labels, cols = logo.regions(img, mask, None, mm_per_px=0.07)  # reduction off: every real colour
    names = [c["name"] for c in cols]
    assert len(names) == 4 and {"Lavender", "Charcoal", "Orange"} <= set(names)
    body = labels[200, 350]
    assert (labels[mask & (np.asarray(bm) == 0)] == body).mean() > 0.97  # the shaded body is one region
    _, two = logo.regions(img, mask, 2, mm_per_px=0.07)
    assert len(two) == 2 and two[0]["name"] == "Lavender"
    # eyes narrower than the printable minimum join their neighbour
    _, coarse = logo.regions(img, mask, None, mm_per_px=0.07, min_mm=4.0)
    assert "Charcoal" not in [c["name"] for c in coarse]


def _wait(c, jid):
    for _ in range(3000):
        j = c.get(f"/api/jobs/{jid}").json()
        if j["task"] and j["task"]["status"] in ("done", "error"):
            assert j["task"]["status"] == "done", j["task"]["error"]
            return j
        time.sleep(0.1)
    raise AssertionError("timed out")


def test_picture_colours_magnet_end_to_end(penguin):
    """A magnet is built from the picture itself (no AI 3D step), one part per real colour, on
    filaments 1..N; "Print in one colour" gives the same shape as one body."""
    with TestClient(app) as c:
        with penguin.open("rb") as f:
            j = c.post("/api/jobs", files={"photo": ("penguin.png", f, "image/png")},
                       data={"subject": "penguin", "style": "soft-clay"}).json()
        jid = j["id"]
        c.post(f"/api/jobs/{jid}/kind", json={"kind": "object"})  # flat art would take the logo path
        t0 = time.time()
        c.post(f"/api/jobs/{jid}/auto", json={"format": "magnet"})
        j = _wait(c, jid)
        assert j["reconstruct"]["engine"] == "picture" and time.time() - t0 < 60
        parts = j["parts"]
        assert [p["id"] for p in parts] == ["colour_1", "colour_2", "colour_3", "colour_4"]
        assert parts[0]["name"] == "Lavender"
        assert [p["filament"] for p in parts] == [1, 2, 3, 4]
        assert all("Picture colour" in p["label"] for p in parts)
        rep = j["report"]
        assert rep["relief_source"] == "picture" and rep["fidelity"]["outline_iou"] >= 0.95
        assert rep["fidelity"]["score"] >= 95  # the review's "like the picture" score
        changes = {c["id"]: c for c in rep["changes"]}
        assert {"pocket", "grooves"} <= set(changes)

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

        # colour reduction caps the parts
        j = c.post(f"/api/jobs/{jid}/printify", json={"regions": {"max_colours": 2}}).json()
        assert len(j["parts"]) == 2

        # one colour: back to one body, same overall shape
        j = c.post(f"/api/jobs/{jid}/printify", json={"regions": {"enabled": False, "max_colours": None}}).json()
        assert [p["id"] for p in j["parts"]] == ["body"]
        body = trimesh.load(d / "body.stl")
        assert body.volume == pytest.approx(total, rel=0.02)  # the parts were exactly the body, split
        c.delete(f"/api/jobs/{jid}")
