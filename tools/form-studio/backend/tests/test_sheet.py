"""A product / turnaround sheet (several views + labels) must never print as one blob."""
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

from formstudio import paths
from formstudio.api.app import app
from formstudio.core import ingest, views


def _tree(d: ImageDraw.ImageDraw, cx: int, base: int, s: float, fill):
    """A simple fir tree: three stacked triangles and a trunk."""
    for i, (w, h) in enumerate(((160, 130), (200, 150), (240, 170))):  # tiers overlap: one connected tree
        top = base - int(s * (380 - i * 120))
        d.polygon([(cx, top), (cx - int(s * w / 2), top + int(s * h)), (cx + int(s * w / 2), top + int(s * h))], fill=fill)
    d.rectangle((cx - int(s * 22), base - int(s * 30), cx + int(s * 22), base), fill=(120, 72, 40))


@pytest.fixture(scope="module")
def sheet_png(tmp_path_factory):
    """Like the real failure: three tree views side by side, labels under each, a dimension circle."""
    img = Image.new("RGB", (1300, 1200), "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=34)
    for i, (cx, s) in enumerate(((230, 1.0), (650, 0.85), (1060, 1.0))):
        _tree(d, cx, 820, s, (70, 150, 60))
        d.text((cx - 90, 860), ["Front View", "Side View", "Back View"][i], font=font, fill=(60, 60, 60))
    d.ellipse((600, 960, 780, 1140), outline=(140, 140, 140), width=4)  # "trunk detail" callout
    d.text((800, 1010), "Width: 8 mm", font=font, fill=(60, 60, 60))
    d.text((800, 1060), "Depth: 2 mm", font=font, fill=(60, 60, 60))
    p = tmp_path_factory.mktemp("sheet") / "tree_sheet.png"
    img.save(p)
    return p


def _mask(path):
    return np.asarray(Image.open(path.with_name(path.stem + "_mask.png")).convert("L")) > 24


def test_cutout_drops_labels_and_callouts(sheet_png, tmp_path):
    out = tmp_path / "cut.png"
    ingest.cutout(sheet_png, out)
    m = _mask(out)
    n = ndimage.label(m)[1]
    assert n <= 4  # the trees (a trunk may detach at low res) — not dozens of letters


def test_sheet_is_detected_and_single_objects_are_not(sheet_png, mug_photo):
    assert len(views.panels_of(sheet_png)) == 3
    assert views.panels_of(mug_photo) is None
    # a shaded photo-like ball: one object
    y, x = np.mgrid[0:500, 0:500]
    ball = Image.fromarray(np.where((np.hypot(x - 250, y - 250) < 180)[..., None],
                                    np.array([200, 120, 60]), np.array([250, 250, 250])).astype(np.uint8))
    assert views.panels(ball) is None


def test_main_object_keeps_the_front_tree(sheet_png, tmp_path):
    out = tmp_path / "front.png"
    info = views.main_object(sheet_png, out)
    assert info == {"panels": 3}
    m = _mask(out)
    n = ndimage.label(m)[1]
    assert 1 <= n <= 2  # one tree (its trunk may be a second piece), not three
    ys, xs = np.nonzero(m)
    assert (ys.max() - ys.min()) > (xs.max() - xs.min())  # a tree is taller than wide


def _wait(c, jid, seconds=300):
    for _ in range(int(seconds * 10)):
        j = c.get(f"/api/jobs/{jid}").json()
        if j["task"] and j["task"]["status"] in ("done", "error"):
            assert j["task"]["status"] == "done", j["task"]["error"]
            return j
        time.sleep(0.1)
    raise AssertionError("timed out")


def test_sheet_upload_prints_one_tree(sheet_png):
    with TestClient(app) as c:
        with sheet_png.open("rb") as f:
            j = c.post("/api/jobs", files={"photo": ("tree_sheet.png", f, "image/png")},
                       data={"subject": "tree", "style": "soft-clay"}).json()
        jid = j["id"]
        c.post(f"/api/jobs/{jid}/kind", json={"kind": "object"})  # flat art would take the logo path; test the object one

        # the offline preview restyle answers with the sheet again: the stored option is its front object
        c.post(f"/api/jobs/{jid}/stylize", json={"engine": "preview", "n": 1})
        j = _wait(c, jid)
        cand = j["candidates"][0]
        assert cand["sheet"] == 3 and cand["sheet_file"]
        cpath = paths.jobs_dir() / jid / cand["file"]
        img = np.asarray(Image.open(cpath).convert("RGB"))
        fg = (np.abs(img.astype(int) - 255).max(axis=2) > 28)
        ys, xs = np.nonzero(fg)
        assert (ys.max() - ys.min()) > (xs.max() - xs.min())  # one tree, taller than wide

        # choosing it: the 3D input is one tree, and the sheet's three views are kept for free
        c.post(f"/api/jobs/{jid}/confirm", json={"candidate": 0})
        j = c.get(f"/api/jobs/{jid}").json()
        assert j["confirmed"]["sheet"] == 3
        views_dir = paths.jobs_dir() / jid / "03_views"
        assert all((views_dir / f"{v}.png").exists() for v in ("front", "left", "back"))
        conf = paths.jobs_dir() / jid / "03_confirmed.png"
        m = _mask(conf)
        assert ndimage.label(m)[1] <= 2

        # build + print: one tree on the bed, not three fused with text blobs
        r = c.post(f"/api/jobs/{jid}/reconstruct", json={"engine": "extrude"})
        assert r.status_code == 200, r.text
        j = _wait(c, jid)
        assert j["stage"] == "printified"
        size = j["report"]["size_mm"]
        assert size[0] < size[2] * 1.1  # roughly tree-shaped: not a wide sheet
        c.delete(f"/api/jobs/{jid}")


def test_photo_as_is_also_handles_a_sheet(sheet_png):
    with TestClient(app) as c:
        with sheet_png.open("rb") as f:
            j = c.post("/api/jobs", files={"photo": ("tree_sheet.png", f, "image/png")},
                       data={"subject": "tree", "style": "soft-clay"}).json()
        jid = j["id"]
        c.post(f"/api/jobs/{jid}/kind", json={"kind": "object"})
        j = c.post(f"/api/jobs/{jid}/confirm", json={"candidate": None}).json()  # use photo as is
        assert j["confirmed"]["engine"] == "photo" and j["confirmed"]["sheet"] == 3
        m = _mask(paths.jobs_dir() / jid / "03_confirmed.png")
        assert ndimage.label(m)[1] <= 2
        c.delete(f"/api/jobs/{jid}")
