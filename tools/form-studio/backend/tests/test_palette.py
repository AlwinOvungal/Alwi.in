"""Photo step: "Reduce colours" — off by default, a preview, editable colours, used through to the print."""
import time

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from formstudio import paths
from formstudio.api.app import app

import pytest
from PIL import ImageDraw


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


def _wait(c, jid):
    for _ in range(3000):
        j = c.get(f"/api/jobs/{jid}").json()
        if j["task"] and j["task"]["status"] in ("done", "error"):
            assert j["task"]["status"] == "done", j["task"]["error"]
            return j
        time.sleep(0.1)
    raise AssertionError("timed out")


def test_palette_reduce_edit_restore_and_print(penguin):
    with TestClient(app) as c:
        with penguin.open("rb") as f:
            j = c.post("/api/jobs", files={"photo": ("penguin.png", f, "image/png")},
                       data={"subject": "penguin", "style": "soft-clay"}).json()
        jid = j["id"]
        c.post(f"/api/jobs/{jid}/kind", json={"kind": "object"})
        d = paths.jobs_dir() / jid
        assert not (j.get("palette") or {}).get("enabled")  # off by default: nothing reduced

        j = c.post(f"/api/jobs/{jid}/palette", json={"enabled": True, "max_colours": 3}).json()
        pal = j["palette"]
        assert pal["enabled"] and pal["max"] == 3 and len(pal["colours"]) == 3
        assert "02_reduced.png" in j["files"]
        red = np.asarray(Image.open(d / "02_reduced.png").convert("RGB"))
        assert len({tuple(p) for p in red.reshape(-1, 3)} - {(255, 255, 255)}) == 3  # exactly the 3 colours

        # change one colour: the preview follows; restore puts the picture's own back
        j = c.post(f"/api/jobs/{jid}/palette", json={"enabled": True, "colours": [None, "#1D3FBB"]}).json()
        assert j["palette"]["colours"][1]["hex"] == "#1D3FBB" and j["palette"]["colours"][1]["original"] != "#1D3FBB"
        red = np.asarray(Image.open(d / "02_reduced.png").convert("RGB"))
        assert ((red == [0x1D, 0x3F, 0xBB]).all(-1)).sum() > 100
        j = c.post(f"/api/jobs/{jid}/palette", json={"enabled": True, "restore": True}).json()
        assert all(x["hex"] == x["original"] for x in j["palette"]["colours"])
        assert c.post(f"/api/jobs/{jid}/palette", json={"enabled": True, "colours": ["red"]}).status_code == 400

        # recolour, then make it: the print has 3 parts in the approved colours
        c.post(f"/api/jobs/{jid}/palette", json={"enabled": True, "colours": [None, None, "#1D3FBB"]})
        c.post(f"/api/jobs/{jid}/auto", json={"format": "magnet"})
        j = _wait(c, jid)
        assert j["confirmed"]["engine"] == "photo"
        conf = np.asarray(Image.open(d / "03_confirmed.png").convert("RGB"))
        assert len({tuple(p) for p in conf[::4, ::4].reshape(-1, 3)}) <= 5  # the reduced picture went on
        hexes = [p["hex"] for p in j["parts"]]
        assert len(hexes) == 3 and "#1D3FBB" in hexes
        assert j["print_params"]["regions"]["max_colours"] == 3

        # off: nothing reduced, the preview is gone, the print may use every colour again
        j = c.post(f"/api/jobs/{jid}/palette", json={"enabled": False}).json()
        assert not j["palette"]["enabled"] and "02_reduced.png" not in j["files"]
        assert j["print_params"]["regions"]["max_colours"] is None
        c.delete(f"/api/jobs/{jid}")


def test_palette_steers_the_restyle(penguin, monkeypatch):
    """Restyles start from the reduced picture and are told to keep to its colours."""
    from formstudio import pipeline
    from formstudio.core.job import Job
    from formstudio.engines import STYLIZERS

    seen = {}

    def fake(inp, ctx, progress):
        seen["cutout"], seen["prompt"] = inp.cutout.name, inp.style.edit_prompt
        Image.open(inp.cutout).save(inp.out_dir / "candidate_1.png")
        return [{"file": "candidate_1.png", "seed": 1}]

    monkeypatch.setattr(STYLIZERS["preview"], "stylize", fake)
    job = pipeline.create(penguin, "penguin", "penguin", "soft-clay")
    pipeline.set_kind(job, "object")
    pal = pipeline.set_palette(job, True, 2)
    from formstudio.api import settings as settings_mod

    pipeline.stylize(job, settings_mod.context(), "preview", [1], lambda p, m: None)
    assert seen["cutout"] == "02_reduced.png"
    assert all(c["hex"] in seen["prompt"] for c in pal["colours"])
    Job.load(job.id).delete() if hasattr(Job, "delete") else None
