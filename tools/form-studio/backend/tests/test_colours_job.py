"""Choosing an object's filament colours."""
import time
import zipfile

from fastapi.testclient import TestClient

from formstudio import paths
from formstudio.api.app import app


def _wait(c, jid):
    for _ in range(300):
        j = c.get(f"/api/jobs/{jid}").json()
        if j["task"] and j["task"]["status"] in ("done", "error"):
            assert j["task"]["status"] == "done", j["task"]["error"]
            return j
        time.sleep(0.1)
    raise AssertionError("timed out")


def test_choose_colours_export_and_reset(mug_photo):
    with TestClient(app) as c:
        with mug_photo.open("rb") as f:
            jid = c.post("/api/jobs", files={"photo": ("m.png", f, "image/png")}, data={"subject": "mug", "style": "clay-minimal"}).json()["id"]
        c.post(f"/api/jobs/{jid}/confirm", json={"candidate": None})
        c.post(f"/api/jobs/{jid}/reconstruct", json={"engine": "lathe"})
        j = _wait(c, jid)
        assert j["colours"]["body"]["name"] == "Olive" and not j["colours_custom"]  # the style's to begin with
        c.post(f"/api/jobs/{jid}/export", json={"formats": ["3mf"]})
        j = c.get(f"/api/jobs/{jid}").json()
        assert j["stage"] == "exported"
        report_before = j["report"]

        j = c.post(f"/api/jobs/{jid}/colours", json={"body": {"hex": "#ff6a13"}, "base": {"hex": "#000000", "name": "Black"}}).json()
        assert j["colours"]["body"]["hex"] == "#FF6A13" and j["colours"]["body"]["name"]  # named for you
        assert j["colours"]["base"] == {"hex": "#000000", "name": "Black"} and j["colours_custom"]
        assert j["stage"] == "printified" and j["exports"] == []  # the old 3MF had the old colours
        assert j["report"] == report_before  # colour isn't geometry: no rebuild

        r = c.post(f"/api/jobs/{jid}/export", json={"formats": ["3mf"]})
        assert r.status_code == 200, r.text
        j = r.json()
        with zipfile.ZipFile(paths.jobs_dir() / jid / next(e for e in j["exports"] if e.endswith(".3mf"))) as z:
            text = "".join(z.read(n).decode(errors="ignore") for n in z.namelist() if n.endswith((".model", ".config")))
        assert "FF6A13" in text.upper() and "000000" in text

        # switching format keeps your colours
        j = c.post(f"/api/jobs/{jid}/printify", json={"format": "magnet"}).json()
        assert j["colours"]["body"]["hex"] == "#FF6A13"

        sug = c.post(f"/api/jobs/{jid}/colours/suggest").json()
        assert sug["body"]["hex"].startswith("#") and sug["base"]["hex"].startswith("#")

        j = c.delete(f"/api/jobs/{jid}/colours").json()
        assert j["colours"]["body"]["name"] == "Olive" and not j["colours_custom"]
        assert c.post(f"/api/jobs/{jid}/colours", json={"body": {"hex": "orange"}}).status_code == 422
        c.delete(f"/api/jobs/{jid}")
