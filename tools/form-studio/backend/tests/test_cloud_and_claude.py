"""Cloud engines and Claude, with fake services: no network, no keys, no cost."""
import io
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import trimesh
from PIL import Image

from formstudio import claude
from formstudio.core import ingest
from formstudio.core.primitives import Part, ShapeRecipe
from formstudio.core.style import get_style
from formstudio.engines import RECONSTRUCTORS, STYLIZERS, EngineContext, ReconstructInput, StylizeInput


def _png(colour=(90, 110, 50)) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", (256, 256), (255, 255, 255))
    img.paste(Image.new("RGB", (120, 160), colour), (68, 48))
    img.save(buf, "PNG")
    return buf.getvalue()


def _glb() -> bytes:
    return trimesh.creation.box((1, 2, 1)).export(file_type="glb")


class FakeFal:
    """Answers fal's queue API: submit → status → result → file download."""

    def __init__(self):
        self.submitted: list[tuple[str, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "POST":
            endpoint = url.split("queue.fal.run/")[1]
            self.submitted.append((endpoint, json.loads(request.content)))
            assert request.headers["authorization"] == "Key test-fal"
            return httpx.Response(200, json={"status_url": f"https://queue.fal.run/x/{endpoint}/status",
                                             "response_url": f"https://queue.fal.run/x/{endpoint}/result"})
        if url.endswith("/status?logs=1"):
            return httpx.Response(200, json={"status": "COMPLETED"})
        if url.endswith("/result"):
            if "trellis" in url or "hunyuan" in url:
                return httpx.Response(200, json={"model_mesh": {"url": "https://cdn.fal.media/mesh.glb"}})
            return httpx.Response(200, json={"images": [{"url": "https://cdn.fal.media/out.png"}]})
        if url.startswith("https://cdn.fal.media/"):
            assert "authorization" not in request.headers  # the key never goes to the CDN
            return httpx.Response(200, content=_glb() if url.endswith(".glb") else _png())
        return httpx.Response(404)


@pytest.fixture
def ctx():
    fake = FakeFal()
    c = EngineContext(settings={"_fal_transport": httpx.MockTransport(fake), "claude": {"model": "claude-opus-5-5"}},
                      secrets={"fal": "test-fal", "anthropic": "test-anthropic"})
    c.fake = fake  # type: ignore[attr-defined]
    return c


@pytest.fixture
def cutout(tmp_path):
    src = tmp_path / "photo.png"
    src.write_bytes(_png((180, 60, 50)))
    dst = tmp_path / "01_cutout.png"
    ingest.cutout(src, dst)
    return dst


def test_nano_banana_sends_photo_and_style_references(ctx, cutout, tmp_path):
    style = get_style("clay-minimal")
    out = tmp_path / "cands"
    out.mkdir()
    res = STYLIZERS["fal-nano-banana"].stylize(
        StylizeInput(cutout, cutout.with_name("01_cutout_mask.png"), style, "coffee mug", [11, 23], out), ctx, lambda p, m: None)
    assert [r["file"] for r in res] == ["candidate_1.png", "candidate_2.png"]
    endpoint, body = ctx.fake.submitted[0]
    assert endpoint == "fal-ai/nano-banana/edit"
    assert len(body["image_urls"]) == 1 + len(style.reference_paths)  # photo + references
    assert "coffee mug" in body["prompt"] and body["seed"] == 11
    assert Image.open(out / "candidate_1.png").size == (1024, 1024)


def test_trellis_downloads_the_mesh(ctx, cutout, tmp_path):
    out = tmp_path / "04_raw.glb"
    RECONSTRUCTORS["fal-trellis"].reconstruct(
        ReconstructInput(cutout, cutout.with_name("01_cutout_mask.png"), 7, out), ctx, lambda p, m: None)
    endpoint, body = ctx.fake.submitted[0]
    assert endpoint == "fal-ai/trellis" and body["image_url"].startswith("data:image/png;base64,")
    assert trimesh.load(out, force="mesh").is_watertight
    assert not out.with_name("04_upload.png").exists()


def test_custom_endpoint_from_settings(ctx, cutout, tmp_path):
    ctx.settings["fal"] = {"endpoints": {"fal-trellis": "fal-ai/trellis/v2"}}
    RECONSTRUCTORS["fal-trellis"].reconstruct(
        ReconstructInput(cutout, cutout.with_name("01_cutout_mask.png"), 7, tmp_path / "m.glb"), ctx, lambda p, m: None)
    assert ctx.fake.submitted[0][0] == "fal-ai/trellis/v2"


# ---- Claude -------------------------------------------------------------------------

MUG_RECIPE = ShapeRecipe(subject="coffee mug", notes="", symmetry="none", parts=[
    Part(label="body", kind="cylinder", op="add", size=[60, 80, 60], position=[0, 40, 0], rotation_deg=[0, 0, 0], roundness=8, taper=1),
    Part(label="opening", kind="cylinder", op="subtract", size=[48, 80, 48], position=[0, 52, 0], rotation_deg=[0, 0, 0], roundness=6, taper=1),
    Part(label="handle", kind="torus", op="add", size=[40, 10, 40], position=[32, 42, 0], rotation_deg=[90, 0, 0], roundness=0, taper=1),
])


class FakeClaude:
    def __init__(self, parsed, stop_reason="end_turn"):
        self.calls: list[dict] = []
        self.parsed, self.stop_reason = parsed, stop_reason
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self.parse))

    def parse(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(stop_reason=self.stop_reason, parsed_output=self.parsed, model=kw["model"],
                               usage=SimpleNamespace(input_tokens=1200, output_tokens=400))


def test_claude_sculpt_builds_and_saves_the_recipe(ctx, cutout, tmp_path, monkeypatch):
    fake = FakeClaude(MUG_RECIPE)
    monkeypatch.setattr(claude, "_client", lambda key: fake)
    out = tmp_path / "04_raw.glb"
    info = RECONSTRUCTORS["claude-sculpt"].reconstruct(
        ReconstructInput(cutout, cutout.with_name("01_cutout_mask.png"), 0, out, style=get_style("clay-minimal"), subject="coffee mug"),
        ctx, lambda p, m: None)
    call = fake.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["output_format"] is ShapeRecipe
    assert call["thinking"] == {"type": "adaptive"} and call["output_config"]["effort"] == "medium"
    images = [b for b in call["messages"][0]["content"] if b["type"] == "image"]
    assert len(images) >= 2  # the object plus style reference(s)
    assert "clay" in call["system"].lower()  # the style's sculpt guide is included
    assert info["parts"] == 3 and trimesh.load(out, force="mesh").is_watertight
    assert ShapeRecipe.model_validate_json((tmp_path / "04_recipe.json").read_text()) == MUG_RECIPE


@pytest.mark.parametrize("stop,msg", [("refusal", "declined"), ("max_tokens", "cut off")])
def test_claude_stop_reasons_become_friendly_errors(monkeypatch, cutout, stop, msg):
    monkeypatch.setattr(claude, "_client", lambda key: FakeClaude(MUG_RECIPE, stop))
    with pytest.raises(claude.ClaudeError, match=msg):
        claude.ask(key="k", model=None, system="s", images=[("x", cutout)], prompt="p", schema=ShapeRecipe)


def test_claude_needs_a_key(cutout):
    with pytest.raises(claude.ClaudeError, match="Anthropic API key"):
        claude.ask(key=None, model=None, system="s", images=[], prompt="p", schema=ShapeRecipe)
    ok, why = RECONSTRUCTORS["claude-sculpt"].available(EngineContext(settings={}, secrets={}))
    assert not ok and "Anthropic" in why


def test_style_draft_is_cleaned_up(monkeypatch, cutout):
    draft = claude.StyleDraft(
        name="Glossy Toy", description="Shiny toys.", prompt="glossy toy, bright", edit_prompt="Make it a glossy toy",
        negative_prompt="matte", sculpt_guide="Round and bold.",
        soften_mm=9, min_feature_mm=0.1, finish="voxel")
    monkeypatch.setattr(claude, "_client", lambda key: FakeClaude(draft))
    out = claude.draft_style("k", None, [cutout], "")
    assert "{subject}" in out.prompt and "{subject}" in out.edit_prompt
    assert not hasattr(out, "body") and "colour" in claude.STYLE_SYSTEM  # styles never carry colours
    assert out.soften_mm == 3.0 and out.min_feature_mm == 0.8


def test_fal_errors_say_where_to_fix_them():
    import httpx
    import pytest

    from formstudio.engines.base import EngineError
    from formstudio.engines.cloud import FalClient

    for status, words, link in ((402, "balance is empty", "Open fal.ai billing"), (401, "rejected the API key", "Open fal.ai keys")):
        client = FalClient("fal-test", transport=httpx.MockTransport(lambda r, s=status: httpx.Response(s, text="nope")))
        with pytest.raises(EngineError) as e:
            client.run("fal-ai/x", {})
        assert words in str(e.value) and e.value.links[0][0] == link and e.value.detail == "nope"
