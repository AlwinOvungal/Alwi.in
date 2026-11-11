import io
import zipfile

from fastapi.testclient import TestClient
from PIL import Image

from formstudio import claude
from formstudio.api.app import app

from .test_api import wait  # noqa: F401  (shared helper)


def _img(colour) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), colour).save(buf, "PNG")
    return buf.getvalue()


EDIT = {
    "name": "Glossy Toy",
    "description": "Bright glossy toys",
    "prompt": "a {subject} as a glossy plastic toy",
    "edit_prompt": "Turn this {subject} into a glossy plastic toy",
    "negative_prompt": "matte",
    "sculpt_guide": "Bold rounded forms.",
    "palette": {"body": {"name": "Tomato", "hex": "#E5533D"}, "base": {"name": "Ink", "hex": "#1B1B18"}},
    "geometry": {"height_mm": 45, "soften_mm": 0.6, "min_feature_mm": 2.0},
}


def test_style_lifecycle():
    with TestClient(app) as c:
        builtin = c.get("/api/styles/clay-minimal").json()
        assert builtin["editable"] is False and builtin["references"] == 1
        assert c.put("/api/styles/clay-minimal", json=EDIT).status_code == 403
        assert c.delete("/api/styles/clay-minimal").status_code == 403

        s = c.post("/api/styles", json=EDIT).json()
        sid = s["id"]
        assert sid == "glossy-toy" and s["editable"] and s["version"] == 1 and s["references"] == 0
        assert s["palette"]["body"]["hex"] == "#E5533D" and s["geometry"]["height_mm"] == 45

        r = c.post(f"/api/styles/{sid}/references", files={"image": ("a.png", _img((200, 40, 30)), "image/png")})
        assert r.status_code == 200 and r.json()["references"] == 1 and r.json()["version"] == 2
        assert c.get(f"/api/styles/{sid}/references/0").headers["content-type"] == "image/png"
        assert c.post(f"/api/styles/{sid}/references", files={"image": ("x.txt", b"nope", "text/plain")}).status_code == 400

        s = c.put(f"/api/styles/{sid}", json={**EDIT, "name": "Glossy Toy II", "prompt": "glossy toy"}).json()
        assert s["name"] == "Glossy Toy II" and s["version"] == 3 and "{subject}" in s["prompt"]
        assert c.put(f"/api/styles/{sid}", json={**EDIT, "palette": {"body": {"name": "x", "hex": "red"},
                                                                    "base": EDIT["palette"]["base"]}}).status_code == 422

        dup = c.post("/api/styles/clay-minimal/duplicate").json()
        assert dup["editable"] and dup["references"] == 1 and dup["id"] != "clay-minimal"

        assert c.delete(f"/api/styles/{sid}/references/0").json()["references"] == 0
        assert c.delete(f"/api/styles/{dup['id']}").json()["ok"]
        assert c.get(f"/api/styles/{dup['id']}").status_code == 404


def test_object_uses_its_style_and_can_switch(mug_photo):
    with TestClient(app) as c:
        sid = c.post("/api/styles", json={**EDIT, "name": "Ink Toy"}).json()["id"]
        with mug_photo.open("rb") as f:
            job = c.post("/api/jobs", files={"photo": ("cup.png", f, "image/png")}, data={"subject": "cup", "style": sid}).json()
        jid = job["id"]
        assert job["style"]["id"] == sid and job["print_params"]["geometry"]["height_mm"] == 45
        c.post(f"/api/jobs/{jid}/confirm", json={"candidate": None})
        c.post(f"/api/jobs/{jid}/reconstruct", json={"engine": "lathe"})
        wait(c, jid)
        c.post(f"/api/jobs/{jid}/export", json={"formats": ["3mf"]})
        from formstudio.core.job import Job

        with zipfile.ZipFile(Job.load(jid).path("05_print", "model.3mf")) as z:
            model = z.read("3D/3dmodel.model").decode()
        assert "#E5533DFF" in model and "#1B1B18FF" in model

        job = c.post(f"/api/jobs/{jid}/style", json={"style": "clay-minimal"}).json()
        assert job["style"]["id"] == "clay-minimal" and job["stage"] == "cutout"
        assert c.post(f"/api/jobs/{jid}/style", json={"style": "nope"}).status_code == 404


def test_draft_and_naming_need_a_key_and_use_claude(mug_photo, monkeypatch):
    with TestClient(app) as c:
        r = c.post("/api/styles/clay-minimal/draft", json={"notes": ""})
        assert r.status_code == 400 and "Anthropic" in r.json()["detail"]

        c.put("/api/settings", json={"anthropic_key": "sk-test-1234"})
        try:
            assert c.get("/api/settings").json()["claude"]["key_hint"] == "…1234"
            calls = []

            def fake_ask(**kw):
                calls.append(kw)
                return kw["schema"](name="french press") if kw["schema"] is claude.ObjectName else None

            monkeypatch.setattr(claude, "ask", fake_ask)
            with mug_photo.open("rb") as f:
                job = c.post("/api/jobs", files={"photo": ("IMG_0042.png", f, "image/png")}).json()
            assert job["subject"] == "french press" and job["name"] == "french press"
            assert calls[0]["effort"] == "low"
            assert c.post(f"/api/jobs/{job['id']}/suggest-name").json() == {"subject": "french press"}
        finally:
            c.put("/api/settings", json={"anthropic_key": ""})


def test_rebuild_from_recipe(mug_photo, monkeypatch):
    from .test_cloud_and_claude import MUG_RECIPE, FakeClaude

    monkeypatch.setattr(claude, "_client", lambda key: FakeClaude(MUG_RECIPE))
    with TestClient(app) as c:
        c.put("/api/settings", json={"anthropic_key": "sk-test-9999"})
        try:
            with mug_photo.open("rb") as f:
                jid = c.post("/api/jobs", files={"photo": ("m.png", f, "image/png")}, data={"subject": "mug"}).json()["id"]
            assert c.post(f"/api/jobs/{jid}/rebuild-recipe").status_code == 400  # no recipe yet
            c.post(f"/api/jobs/{jid}/confirm", json={"candidate": None})
            c.post(f"/api/jobs/{jid}/reconstruct", json={"engine": "claude-sculpt"})
            job = wait(c, jid)
            assert "04_recipe.json" in job["files"] and job["reconstruct"]["parts"] == 3
            job = c.post(f"/api/jobs/{jid}/rebuild-recipe").json()
            assert job["report"]["watertight"]
        finally:
            c.put("/api/settings", json={"anthropic_key": ""})
