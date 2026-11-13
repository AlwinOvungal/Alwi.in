"""OpenAI image restyle and Meshy image-to-3D against fake services: no keys, no cost."""
import base64
import io
import json

import httpx
import pytest
import trimesh
from PIL import Image

from formstudio.core import ingest
from formstudio.core.style import get_style
from formstudio.engines import RECONSTRUCTORS, STYLIZERS, EngineContext, ReconstructInput, StylizeInput


def _png_b64() -> str:
    buf = io.BytesIO()
    img = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    img.paste(Image.new("RGBA", (200, 300), (60, 110, 216, 255)), (156, 106))
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


@pytest.fixture
def cutout(tmp_path):
    src = tmp_path / "photo.png"
    img = Image.new("RGB", (400, 400), (255, 255, 255))
    img.paste(Image.new("RGB", (160, 220), (180, 60, 50)), (120, 90))
    img.save(src)
    dst = tmp_path / "01_cutout.png"
    ingest.cutout(src, dst)
    return dst


# ---- OpenAI ---------------------------------------------------------------------

def openai_ctx(handler, **settings):
    return EngineContext(settings={"_openai_transport": httpx.MockTransport(handler), "_sleep": lambda s: None, "openai": settings},
                         secrets={"openai": "sk-test"})


def test_openai_restyle_sends_photo_and_style_references(cutout, tmp_path):
    seen = {}

    def handler(request: httpx.Request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": [{"b64_json": _png_b64()} for _ in range(seen["body"]["n"])]})

    style = get_style("clay-minimal")
    out = tmp_path / "c"
    out.mkdir()
    res = STYLIZERS["openai-image"].stylize(
        StylizeInput(cutout, cutout.with_name("01_cutout_mask.png"), style, "coffee mug", [1, 2, 3], out),
        openai_ctx(handler, model="gpt-image-2.5-flare", quality="low"), lambda p, m: None)
    body = seen["body"]
    assert seen["auth"] == "Bearer sk-test"
    assert body["model"] == "gpt-image-2.5-flare" and body["quality"] == "low" and body["n"] == 3
    assert body["background"] == "transparent" and body["size"] == "1024x1024"
    assert len(body["images"]) == 1 + len(style.reference_paths)
    assert all(i["image_url"].startswith("data:image/png;base64,") for i in body["images"])
    assert "coffee mug" in body["prompt"] and "Completely redraw" in body["prompt"]
    assert "never the objects" in body["prompt"] and "never their colours" in body["prompt"]  # look, not colours
    assert "own colours" in body["prompt"]
    assert [r["file"] for r in res] == ["candidate_1.png", "candidate_2.png", "candidate_3.png"]
    img = Image.open(out / "candidate_1.png")
    assert img.size == (1024, 1024) and img.mode == "RGB" and img.getpixel((5, 5)) == (255, 255, 255)


@pytest.mark.parametrize("status,msg", [(401, "rejected the API key"), (429, "rate limit"), (400, "safety system")])
def test_openai_errors_are_friendly(cutout, tmp_path, status, msg):
    def handler(request):
        return httpx.Response(status, json={"error": {"message": "Rejected by the safety system"}})

    with pytest.raises(RuntimeError, match=msg):
        STYLIZERS["openai-image"].stylize(
            StylizeInput(cutout, cutout.with_name("01_cutout_mask.png"), get_style("pixel-voxel"), "mug", [1], tmp_path),
            openai_ctx(handler), lambda p, m: None)


# ---- Meshy ----------------------------------------------------------------------

class FakeMeshy:
    def __init__(self, final="SUCCEEDED", create_status=200):
        self.final, self.create_status = final, create_status
        self.polls, self.body, self.file_auth = 0, None, "unset"

    def __call__(self, request: httpx.Request):
        url = str(request.url)
        if request.method == "POST":
            assert request.headers["authorization"] == "Bearer msy-test"
            self.body = json.loads(request.content)
            if self.create_status != 200:
                return httpx.Response(self.create_status, json={"message": "nope"})
            return httpx.Response(200, json={"result": "task-123"})
        if url.endswith("/image-to-3d/task-123"):
            self.polls += 1
            if self.polls == 1:
                return httpx.Response(200, json={"id": "task-123", "status": "PENDING", "progress": 0})
            if self.polls == 2:
                return httpx.Response(200, json={"id": "task-123", "status": "IN_PROGRESS", "progress": 60})
            if self.final == "FAILED":
                return httpx.Response(200, json={"status": "FAILED", "task_error": {"message": "image too dark"}})
            return httpx.Response(200, json={"status": "SUCCEEDED", "progress": 100, "consumed_credits": 20,
                                             "model_urls": {"glb": "https://assets.meshy.ai/x/model.glb"}})
        if url.startswith("https://assets.meshy.ai/"):
            self.file_auth = request.headers.get("authorization")
            mesh = trimesh.creation.box((1, 2, 1))  # glTF y-up: 2 tall
            return httpx.Response(200, content=mesh.export(file_type="glb"))
        return httpx.Response(404)


def meshy_ctx(fake, model="latest"):
    return EngineContext(settings={"_meshy_transport": httpx.MockTransport(fake), "meshy": {"ai_model": model, "_poll": 0}},
                         secrets={"meshy": "msy-test"})


def test_meshy_creates_polls_and_downloads(cutout, tmp_path):
    fake = FakeMeshy()
    events = []
    out = tmp_path / "04_raw.glb"
    info = RECONSTRUCTORS["meshy"].reconstruct(
        ReconstructInput(cutout, cutout.with_name("01_cutout_mask.png"), 0, out), meshy_ctx(fake, "meshy-6-lite"),
        lambda p, m: events.append(m))
    assert fake.body["ai_model"] == "meshy-6-lite" and fake.body["should_texture"] is False
    assert fake.body["image_url"].startswith("data:image/png;base64,") and fake.body["target_formats"] == ["glb"]
    assert fake.file_auth is None  # the key never goes to the file host
    assert info == {"task_id": "task-123", "model": "meshy-6-lite", "credits": 20}
    assert any("queue" in e for e in events) and any("60%" in e for e in events)
    mesh = trimesh.load(out, force="mesh")
    assert mesh.is_watertight and mesh.extents[1] == pytest.approx(2)  # stands up (y-up), like other engines
    assert not out.with_name("04_upload.png").exists()


def test_meshy_failure_and_plan_messages(cutout, tmp_path):
    inp = ReconstructInput(cutout, cutout.with_name("01_cutout_mask.png"), 0, tmp_path / "m.glb")
    with pytest.raises(RuntimeError, match="image too dark"):
        RECONSTRUCTORS["meshy"].reconstruct(inp, meshy_ctx(FakeMeshy(final="FAILED")), lambda p, m: None)
    with pytest.raises(RuntimeError, match="Pro plan") as e:
        RECONSTRUCTORS["meshy"].reconstruct(inp, meshy_ctx(FakeMeshy(create_status=402)), lambda p, m: None)
    assert [label for label, _ in e.value.links] == ["Open Meshy plans", "Open Meshy API keys"] and e.value.steps
    ok, why = RECONSTRUCTORS["meshy"].available(EngineContext(settings={}, secrets={}))
    assert not ok and "Meshy" in why


def test_keys_are_stored_and_masked():
    from fastapi.testclient import TestClient

    from formstudio.api.app import app

    with TestClient(app) as c:
        s = c.put("/api/settings", json={"openai_key": "sk-abc-7777", "meshy_key": "msy-8888",
                                         "openai": {"model": "gpt-image-2", "quality": "high"}}).json()
        try:
            assert s["openai"]["key_hint"] == "…7777" and s["meshy"]["key_hint"] == "…8888"
            assert s["openai"]["model"] == "gpt-image-2" and "sk-abc-7777" not in json.dumps(s)
            assert c.post("/api/settings/test-key?service=nope").status_code == 400
            eng = c.get("/api/engines").json()
            assert {e["id"]: e["available"] for e in eng["stylize"]}["openai-image"]
            assert {e["id"]: e["available"] for e in eng["reconstruct"]}["meshy"]
        finally:
            s = c.put("/api/settings", json={"openai_key": "", "meshy_key": ""}).json()
        assert not s["openai"]["has_key"] and not s["meshy"]["has_key"]
