"""New style by conversation with ChatGPT: draft, follow-up, style picture, save — never colours."""
import base64
import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from formstudio.api import settings as settings_mod
from formstudio.api.app import app
from formstudio.engines.base import EngineContext


def _png() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 200, 195)).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


DRAFT = {
    "reply": "A glossy vinyl toy look. Should edges be soft or crisp?",
    "questions": ["Soft or crisp edges?"],
    "style": {
        "name": "Vinyl Pop", "description": "Glossy vinyl designer toys in bright red.",
        "shape_language": "bold rounded forms", "surface": "high gloss", "texture": "smooth", "detail": "low",
        "finish": "soft", "soften_mm": 9, "min_feature_mm": 0.2,
        "prompt": "glossy vinyl designer toy, pastel pink colour, bold rounded forms",
        "edit_prompt": "Redraw this {subject} as a glossy vinyl designer toy",
        "negative_prompt": "matte, text", "sculpt_guide": "Few bold rounded parts.",
        "image_prompt": "three sample objects in glossy vinyl, neutral grey",
    },
}


@pytest.fixture()
def openai(monkeypatch):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content or b"{}")
        calls.append((str(req.url), body))
        if req.url.path.endswith("/chat/completions"):
            if body["model"] == "gpt-5-mini" and len(calls) == 1:
                return httpx.Response(404, json={"error": {"code": "model_not_found", "message": "The model does not exist"}})
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(DRAFT)}}]})
        if req.url.path.endswith("/images/generations"):
            return httpx.Response(200, json={"data": [{"b64_json": _png()}]})
        return httpx.Response(404, json={})

    real = settings_mod.context

    def ctx():
        c = real()
        return EngineContext(device=c.device, settings={**c.settings, "_openai_transport": httpx.MockTransport(handler)},
                             secrets={**c.secrets, "openai": "sk-test"})

    monkeypatch.setattr(settings_mod, "context", ctx)
    return calls


def test_style_chat_draft_image_save(openai):
    with TestClient(app) as c:
        st = c.post("/api/style-chats", json={"message": "shiny vinyl toys"}).json()
        cid = st["id"]
        assert [m["role"] for m in st["messages"]] == ["user", "assistant"]
        assert st["messages"][1]["questions"] == ["Soft or crisp edges?"] and "raw" not in st["messages"][1]
        prop = st["proposal"]
        assert openai[1][1]["model"] == "gpt-4.1-mini"  # the chat model wasn't on the account: fell back
        sys = openai[1][1]["messages"][0]["content"]
        assert "NEVER defines colours" in sys
        # no colours survive into the style, numbers are kept sane, {subject} is in both prompts
        text = " ".join(prop[k] for k in ("prompt", "edit_prompt", "description")).lower()
        import re
        assert not re.search(r"\b(red|pink)\b", text) and "in ." not in text and "{subject}" in prop["prompt"] and "{subject}" in prop["edit_prompt"]
        assert prop["soften_mm"] == 2.0 and prop["min_feature_mm"] == 1.2 and "own colours" in prop["edit_prompt"]

        st = c.post(f"/api/style-chats/{cid}/messages", json={"message": "crisper edges please"}).json()
        last = openai[-1][1]["messages"]
        assert any(m["role"] == "assistant" for m in last) and "draft style" in last[-1]["content"]  # history + draft

        st = c.post(f"/api/style-chats/{cid}/image").json()
        assert st["images"] == ["image_1.png"] and st["chosen"] == 0
        assert "vinyl" in openai[-1][1]["prompt"]
        assert c.get(f"/api/style-chats/{cid}/images/image_1.png").headers["content-type"] == "image/png"
        assert c.get(f"/api/style-chats/{cid}/images/..%2Fchat.json").status_code == 404

        s = c.post(f"/api/style-chats/{cid}/save", json={"name": "Vinyl Pop"}).json()
        assert s["name"] == "Vinyl Pop" and s["references"] == 1 and s["editable"] and "palette" not in s
        assert s["id"] in [x["id"] for x in c.get("/api/styles").json()]  # alongside the others
        c.delete(f"/api/styles/{s['id']}")
        assert c.get("/api/style-chats/0123456789ab").status_code == 404


def test_style_chat_needs_a_key(monkeypatch):
    real = settings_mod.context

    def ctx():
        c = real()
        return EngineContext(device=c.device, settings=c.settings, secrets={**c.secrets, "openai": None})

    monkeypatch.setattr(settings_mod, "context", ctx)
    with TestClient(app) as c:
        r = c.post("/api/style-chats", json={"message": "clay"})
        assert r.status_code == 400 and "OpenAI API key" in r.json()["detail"]
