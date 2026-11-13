"""Style step: refine an option in words (a new, linked option) and design advice at print scale."""
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from formstudio.api.app import app
from formstudio.engines import STYLIZERS


@pytest.fixture()
def tree_png(tmp_path):
    """A tree with a very thin trunk: the advice should say so."""
    img = Image.new("RGB", (600, 700), "white")
    d = ImageDraw.Draw(img)
    d.polygon([(300, 40), (90, 520), (510, 520)], fill=(60, 150, 60))
    d.rectangle((297, 520, 303, 660), fill=(110, 70, 40))  # 6 px of 480: ~0.4 mm at 35 mm tall
    p = tmp_path / "tree.png"
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


def test_refine_and_advice(tree_png, monkeypatch):
    eng = STYLIZERS["openai-image"]
    seen = {}

    def fake(inp, ctx, progress):
        seen["src"], seen["prompt"], seen["refs"] = inp.cutout, inp.style.edit_prompt_for(inp.subject), inp.style.reference_paths
        Image.open(inp.cutout).save(inp.out_dir / "candidate_1.png")
        return [{"file": "candidate_1.png", "seed": None}]

    with TestClient(app) as c:
        with tree_png.open("rb") as f:
            j = c.post("/api/jobs", files={"photo": ("tree.png", f, "image/png")},
                       data={"subject": "tree", "style": "soft-clay", "format": "magnet"}).json()
        jid = j["id"]
        assert j["print_params"]["format"] == "magnet"  # asked first, set from the start
        c.post(f"/api/jobs/{jid}/kind", json={"kind": "object"})
        c.post(f"/api/jobs/{jid}/stylize", json={"engine": "preview", "n": 1})
        j = _wait(c, jid)
        assert len(j["candidates"]) == 1

        adv = c.get(f"/api/jobs/{jid}/advice", params={"candidate": 0}).json()
        ids = [a["id"] for a in adv]
        assert "thin" in ids and "front" in ids  # the hairline trunk, and the flat-format tip
        thin = next(a for a in adv if a["id"] == "thin")
        assert "thicker" in thin["message"] and "mm at 35 mm tall" in thin["text"]

        # no key: a clear refusal, not a crash
        monkeypatch.setattr(eng, "available", lambda ctx: (False, "no key"))
        r = c.post(f"/api/jobs/{jid}/refine", json={"candidate": 0, "message": "thicker trunk"})
        assert r.status_code == 400 and "key" in r.json()["detail"]

        monkeypatch.setattr(eng, "available", lambda ctx: (True, ""))
        monkeypatch.setattr(eng, "stylize", fake)
        c.post(f"/api/jobs/{jid}/palette", json={"enabled": True, "max_colours": 2})
        r = c.post(f"/api/jobs/{jid}/refine", json={"candidate": 0, "message": "make the trunk much thicker"})
        assert r.status_code == 200, r.text
        j = _wait(c, jid)
        new = j["candidates"][-1]
        assert new["parent"] == j["candidates"][0]["n"] and new["message"] == "make the trunk much thicker"
        assert new["engine"] == "openai-image"
        assert seen["src"].name == j["candidates"][0]["file"].split("/")[-1]  # edits that option, not the photo
        assert "make the trunk much thicker" in seen["prompt"] and "magnet" in seen["prompt"]
        assert all(col["hex"] in seen["prompt"] for col in j["palette"]["colours"])  # the approved colours
        assert seen["refs"] == []  # an edit, not a restyle: no style references
        assert c.post(f"/api/jobs/{jid}/refine", json={"candidate": 9, "message": "x"}).status_code == 404
        c.delete(f"/api/jobs/{jid}")
