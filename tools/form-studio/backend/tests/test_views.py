"""3 views (front, side, back) and multi-view 3D engines; Clean up."""
import base64
import io
import json
import shutil
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from formstudio import paths, pipeline
from formstudio.api.app import app
from formstudio.core import cleanup, views as views_mod
from formstudio.core.job import VIEWS_DIR
from formstudio.engines import RECONSTRUCTORS, VIEWERS, EngineContext
from formstudio.engines.base import ReconstructInput


def _sheet(transparent: bool, gaps=(60, 30)) -> Image.Image:
    """Three shapes side by side, like a turnaround sheet: red square, green circle, blue triangle."""
    w, h = 900, 400
    img = Image.new("RGBA" if transparent else "RGB", (w, h), (0, 0, 0, 0) if transparent else (250, 250, 248))
    d = ImageDraw.Draw(img)
    x = 40
    d.rectangle((x, 100, x + 200, 320), fill=(200, 40, 30, 255)); x += 200 + gaps[0]
    d.ellipse((x, 100, x + 220, 320), fill=(40, 160, 60, 255)); x += 220 + gaps[1]
    d.polygon([(x, 320), (x + 110, 90), (x + 220, 320)], fill=(40, 70, 200, 255))
    return img


@pytest.mark.parametrize("transparent", [True, False])
def test_sheet_splits_into_front_side_back(tmp_path, transparent):
    p = tmp_path / "sheet.png"
    _sheet(transparent).save(p)
    out = views_mod.split_sheet(p, tmp_path / "v")
    assert list(out) == ["front", "left", "back"]
    colours = []
    for name, f in out.items():
        im = Image.open(f).convert("RGB")
        assert im.width == im.height and im.getpixel((2, 2)) == (255, 255, 255)
        assert f.with_name(f"{name}_mask.png").exists()
        px = [c for c in im.resize((64, 64)).getdata() if c != (255, 255, 255)]
        colours.append(max(range(3), key=lambda k: sum(c[k] for c in px)))
    assert colours == [0, 1, 2]  # red front, green side, blue back: in order, nothing mixed up


def test_odd_sheet_falls_back_to_thirds():
    img = Image.new("RGB", (600, 200), (255, 255, 255))
    ImageDraw.Draw(img).rectangle((10, 50, 590, 150), fill=(0, 0, 0))  # one long object, no gaps
    assert views_mod.find_columns(img) == [(0, 199), (200, 399), (400, 599)]


def _png_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


@pytest.fixture
def confirmed_job(mug_photo):
    job = pipeline.create(mug_photo, "mug", "coffee mug", "clay-minimal")
    pipeline.use_photo_as_is(job)
    yield job
    if job.folder.exists():
        job.delete()


def test_openai_view_maker_one_request_and_views_saved(confirmed_job):
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(200, json={"data": [{"b64_json": _png_b64(_sheet(True))}]})

    ctx = EngineContext(settings={"_openai_transport": httpx.MockTransport(handler), "_sleep": lambda s: None, "openai": {}},
                        secrets={"openai": "sk-test"})
    pipeline.make_views(confirmed_job, ctx, "openai-views", lambda p, m: None)
    assert len(calls) == 1 and calls[0]["size"] == "1536x1024" and "FRONT" in calls[0]["prompt"]
    assert "No text, labels" in calls[0]["prompt"]
    v = pipeline._views(confirmed_job)
    assert v and list(v) == ["front", "left", "back"]
    assert confirmed_job.manifest["views"]["engine"] == "openai-views"
    # picking another picture clears the views: they belong to the chosen one
    confirmed_job.reset_after("stylized")
    assert pipeline._views(confirmed_job) is None and not confirmed_job.path(VIEWS_DIR).exists()


def test_openai_view_maker_explains_limit_zero(confirmed_job):
    said = "Request too large for gpt-image-2.5-sunburst on tokens per min (TPM): Limit 0, Requested 325."

    def handler(request):
        return httpx.Response(429, json={"error": {"code": "rate_limit_exceeded", "message": said}})

    ctx = EngineContext(settings={"_openai_transport": httpx.MockTransport(handler), "_sleep": lambda s: None, "openai": {}},
                        secrets={"openai": "sk-test"})
    with pytest.raises(RuntimeError) as e:
        pipeline.make_views(confirmed_job, ctx, "openai-views", lambda p, m: None)
    assert "allowance of 0" in str(e.value) and e.value.detail == said
    assert not confirmed_job.path(VIEWS_DIR).exists() and not confirmed_job.path(VIEWS_DIR + ".tmp").exists()


class FakeFal:
    def __init__(self, result):
        self.result, self.posts = result, []

    def __call__(self, request: httpx.Request):
        url = str(request.url)
        if request.method == "POST":
            self.posts.append((url, json.loads(request.content)))
            return httpx.Response(200, json={"status_url": "https://queue.fal.run/s", "response_url": "https://queue.fal.run/r"})
        if url.endswith("/s?logs=1"):
            return httpx.Response(200, json={"status": "COMPLETED"})
        if url == "https://queue.fal.run/r":
            return httpx.Response(200, json=self.result)
        return httpx.Response(200, content=self.file)


def test_nano_banana_view_maker(confirmed_job):
    fake = FakeFal({"images": [{"url": "https://cdn.fal/x.png"}]})
    buf = io.BytesIO()
    _sheet(False).save(buf, "PNG")
    fake.file = buf.getvalue()
    ctx = EngineContext(settings={"_fal_transport": httpx.MockTransport(fake)}, secrets={"fal": "fal-test"})
    pipeline.make_views(confirmed_job, ctx, "fal-views", lambda p, m: None)
    assert fake.posts[0][0].endswith("fal-ai/nano-banana/edit") and fake.posts[0][1]["aspect_ratio"] == "3:2"
    assert pipeline._views(confirmed_job)


def _views_for(tmp_path):
    out = {}
    for i, name in enumerate(("front", "left", "back")):
        img = tmp_path / f"{name}.png"
        Image.new("RGB", (64, 64), (200, 40 * i, 30)).save(img)
        mask = tmp_path / f"{name}_mask.png"
        Image.new("L", (64, 64), 255).save(mask)
        out[name] = (img, mask)
    return out


def _inp(tmp_path, views):
    img, mask = tmp_path / "c.png", tmp_path / "c_mask.png"
    Image.new("RGB", (64, 64), (200, 40, 30)).save(img)
    Image.new("L", (64, 64), 255).save(mask)
    return ReconstructInput(img, mask, 7, tmp_path / "raw.glb", views=views)


@pytest.mark.parametrize("with_views", [True, False])
def test_trellis_and_hunyuan_use_the_views(tmp_path, mug_glb, with_views):
    views = _views_for(tmp_path) if with_views else None
    for eid, multi, single in (("fal-trellis", "fal-ai/trellis/multi", "fal-ai/trellis"),
                               ("fal-hunyuan", "fal-ai/hunyuan3d/v2/multi-view", "fal-ai/hunyuan3d/v2/mini")):
        fake = FakeFal({"model_mesh": {"url": "https://cdn.fal/m.glb"}})
        fake.file = mug_glb.read_bytes()
        ctx = EngineContext(settings={"_fal_transport": httpx.MockTransport(fake)}, secrets={"fal": "fal-test"})
        info = RECONSTRUCTORS[eid].reconstruct(_inp(tmp_path, views), ctx, lambda p, m: None)
        url, body = fake.posts[0]
        assert url.endswith("/" + (multi if with_views else single))
        if with_views and eid == "fal-trellis":
            assert len(body["image_urls"]) == 3 and info["views"] == 3
        if with_views and eid == "fal-hunyuan":
            assert {"front_image_url", "left_image_url", "back_image_url"} <= body.keys()


def test_meshy_uses_multi_image_with_views(tmp_path, mug_glb):
    seen = {}

    def handler(request):
        url = str(request.url)
        if request.method == "POST":
            seen["url"], seen["body"] = url, json.loads(request.content)
            return httpx.Response(200, json={"result": "t1"})
        if url.endswith("/t1"):
            seen["poll"] = url
            return httpx.Response(200, json={"status": "SUCCEEDED", "model_urls": {"glb": "https://assets.meshy.ai/m.glb"}})
        return httpx.Response(200, content=mug_glb.read_bytes())

    ctx = EngineContext(settings={"_meshy_transport": httpx.MockTransport(handler), "meshy": {"_poll": 0}}, secrets={"meshy": "msy"})
    info = RECONSTRUCTORS["meshy"].reconstruct(_inp(tmp_path, _views_for(tmp_path)), ctx, lambda p, m: None)
    assert seen["url"].endswith("/multi-image-to-3d") and len(seen["body"]["image_urls"]) == 3
    assert "image_url" not in seen["body"] and seen["poll"].startswith(seen["url"]) and info["views"] == 3


def test_local_view_maker_and_hunyuan_mv_say_how_to_install():
    ctx = EngineContext(settings={})
    ok, why = VIEWERS["local-views"].available(ctx)
    assert not ok and ("with-views" in why or "Not installed" in why)
    ok, why = RECONSTRUCTORS["hunyuan-mv"].available(ctx)
    assert not ok and "hunyuan" in why.lower()


def test_views_api_and_engine_list(mug_photo, monkeypatch):
    class FakeViews:
        id, label, kind, note = "fake-views", "Fake", "offline", ""

        def available(self, ctx):
            return True, ""

        def make_views(self, inp, ctx, progress):
            p = inp.out_dir / "sheet.png"
            _sheet(False).save(p)
            return views_mod.split_sheet(p, inp.out_dir)

    monkeypatch.setitem(VIEWERS, "fake-views", FakeViews())
    with TestClient(app) as c:
        eng = c.get("/api/engines").json()
        assert {e["id"] for e in eng["views"]} >= {"openai-views", "fal-views", "local-views"}
        assert {e["id"]: e["multi_view"] for e in eng["reconstruct"]}["meshy"] is True
        with mug_photo.open("rb") as f:
            jid = c.post("/api/jobs", files={"photo": ("m.png", f, "image/png")}, data={"subject": "mug"}).json()["id"]
        assert c.post(f"/api/jobs/{jid}/views", json={"engine": "fake-views"}).status_code == 400  # pick first
        c.post(f"/api/jobs/{jid}/confirm", json={"candidate": None})
        assert c.post(f"/api/jobs/{jid}/views", json={"engine": "fake-views"}).status_code == 200
        for _ in range(200):
            j = c.get(f"/api/jobs/{jid}").json()
            if j["task"]["status"] in ("done", "error"):
                break
            time.sleep(0.05)
        assert j["task"]["status"] == "done", j["task"]["error"]
        assert j["views"]["files"] == ["03_views/front.png", "03_views/left.png", "03_views/back.png"]
        assert c.get(f"/api/jobs/{jid}/files/03_views/left.png").status_code == 200
        assert c.delete(f"/api/jobs/{jid}/views").json()["views"] is None
        c.delete(f"/api/jobs/{jid}")


def test_cleanup_finds_and_removes_only_leftovers(mug_photo):
    job = pipeline.create(mug_photo, "keep me", "mug", "clay-minimal")
    (job.path("02_candidates/.run3")).mkdir(parents=True)
    (job.path("02_candidates/.run3/x.png")).write_bytes(b"x" * 5000)
    shape = paths.home() / "cache" / "shape"
    shape.mkdir(parents=True, exist_ok=True)
    (shape / "abc.glb").write_bytes(b"y" * 7000)
    for name in ("2026-01-01_00-00-00", "2026-02-01_00-00-00"):
        d = paths.home() / "bench" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "report.md").write_text("r" * 3000)
    with TestClient(app) as c:
        items = {i["id"]: i for i in c.get("/api/cleanup").json()["items"]}
        assert {"temp", "shape", "bench"} <= items.keys()
        runs = [d for d in (paths.home() / "bench").iterdir() if d.is_dir()]
        assert items["temp"]["bytes"] >= 5000 and items["bench"]["count"] == len(runs) - 1  # the latest speed test stays
        resp = c.post("/api/cleanup", json={"ids": ["temp", "shape", "bench"]})
        assert resp.status_code == 200, resp.text
        r = resp.json()
        assert r["freed"] >= 5000 + 7000 + 3000
        assert not {"temp", "shape", "bench"} & {i["id"] for i in r["items"]}
    assert job.path("01_cutout.png").exists() and job.path("00_input.png").exists()  # the object is untouched
    assert len([d for d in (paths.home() / "bench").iterdir() if d.is_dir()]) == 1  # only the latest is left
    job.delete()
    shutil.rmtree(paths.home() / "bench", ignore_errors=True)


def test_three_views_button_picks_the_option_and_makes_views(mug_photo, monkeypatch):
    class FakeViews:
        id, label, kind, note = "fake-views", "Fake", "offline", ""

        def available(self, ctx):
            return True, ""

        def make_views(self, inp, ctx, progress):
            p = inp.out_dir / "sheet.png"
            _sheet(False).save(p)
            return views_mod.split_sheet(p, inp.out_dir)

    monkeypatch.setitem(VIEWERS, "fake-views", FakeViews())
    with TestClient(app) as c:
        assert c.put("/api/settings", json={"views_auto": True}).json()["views_auto"] is True
        c.put("/api/settings", json={"views_auto": False})
        with mug_photo.open("rb") as f:
            jid = c.post("/api/jobs", files={"photo": ("m.png", f, "image/png")}, data={"subject": "mug"}).json()["id"]
        c.post(f"/api/jobs/{jid}/stylize", json={"engine": "preview", "n": 2})
        for _ in range(200):
            if c.get(f"/api/jobs/{jid}").json()["task"]["status"] == "done":
                break
            time.sleep(0.05)
        r = c.post(f"/api/jobs/{jid}/use-now", json={"candidate": 1, "engine": "fake-views", "views": True})
        assert r.status_code == 200, r.text
        for _ in range(200):
            j = c.get(f"/api/jobs/{jid}").json()
            if j["task"]["kind"] == "views" and j["task"]["status"] in ("done", "error"):
                break
            time.sleep(0.05)
        assert j["task"]["status"] == "done", j["task"]["error"]
        assert j["stage"] == "confirmed" and j["confirmed"]["index"] == 1 and len(j["views"]["files"]) == 3
        c.delete(f"/api/jobs/{jid}")


def _fake_mvadapter(root, utils_init="import nvdiffrast  # NVIDIA-only: fails on a Mac\n"):
    """A stand-in MV-Adapter checkout laid out like the real one."""
    pkg = root / "mvadapter"
    for sub in ("utils", "pipelines", "schedulers", "loaders", "models"):
        (pkg / sub).mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "utils" / "__init__.py").write_text(utils_init)
    (pkg / "utils" / "geometry.py").write_text(
        "import torch\n"
        "def get_plucker_embeds_from_cameras_ortho(c2w, ortho_scale, image_size):\n"
        "    assert len(c2w) == len(ortho_scale) == 6 and c2w.shape[1:] == (4, 4)\n"
        "    return torch.zeros(len(c2w), 6, image_size, image_size)\n")
    (pkg / "pipelines" / "__init__.py").write_text("")
    (pkg / "pipelines" / "pipeline_mvadapter_i2mv_sdxl.py").write_text(
        "from types import SimpleNamespace\n"
        "from PIL import Image\n"
        "class _Enc:\n"
        "    def to(self, **kw): return self\n"
        "class _Vae:\n"
        "    sliced = False\n"
        "    def enable_slicing(self): _Vae.sliced = True\n"
        "class MVAdapterI2MVSDXLPipeline:\n"
        "    scheduler = None\n"
        "    cond_encoder = _Enc()\n"
        "    @classmethod\n"
        "    def from_pretrained(cls, *a, **kw): return cls()\n"
        "    def init_custom_adapter(self, num_views): self.n = num_views\n"
        "    def load_custom_adapter(self, *a, **kw): pass\n"
        "    def to(self, **kw): return self\n"
        "    vae = _Vae()  # like diffusers 0.42: no pipe.enable_vae_slicing(), the VAE has enable_slicing()\n"
        "    def __call__(self, prompt, num_images_per_prompt, callback_on_step_end, control_image, **kw):\n"
        "        assert control_image.shape[0] == num_images_per_prompt == 6\n"
        "        callback_on_step_end(self, 0, 0, {})\n"
        "        colours = [(200,40,30),(0,0,0),(40,160,60),(40,70,200),(0,0,0),(0,0,0)]\n"
        "        imgs = []\n"
        "        for c in colours:\n"
        "            im = Image.new('RGB', (768, 768), (255, 255, 255))\n"
        "            im.paste(Image.new('RGB', (300, 300), c), (234, 234))\n"
        "            imgs.append(im)\n"
        "        return SimpleNamespace(images=imgs)\n")
    (pkg / "schedulers" / "scheduling_shift_snr.py").write_text(
        "class ShiftSNRScheduler:\n"
        "    @staticmethod\n"
        "    def from_scheduler(s, **kw): return s\n")


def test_local_view_maker_works_without_nvidia(confirmed_job, monkeypatch, tmp_path):
    import sys

    from formstudio.engines import local, views as views_engine

    root = tmp_path / "MV-Adapter"
    _fake_mvadapter(root)
    monkeypatch.setattr(views_engine, "mvadapter_dir", lambda: root)
    monkeypatch.setattr(local, "_ensure_models", lambda *a, **k: None)
    import diffusers

    monkeypatch.setattr(diffusers.AutoencoderKL, "from_pretrained", classmethod(lambda cls, *a, **k: None))
    for m in [m for m in sys.modules if m == "mvadapter" or m.startswith("mvadapter.") or m == "formstudio_mvadapter_geometry"]:
        monkeypatch.delitem(sys.modules, m)
    local.unload_all()
    ctx = EngineContext(settings={}, device="cpu")
    assert VIEWERS["local-views"].available(ctx)[0]
    pipeline.make_views(confirmed_job, ctx, "local-views", lambda p, m: None)  # utils/__init__ (nvdiffrast) never loads
    v = pipeline._views(confirmed_job)
    picked = [max(range(3), key=lambda k: sum(c[k] for c in Image.open(v[n][0]).convert("RGB").resize((32, 32)).getdata()
                                              if c != (255, 255, 255))) for n in ("front", "left", "back")]
    assert picked == [0, 1, 2]  # 0°, 90° and 180° of the six turns
    pipe_mod = sys.modules["mvadapter.pipelines.pipeline_mvadapter_i2mv_sdxl"]
    assert pipe_mod._Vae.sliced and not hasattr(pipe_mod.MVAdapterI2MVSDXLPipeline, "enable_vae_slicing")
    local.unload_all()


def test_cameras_match_mv_adapter():
    from formstudio.engines.views import camera_to_world

    c2w = camera_to_world([x - 90 for x in [0, 90, 180]])
    assert tuple(round(float(v), 3) for v in c2w[0, :3, 3]) == (0.0, -1.8, 0.0)  # front camera in front
    assert tuple(round(float(v), 3) for v in c2w[2, :3, 3]) == (0.0, 1.8, 0.0)  # back camera behind
    assert float(c2w[0, 3, 3]) == 1.0


def test_project_caches_are_found_and_only_those_go(tmp_path, monkeypatch):
    proj = tmp_path / "form-studio"
    keep = [proj / "backend/formstudio/web_dist/index.html", proj / ".venv/lib/x/__pycache__/a.pyc",
            proj / ".git/objects/__pycache__/b", proj / "backend/formstudio/core.py"]
    gone = [proj / "backend/formstudio/__pycache__/a.pyc", proj / "backend/tests/__pycache__/b.pyc",
            proj / "backend/.pytest_cache/v/c"]
    for f in keep + gone + [proj / "web/node_modules/vite/x.js"]:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"z" * 2000)
    monkeypatch.setattr(cleanup, "_project_dir", lambda: proj)
    items = {i["id"]: i for i in cleanup.scan({"engines": {}})}
    assert items["pycache"]["count"] == 3
    assert items["buildtools"]["careful"] is True  # listed, not ticked
    cleanup.clean(["pycache"], {"engines": {}})
    assert all(f.exists() for f in keep) and not any(f.exists() for f in gone)
    assert (proj / "web/node_modules/vite/x.js").exists()
    cleanup.clean(["buildtools"], {"engines": {}})
    assert not (proj / "web/node_modules").exists()
