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
        assert c.delete("/api/styles/clay-minimal").status_code == 200  # built-ins can be deleted (hidden)
        assert c.post("/api/styles/restore", json={"which": "builtins"}).json()["restored"] == 1

        s = c.post("/api/styles", json=EDIT).json()
        sid = s["id"]
        assert sid == "glossy-toy" and s["editable"] and s["version"] == 1 and s["references"] == 0
        assert "palette" not in s and s["geometry"]["height_mm"] == 45  # styles never carry colours

        r = c.post(f"/api/styles/{sid}/references", files={"image": ("a.png", _img((200, 40, 30)), "image/png")})
        assert r.status_code == 200 and r.json()["references"] == 1 and r.json()["version"] == 2
        assert c.get(f"/api/styles/{sid}/references/0").headers["content-type"] == "image/png"
        assert c.post(f"/api/styles/{sid}/references", files={"image": ("x.txt", b"nope", "text/plain")}).status_code == 400

        s = c.put(f"/api/styles/{sid}", json={**EDIT, "name": "Glossy Toy II", "prompt": "glossy toy"}).json()
        assert s["name"] == "Glossy Toy II" and s["version"] == 3 and "{subject}" in s["prompt"]
        assert c.put(f"/api/styles/{sid}", json={**EDIT, "geometry": {"height_mm": 1, "soften_mm": 0.6,
                                                                     "min_feature_mm": 2.0}}).status_code == 422

        dup = c.post("/api/styles/clay-minimal/duplicate").json()
        assert dup["editable"] and dup["references"] == 1 and dup["id"] != "clay-minimal"

        assert c.delete(f"/api/styles/{sid}/references/0").json()["references"] == 0
        assert c.delete(f"/api/styles/{dup['id']}").json()["ok"]
        assert c.get(f"/api/styles/{dup['id']}").json()["removed"] is True  # kept for objects that use it
        assert dup["id"] not in [x["id"] for x in c.get("/api/styles").json()]


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
        assert "#E5533DFF" not in model and "#8A8A85FF" in model  # the style's old colours never reach the print

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


def test_new_style_from_another_with_pictures():
    with TestClient(app) as c:
        s = c.post("/api/styles", json={"name": "Brick Toys", "description": "", "based_on": "pixel-voxel"}).json()
        sid = s["id"]
        assert s["geometry"]["finish"] == "voxel" and "voxel" in s["edit_prompt"].lower() and s["references"] == 0
        red_cream = io.BytesIO()
        img = Image.new("RGB", (200, 200), (255, 255, 255))
        img.paste(Image.new("RGB", (160, 100), (200, 40, 40)), (20, 20))
        img.paste(Image.new("RGB", (160, 50), (240, 230, 205)), (20, 120))
        img.save(red_cream, "PNG")
        files = [("image", (f"{i}.png", red_cream.getvalue(), "image/png")) for i in range(2)]
        s = c.post(f"/api/styles/{sid}/references", files=files).json()
        assert s["references"] == 2
        too_many = [("image", (f"{i}.png", _img((10, 10, 10 * i)), "image/png")) for i in range(3)]
        r = c.post(f"/api/styles/{sid}/references", files=too_many)
        assert r.status_code == 400 and "room for 2 more" in r.json()["detail"]
        s = c.post(f"/api/styles/{sid}/references", files=[("image", ("b.png", _img((20, 90, 200)), "image/png"))]).json()
        assert s["references"] == 3
        s = c.post(f"/api/styles/{sid}/references/2/first").json()
        first = c.get(f"/api/styles/{sid}/references/0").content
        assert Image.open(io.BytesIO(first)).getpixel((5, 5)) == (20, 90, 200)
        assert c.post("/api/styles/suggest-colours").status_code in (404, 405)  # styles don't pick colours any more
        c.delete(f"/api/styles/{sid}")


def test_reference_mode_round_trips():
    c = TestClient(app)
    s = c.post("/api/styles", json={"name": "Mode test", "description": "x"}).json()
    assert s["reference_mode"] == "style"
    s = c.put(f"/api/styles/{s['id']}", json={"name": "Mode test", "description": "x", "reference_mode": "full"}).json()
    assert s["reference_mode"] == "full" and s["version"] == 2
    assert c.put(f"/api/styles/{s['id']}", json={"name": "Mode test", "reference_mode": "everything"}).status_code == 422
    c.delete(f"/api/styles/{s['id']}")


def test_built_in_styles_can_be_deleted_and_restored(mug_photo):
    c = TestClient(app)
    c.post("/api/styles/restore", json={"which": "builtins"})
    with mug_photo.open("rb") as f:
        job = c.post("/api/jobs", files={"photo": ("mug.png", f, "image/png")}, data={"subject": "mug", "style": "low-poly"}).json()
    assert c.delete("/api/styles/low-poly").status_code == 200
    ids = [s["id"] for s in c.get("/api/styles").json()]
    assert "low-poly" not in ids
    assert c.get("/api/styles/removed").json()["builtins"] == 1
    # the object made with it still works, and says which style it was
    j = c.get(f"/api/jobs/{job['id']}").json()
    assert j["style_name"] == "Low Poly" and j["style_removed"] is True
    assert c.get("/api/styles/low-poly").json()["removed"] is True
    assert c.post(f"/api/jobs/{job['id']}/confirm", json={"candidate": None}).status_code == 200
    # a new style can't take over the deleted one's id (older objects point at it)
    s = c.post("/api/styles", json={"name": "Low Poly", "description": ""}).json()
    assert s["id"] != "low-poly"
    c.delete(f"/api/styles/{s['id']}")
    r = c.post("/api/styles/restore", json={"which": "builtins"}).json()
    assert r["restored"] == 1 and "low-poly" in [s["id"] for s in c.get("/api/styles").json()]
    c.delete(f"/api/jobs/{job['id']}")


def test_your_deleted_style_keeps_its_objects_working_and_comes_back(mug_photo):
    c = TestClient(app)
    s = c.post("/api/styles", json={"name": "Keep Me", "description": "x"}).json()
    with mug_photo.open("rb") as f:
        job = c.post("/api/jobs", files={"photo": ("mug.png", f, "image/png")}, data={"subject": "mug", "style": s["id"]}).json()
    assert c.delete(f"/api/styles/{s['id']}").status_code == 200
    assert s["id"] not in [x["id"] for x in c.get("/api/styles").json()]
    assert c.get(f"/api/jobs/{job['id']}").json()["style_name"] == "Keep Me"
    assert c.put(f"/api/styles/{s['id']}", json={"name": "Changed"}).status_code == 403  # restore before editing
    assert c.post("/api/styles/restore", json={"which": "deleted"}).json()["restored"] >= 1
    assert s["id"] in [x["id"] for x in c.get("/api/styles").json()]
    c.delete(f"/api/jobs/{job['id']}")
    c.delete(f"/api/styles/{s['id']}")


def test_the_last_style_cannot_be_deleted():
    from formstudio.core import style as style_mod

    c = TestClient(app)
    visible = [s["id"] for s in c.get("/api/styles").json()]
    try:
        for sid in visible[:-1]:
            assert c.delete(f"/api/styles/{sid}").status_code == 200
        r = c.delete(f"/api/styles/{visible[-1]}")
        assert r.status_code == 400 and "at least one" in r.json()["detail"]
        assert style_mod.default_style_id() == visible[-1]
    finally:
        c.post("/api/styles/restore", json={"which": "builtins"})
        c.post("/api/styles/restore", json={"which": "deleted"})


def test_a_new_style_starts_from_your_inputs_only():
    c = TestClient(app)
    s = c.post("/api/styles", json={
        "name": "Candy Glass", "description": "glossy translucent candy",
        "palette": {"body": {"name": "Cherry", "hex": "#C2185B"}, "base": {"name": "Snow", "hex": "#FAFAFA"}},
        "geometry": {"height_mm": 40, "soften_mm": 0.9, "min_feature_mm": 1.6, "finish": "faceted"},
    }).json()
    words = " ".join([s["prompt"], s["edit_prompt"], s["sculpt_guide"]]).lower()
    assert "glossy translucent candy" in words and "facets" in words
    assert "cherry" not in words and "mainly" not in words and "palette" not in s  # colours never get in
    assert "olive" not in words and "clay" not in words  # nothing from Clay Minimal
    assert s["geometry"]["finish"] == "faceted" and s["geometry"]["height_mm"] == 40 and s["references"] == 0
    assert {f["id"] for f in s["formats"]} == {"stand", "magnet", "keychain", "ornament"}
    c.delete(f"/api/styles/{s['id']}")
