"""One click ("Make it now"): photo in, printable parts out, with whatever engines are ready."""
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

from formstudio.api.app import app


@pytest.fixture(scope="module")
def logo_png(tmp_path_factory):
    """A two-colour logo: a red mark and black lettering on white."""
    img = Image.new("RGB", (1800, 520), "white")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((120, 110, 330, 295), 60, fill=(238, 46, 36))
    d.text((420, 120), "factors.ai", font=ImageFont.load_default(size=230), fill=(15, 15, 15))
    p = tmp_path_factory.mktemp("logo") / "factors.png"
    img.save(p)
    return p, None


def _wait(c, jid, seconds=300):
    for _ in range(int(seconds * 10)):
        j = c.get(f"/api/jobs/{jid}").json()
        if j["task"] and j["task"]["status"] in ("done", "error"):
            assert j["task"]["status"] == "done", j["task"]["error"]
            return j
        time.sleep(0.1)
    raise AssertionError("timed out")


def test_one_click_photo_to_magnet(mug_photo):
    with TestClient(app) as c:
        with mug_photo.open("rb") as f:
            j = c.post("/api/jobs", files={"photo": ("coffee_mug.png", f, "image/png")},
                       data={"subject": "mug", "style": "soft-clay"}).json()
        jid = j["id"]
        c.post(f"/api/jobs/{jid}/kind", json={"kind": "object"})  # the flat drawing reads as a logo; test the object path
        # the default engine (local SDXL, not downloaded here) isn't ready, so one click keeps the photo
        c.put("/api/settings", json={"engines": {"stylize": "local-sdxl"}})
        r = c.post(f"/api/jobs/{jid}/auto", json={"format": "magnet"})
        assert r.status_code == 200, r.text
        assert r.json()["task"]["kind"] == "auto"
        j = _wait(c, jid)
        # no AI is available in tests, so the photo is kept and the offline engine builds the shape
        assert j["stage"] == "printified"
        assert j["confirmed"]["engine"] == "photo"
        assert j["reconstruct"]["engine"] == "extrude"
        assert j["print_params"]["format"] == "magnet"
        assert j["parts"] and j["parts"][0]["filament"] == 1
        r = c.post(f"/api/jobs/{jid}/export", json={"formats": ["3mf"]})
        assert r.status_code == 200, r.text
        assert c.get(f"/api/jobs/{jid}").json()["stage"] == "exported"
        assert c.post(f"/api/jobs/{jid}/auto", json={"format": "wheel"}).status_code == 400
        c.delete(f"/api/jobs/{jid}")


def test_one_click_logo(logo_png):
    with TestClient(app) as c:
        with logo_png[0].open("rb") as f:
            j = c.post("/api/jobs", files={"photo": ("factors.png", f, "image/png")},
                       data={"subject": "factors", "style": "soft-clay"}).json()
        jid = j["id"]
        assert j["kind"] == "logo"
        c.post(f"/api/jobs/{jid}/auto", json={})
        j = _wait(c, jid)
        assert j["stage"] == "printified"
        # the exact-artwork path end to end: one preview option, one part per colour plus the plaque
        assert j["candidates"] and j["candidates"][0]["engine"] == "logo"
        assert j["reconstruct"]["engine"] == "logo"
        assert [p["id"] for p in j["parts"]] == ["logo_1", "logo_2", "plaque"]
        c.delete(f"/api/jobs/{jid}")
