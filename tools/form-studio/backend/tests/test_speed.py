"""Speed work: timings, cancel and "use this now", memory budget, caches, cloud batching, the speed test."""
import json
import shutil
import threading
import time
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from formstudio import paths, pipeline
from formstudio.api import settings as settings_mod
from formstudio.api.app import app
from formstudio.api.worker import Bus, Worker
from formstudio.core import bench, printify, timing
from formstudio.core.job import CONFIRMED, RAW_MESH, Job
from formstudio.core.style import get_style
from formstudio.engines import RECONSTRUCTORS, STYLIZERS, EngineContext, local
from formstudio.engines.base import Cancelled, quality_of


def wait_task(client, job_id, statuses=("done", "error", "cancelled"), timeout=120):
    start = time.time()
    while time.time() - start < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["task"] and j["task"]["status"] in statuses:
            return j
        time.sleep(0.1)
    raise AssertionError("task timed out")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def new_job(client, photo) -> str:
    with photo.open("rb") as f:
        r = client.post("/api/jobs", files={"photo": ("mug.png", f, "image/png")}, data={"subject": "coffee mug"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ---- timings -------------------------------------------------------------------------

def test_timings_are_recorded_without_private_details():
    timing.log_path().unlink(missing_ok=True)
    with timing.stage("unit_test_stage", engine="preview", api_key="sk-secret", path="/Users/me/photo.jpg") as info:
        info["warm"] = True
    for s in (2.0, 4.0, 9.0):
        timing.record("unit_rate", s * 2, engine="x", n=2)
    text = timing.log_path().read_text()
    assert "sk-secret" not in text and "/Users/me" not in text
    row = json.loads(text.splitlines()[0])
    assert row["stage"] == "unit_test_stage" and row["warm"] is True and row["outcome"] == "ok"
    assert timing.estimate("unit_rate", engine="x") == 8.0
    assert timing.estimate("unit_rate", per="n", engine="x") == 4.0
    assert timing.estimate("unit_rate", engine="other") is None


def test_failed_and_cancelled_stages_are_marked():
    with pytest.raises(Cancelled), timing.stage("unit_cancel"):
        raise Cancelled()
    with pytest.raises(ValueError), timing.stage("unit_fail"):
        raise ValueError("x")
    rows = {r["stage"]: r for r in timing.read()}
    assert rows["unit_cancel"]["outcome"] == "cancelled" and rows["unit_fail"]["outcome"] == "error"
    assert timing.estimate("unit_fail") is None  # failures never feed estimates


# ---- worker: cancel, replace, priority -----------------------------------------------------

def test_cancel_stops_a_running_task_and_replace_runs_next():
    w = Worker(Bus())
    seen = []

    def slow(progress):
        for i in range(200):
            progress(i / 200, "working")
            seen.append(i)
            time.sleep(0.01)

    t1 = w.submit("job", "stylize", slow)
    while not seen:
        time.sleep(0.01)
    t2 = w.submit("job", "reconstruct", lambda p: p(1.0, "built"), replace=True)
    for _ in range(300):
        if t2.status == "done":
            break
        time.sleep(0.01)
    assert t1.status == "cancelled" and len(seen) < 200
    assert t2.status == "done" and t2.public()["elapsed"] >= 0
    with pytest.raises(RuntimeError):
        w.submit("other", "x", lambda p: time.sleep(0.3)) and w.submit("other", "x", lambda p: None)


def test_background_work_waits_for_real_work():
    w = Worker(Bus())
    order = []
    gate = threading.Event()
    w.submit("a", "x", lambda p: gate.wait(2))  # keep the worker busy while we queue
    w.submit("_warm", "warm", lambda p: order.append("warm"), priority=9)
    w.submit("b", "x", lambda p: order.append("user"))
    gate.set()
    for _ in range(200):
        if len(order) == 2:
            break
        time.sleep(0.01)
    assert order == ["user", "warm"]


# ---- memory budget ------------------------------------------------------------------------

def test_models_stay_loaded_while_they_fit(monkeypatch):
    local.unload_all()
    monkeypatch.setattr(local, "ram_gb", lambda: 32.0)
    local.configure({"keep_loaded": "auto"})
    local._cached("sdxl:cpu", lambda: "SDXL")
    local._cached("sdxl:depth:cpu", lambda: "depth")
    local._cached("triposr:cpu", lambda: "TripoSR")
    assert local.loaded_families() == ["sdxl", "triposr"]  # 9 + 2 GB fit in 16 GB

    local.unload_all()
    monkeypatch.setattr(local, "ram_gb", lambda: 16.0)  # 8 GB budget: SDXL alone is over it
    local._cached("sdxl:cpu", lambda: "SDXL")
    assert not local.would_fit("triposr")
    local._cached("triposr:cpu", lambda: "TripoSR")
    assert local.loaded_families() == ["triposr"]

    local.unload_all()
    monkeypatch.setattr(local, "ram_gb", lambda: 64.0)
    local.configure({"keep_loaded": "one"})  # the old behaviour, for the speed test's "before"
    local._cached("sdxl:cpu", lambda: "SDXL")
    local._cached("triposr:cpu", lambda: "TripoSR")
    assert local.loaded_families() == ["triposr"]
    local.configure({"keep_loaded": "auto"})
    local.unload_all()


def test_out_of_memory_unloads_and_retries_once(monkeypatch):
    calls = []

    def fn():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("MPS backend out of memory")
        return "ok"

    local._cached("sdxl:cpu", lambda: "SDXL")
    assert local._retry_on_oom(fn) == "ok" and len(calls) == 2
    assert local.loaded_families() == []


# ---- settings: quality ------------------------------------------------------------------------

def test_fast_mode_becomes_lightest_quality():
    f = paths.settings_file()
    old = f.read_text() if f.exists() else None
    try:
        f.write_text(json.dumps({"fast_mode": True}))
        s = settings_mod.load()
        assert s["sdxl_quality"] == "lightest" and "fast_mode" not in s
        assert quality_of({}) == "best" and quality_of({"sdxl_quality": "quick"}) == "quick"
    finally:
        f.unlink(missing_ok=True) if old is None else f.write_text(old)
    from formstudio import models

    need = models.packs_needed({"engines": {"stylize": "local-sdxl", "reconstruct": "triposr"}, "sdxl_quality": "quick"})
    assert need[1:3] == ["sdxl", "sdxl-lightning"]


def test_quick_quality_needs_peft():
    eng = STYLIZERS["local-sdxl"]
    ok, why = eng.available(EngineContext(settings={"sdxl_quality": "quick"}))
    if not ok:
        assert "torch" in why or "peft" in why


# ---- cancel + use this now through the API ------------------------------------------------------

class SlowStylizer:
    """Takes a while per option, like local SDXL, and reports progress every step."""
    id, label, kind, note = "slow-test", "Slow (test)", "local", ""

    def available(self, ctx):
        return True, ""

    def stylize(self, inp, ctx, progress):
        for step in range(20):
            progress(step / 20, f"step {step}")
            time.sleep(0.03)
        img = Image.open(inp.cutout).convert("RGB")
        img.save(inp.out_dir / "candidate_1.png")
        return [{"file": "candidate_1.png", "seed": inp.seeds[0]}]


def test_use_this_now_stops_the_rest_and_builds_the_shape(client, mug_photo, monkeypatch):
    monkeypatch.setitem(STYLIZERS, "slow-test", SlowStylizer())
    jid = new_job(client, mug_photo)
    assert client.post(f"/api/jobs/{jid}/stylize", json={"engine": "slow-test", "n": 6}).status_code == 200
    for _ in range(300):  # wait for the first option
        j = client.get(f"/api/jobs/{jid}").json()
        if j["candidates"]:
            break
        time.sleep(0.02)
    assert j["task"]["status"] == "running"
    r = client.post(f"/api/jobs/{jid}/use-now", json={"candidate": 0, "engine": "lathe"})
    assert r.status_code == 200, r.text
    j = wait_task(client, jid, ("done", "error"))
    assert j["task"]["status"] == "done", j["task"]["error"]
    assert j["stage"] == "printified" and j["confirmed"]["index"] == 0
    assert 1 <= len(j["candidates"]) < 6  # the rest were stopped; what was made is kept
    assert not list((Path(paths.jobs_dir()) / jid / "02_candidates").glob(".run*"))
    assert j["timings"]["reconstruct"]["seconds"] >= 0 and "printify" in j["timings"]


def test_cancel_keeps_finished_options(client, mug_photo, monkeypatch):
    monkeypatch.setitem(STYLIZERS, "slow-test", SlowStylizer())
    jid = new_job(client, mug_photo)
    client.post(f"/api/jobs/{jid}/stylize", json={"engine": "slow-test", "n": 8})
    for _ in range(300):
        if client.get(f"/api/jobs/{jid}").json()["candidates"]:
            break
        time.sleep(0.02)
    client.post(f"/api/jobs/{jid}/cancel")
    j = wait_task(client, jid)
    assert j["task"]["status"] == "cancelled"
    assert 1 <= len(j["candidates"]) < 8 and j["stage"] == "stylized"
    # and the object is free again
    assert client.post(f"/api/jobs/{jid}/stylize", json={"engine": "preview", "n": 1}).status_code == 200
    assert wait_task(client, jid)["task"]["status"] == "done"


# ---- caches ------------------------------------------------------------------------------------

def test_print_cache_reuses_the_shape_and_changes_nothing(mug_glb, tmp_path):
    params = get_style("clay-minimal").print_params()
    printify.clear_cache()
    raw = tmp_path / "04_raw.glb"
    shutil.copyfile(mug_glb, raw)
    printify.run(raw, tmp_path / "a", params)
    changed = json.loads(json.dumps(params))
    changed["base"]["margin_mm"] = 5
    hits = printify.CACHE["hits"]
    t0 = time.perf_counter()
    rep_cached = printify.run(raw, tmp_path / "b", changed)
    cached_s = time.perf_counter() - t0
    assert printify.CACHE["hits"] > hits

    printify.CACHE["enabled"] = False
    try:
        t0 = time.perf_counter()
        rep_fresh = printify.run(raw, tmp_path / "c", changed)
        fresh_s = time.perf_counter() - t0
    finally:
        printify.CACHE["enabled"] = True
    import trimesh

    for part in ("body.stl", "base.stl"):
        a = trimesh.load(tmp_path / "b" / part)
        b = trimesh.load(tmp_path / "c" / part)
        assert np.allclose(a.vertices, b.vertices) and (a.faces == b.faces).all()
    assert rep_cached == rep_fresh and rep_cached["watertight"]
    assert cached_s < fresh_s


class CountingReconstructor:
    id, label, kind, note = "count-test", "Counting (test)", "local", ""

    def __init__(self, mesh: Path):
        self.mesh, self.calls = mesh, 0

    def available(self, ctx):
        return True, ""

    def reconstruct(self, inp, ctx, progress):
        self.calls += 1
        shutil.copyfile(self.mesh, inp.out)
        return {"model": "test"}


def test_shape_cache_skips_the_engine_for_the_same_picture(client, mug_photo, mug_glb, monkeypatch):
    eng = CountingReconstructor(mug_glb)
    monkeypatch.setitem(RECONSTRUCTORS, "count-test", eng)
    jid = new_job(client, mug_photo)
    client.post(f"/api/jobs/{jid}/stylize", json={"engine": "preview", "n": 2})
    wait_task(client, jid)
    for _ in range(2):
        assert client.post(f"/api/jobs/{jid}/confirm", json={"candidate": 1}).status_code == 200
        client.post(f"/api/jobs/{jid}/reconstruct", json={"engine": "count-test"})
        j = wait_task(client, jid)
        assert j["task"]["status"] == "done", j["task"]["error"]
    assert eng.calls == 1 and j["reconstruct"]["cached"] is True
    assert client.post(f"/api/jobs/{jid}/confirm", json={"candidate": 0}).status_code == 200
    client.post(f"/api/jobs/{jid}/reconstruct", json={"engine": "count-test"})
    wait_task(client, jid)
    assert eng.calls == 2  # a different picture builds a new shape


# ---- cloud: one request for all options, parallel requests, friendly limits ------------------------------

@pytest.fixture
def cut_job(mug_photo):
    job = pipeline.create(mug_photo, "mug", "coffee mug", "pixel-voxel")
    yield job
    job.delete()


def _png() -> str:
    import base64
    import io

    buf = io.BytesIO()
    Image.new("RGBA", (64, 64), (200, 50, 40, 255)).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def openai_ctx(handler):
    return EngineContext(settings={"_openai_transport": httpx.MockTransport(handler), "_sleep": lambda s: None,
                                   "openai": {}}, secrets={"openai": "sk-test"})


def test_openai_makes_all_options_in_one_request(cut_job):
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body["n"])
        return httpx.Response(200, json={"data": [{"b64_json": _png()} for _ in range(body["n"])]})

    pipeline.stylize(cut_job, openai_ctx(handler), "openai-image", [1, 2, 3], lambda p, m: None)
    assert calls == [3] and len(cut_job.manifest["candidates"]) == 3


def test_openai_waits_out_a_short_rate_limit(cut_job):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "2"},
                                  json={"error": {"code": "rate_limit_exceeded", "message": "Limit 5 images per minute"}})
        return httpx.Response(200, json={"data": [{"b64_json": _png()}]})

    msgs = []
    pipeline.stylize(cut_job, openai_ctx(handler), "openai-image", [1], lambda p, m: msgs.append(m))
    assert len(calls) == 2 and any("trying again in 2 s" in m for m in msgs)


def test_openai_says_why_when_there_is_no_credit(cut_job):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429, json={"error": {"code": "insufficient_quota", "type": "insufficient_quota",
                                                   "message": "You exceeded your current quota"}})

    with pytest.raises(RuntimeError) as e:
        pipeline.stylize(cut_job, openai_ctx(handler), "openai-image", [1], lambda p, m: None)
    assert len(calls) == 1  # no point retrying without credit
    assert "no usable credit" in str(e.value) and "You exceeded your current quota" in e.value.detail


def test_openai_limit_zero_is_explained_and_never_retried(cut_job):
    calls = []
    said = ("Request too large for gpt-image-2.5-flare (for limit gpt-image) in organization org-x on tokens per min "
            "(TPM): Limit 0, Requested 325. You can increase your rate limit by adding a payment method.")

    def handler(request):
        calls.append(1)
        return httpx.Response(429, headers={"retry-after": "2"},
                              json={"error": {"code": "rate_limit_exceeded", "message": said}})

    with pytest.raises(RuntimeError) as e:
        pipeline.stylize(cut_job, openai_ctx(handler), "openai-image", [1], lambda p, m: None)
    assert len(calls) == 1  # waiting can't fix a limit of 0
    assert "allowance of 0" in str(e.value) and e.value.detail == said
    assert [label for label, _ in e.value.links] == ["Open Billing", "Open Limits"]
    assert any("buy at least $5" in step for step in e.value.steps)


def test_task_errors_carry_details_links_and_steps():
    from formstudio.engines.base import EngineError

    w = Worker(Bus())

    def boom(progress):
        raise EngineError("Plain message.", "Service words", [("Open Billing", "https://example.com/billing")], ["Do this."])

    t = w.submit("job-err", "stylize", boom)
    for _ in range(200):
        if t.status == "error":
            break
        time.sleep(0.01)
    pub = t.public()
    assert pub["error"] == "Plain message." and pub["error_detail"] == "Service words"
    assert pub["error_links"] == [{"label": "Open Billing", "url": "https://example.com/billing"}]
    assert pub["error_steps"] == ["Do this."]


class ParallelStylizer(SlowStylizer):
    id = "parallel-test"
    kind = "cloud"
    parallel = 4

    def stylize(self, inp, ctx, progress):
        time.sleep(0.4)
        Image.open(inp.cutout).convert("RGB").save(inp.out_dir / "candidate_1.png")
        return [{"file": "candidate_1.png", "seed": inp.seeds[0]}]


def test_cloud_options_run_side_by_side(cut_job, monkeypatch):
    monkeypatch.setitem(STYLIZERS, "parallel-test", ParallelStylizer())
    t0 = time.perf_counter()
    pipeline.stylize(cut_job, EngineContext(settings={}), "parallel-test", [1, 2, 3, 4], lambda p, m: None)
    assert time.perf_counter() - t0 < 1.2  # 4 × 0.4 s one after another would be 1.6 s
    assert sorted(c["seed"] for c in cut_job.manifest["candidates"]) == [1, 2, 3, 4]
    assert len({c["file"] for c in cut_job.manifest["candidates"]}) == 4


# ---- the speed test ----------------------------------------------------------------------------------

def test_speed_test_reports_times_and_checks(mug_photo, tmp_path):
    ctx = EngineContext(settings=settings_mod.load())
    before = {j.id for j in Job.all()}
    report = bench.run(ctx, [(mug_photo, "coffee mug")], style_id="clay-minimal", stylize_engine="preview",
                       reconstruct_engine="lathe", variants=["before", "best", "quick"], runs=2, options=2,
                       out_dir=tmp_path / "bench")
    assert set(report["variants"]) == {"before", "best"} and report["skipped"] == ["quick"]
    for v in report["variants"].values():
        assert v["checks_ok"], v["checks"]
        assert v["warm"]["first_to_3mf"]["median"] > 0 and v["cold"]["first_option"] > 0
        assert "restyle_image" not in v["stages"] or v["stages"]
    md = (tmp_path / "bench" / "report.md").read_text()
    assert "Photo → 3MF, using the first option right away" in md and "Speed-up compared with Before" in md
    assert (tmp_path / "bench" / "options.png").exists()
    assert {j.id for j in Job.all()} == before  # the test objects are cleaned up
    assert pipeline.SHAPE_CACHE["enabled"] and printify.CACHE["enabled"]  # settings put back


def test_speed_test_api(client, mug_photo):
    client.put("/api/settings", json={"engines": {"stylize": "preview", "reconstruct": "lathe"}})
    new_job(client, mug_photo)
    st = client.get("/api/bench").json()
    assert st["photos"] >= 1
    assert client.post("/api/bench", json={"size": "quick", "compare": False}).status_code == 200
    for _ in range(600):
        st = client.get("/api/bench").json()
        if st["task"]["status"] in ("done", "error", "cancelled"):
            break
        time.sleep(0.2)
    assert st["task"]["status"] == "done", st["task"]["error"]
    assert st["latest"]["variants"]["best"]["checks_ok"]
    assert client.get("/api/bench/files/report.md").status_code == 200
    assert client.get("/api/bench/files/..%2Fsettings.json").status_code == 404
    assert all(not j.get("bench") for j in client.get("/api/jobs").json())
