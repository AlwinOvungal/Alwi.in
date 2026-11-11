"""Model downloads with fake Hugging Face functions: no network, no gigabytes."""
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from formstudio import models
from formstudio.engines.local import _speedups

FILES = {"org/model-a": [("config.json", 100), ("weights.bin", 4000)], "org/model-b": [("w.bin", 1000)]}
PACK = {"label": "Test · Pack", "used_by": "tests", "repos": {"org/model-a": ["*"], "org/model-b": ["w.bin"]}}


@pytest.fixture
def fake_hub(tmp_path, monkeypatch):
    cache = tmp_path / "hub"
    monkeypatch.setitem(models.PACKS, "test", PACK)
    monkeypatch.setattr(models, "hub_cache", lambda: cache)
    monkeypatch.setattr(models, "_list_remote", lambda repo: FILES[repo] + [("README.md", 5)] * (repo == "org/model-b"))
    calls = {"n": 0, "fail": 0}

    def path_of(repo, filename):
        return models._repo_dir(repo) / "snapshots" / "main" / filename

    def download(repo, filename, tqdm_class):
        calls["n"] += 1
        if calls["fail"]:
            calls["fail"] -= 1
            raise ConnectionError("network down")
        size = dict(FILES[repo])[filename]
        bar = tqdm_class(total=size)
        for _ in range(4):  # arrive in pieces, like a real download
            bar.update(size // 4)
            time.sleep(0.01)
        p = path_of(repo, filename)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)
        return str(p)

    monkeypatch.setattr(models, "_download_file", download)
    monkeypatch.setattr(models, "_is_cached", lambda repo, f: path_of(repo, f).exists())
    return calls


def test_download_reports_bytes_speed_and_finishes(fake_hub):
    assert models.status("test")["status"] == "missing"
    seen = []
    models.download("test", lambda p, m: seen.append((p, m)))
    st = models.status("test")
    assert st["status"] == "ready" and st["total_bytes"] == 5100 and st["done_bytes"] == 5100
    assert seen[-1][0] == 1.0 and "ready" in seen[-1][1]
    assert any("of" in m and "Downloading Pack" in m for _, m in seen)
    fracs = [p for p, _ in seen if p is not None]
    assert fracs == sorted(fracs)  # never goes backwards


def test_partial_then_resume(fake_hub, monkeypatch):
    real = models._download_file
    done = []

    def only_first(repo, filename, bar):
        if done:
            raise models.DownloadStopped()
        done.append(1)
        return real(repo, filename, bar)

    monkeypatch.setattr(models, "_download_file", only_first)
    with pytest.raises(models.DownloadStopped):
        models.download("test")
    assert models.status("test")["status"] == "partial"
    monkeypatch.setattr(models, "_download_file", real)
    models.download("test")
    assert models.status("test")["status"] == "ready"


def test_network_errors_retry_then_explain(fake_hub):
    fake_hub["fail"] = 2
    models.download("test", retry_waits=(0, 0))  # two failures, then it works
    assert models.status("test")["status"] == "ready"
    models.delete("test")
    fake_hub["fail"] = 10
    with pytest.raises(models.DownloadFailed, match="continue where it stopped"):
        models.download("test", retry_waits=(0, 0))


def test_delete_only_touches_its_own_repos(fake_hub):
    models.download("test")
    other = models.hub_cache() / "models--someone--else"
    other.mkdir(parents=True)
    (other / "keep.bin").write_bytes(b"1")
    freed = models.delete("test")
    assert freed >= 5100 and models.status("test")["status"] == "missing"
    assert (other / "keep.bin").exists()


def test_background_download_stop_and_fresh(fake_hub, monkeypatch):
    gate = threading.Event()
    real = models._download_file

    def slow(repo, filename, bar):
        gate.wait(2)
        return real(repo, filename, bar)

    monkeypatch.setattr(models, "_download_file", slow)
    dl = models.Downloads()
    st = dl.start("test")
    assert dl.running("test") and dl.start("test") is st  # one at a time
    dl.stop("test")
    gate.set()
    for _ in range(100):
        if st.status != "downloading":
            break
        time.sleep(0.02)
    assert st.status == "stopped"
    monkeypatch.setattr(models, "_download_file", real)
    st2 = dl.start("test", fresh=True)
    for _ in range(200):
        if st2.status != "downloading":
            break
        time.sleep(0.02)
    assert st2.status == "done" and models.status("test")["status"] == "ready"
    events = []
    dl.ensure("test", lambda p, m: events.append(m))  # already ready: returns at once
    assert events == []


def test_speedups_can_leave_attention_alone():
    calls = []
    pipe = SimpleNamespace(enable_attention_slicing=lambda: calls.append("slice"),
                           vae=SimpleNamespace(enable_tiling=lambda: calls.append("vae.tiling")))
    _speedups(pipe, attention_slicing=False)  # with an IP-Adapter loaded
    assert calls == ["vae.tiling"]


@pytest.mark.parametrize("style", ["new", "old", "none"])
def test_speedups_work_with_any_diffusers(style):
    calls = []
    pipe = SimpleNamespace(enable_attention_slicing=lambda: calls.append("slice"))
    if style == "new":
        pipe.vae = SimpleNamespace(enable_tiling=lambda: calls.append("vae.tiling"))
    elif style == "old":
        pipe.enable_vae_tiling = lambda: calls.append("pipe.tiling")
    _speedups(pipe)
    assert calls[0] == "slice"
    assert calls[1:] == {"new": ["vae.tiling"], "old": ["pipe.tiling"], "none": []}[style]


def test_models_api(fake_hub):
    from fastapi.testclient import TestClient

    from formstudio.api.app import app

    with TestClient(app) as c:
        rows = {r["id"]: r for r in c.get("/api/models").json()["packs"]}
        assert {"matting", "triposr-code", "sdxl", "sdxl-turbo", "triposr", "hunyuan", "test"} <= set(rows)
        assert rows["test"]["status"] == "missing" and rows["test"]["total_bytes"] == 5100
        r = c.post("/api/models/test/download")
        assert r.status_code == 202
        for _ in range(200):
            row = {x["id"]: x for x in c.get("/api/models").json()["packs"]}["test"]
            if row["download"]["status"] != "downloading":
                break
            time.sleep(0.02)
        assert row["status"] == "ready" and row["download"]["status"] == "done"
        assert c.delete("/api/models/test").json()["freed_bytes"] >= 5100
        assert c.post("/api/models/nope/download").status_code == 404


def test_delete_all_also_clears_the_chunk_cache(fake_hub):
    from fastapi.testclient import TestClient

    from formstudio.api.app import app

    xet = models.hub_cache().parent / "xet"
    (xet / "chunk-cache").mkdir(parents=True)
    (xet / "chunk-cache" / "c1").write_bytes(b"z" * 300)
    (xet / "logs").mkdir()
    models.download("test")
    with TestClient(app) as c:
        freed = c.delete("/api/models").json()["freed_bytes"]
    assert freed >= 5100 + 300
    assert not (xet / "chunk-cache").exists() and (xet / "logs").exists()


def test_delete_follows_links_into_the_shared_store(fake_hub):
    """Newer huggingface_hub keeps data in hub/blobs/<xx>/<hash> and links to it from each model folder."""
    store = models.hub_cache() / "blobs" / "ab"
    store.mkdir(parents=True)
    mine, shared = store / "abc123", store / "abshared"
    mine.write_bytes(b"m" * 7000)
    shared.write_bytes(b"s" * 900)
    a = models._repo_dir("org/model-a") / "blobs"
    a.mkdir(parents=True)
    (a / "w1").symlink_to(mine)
    (a / "w2").symlink_to(shared)
    other = models.hub_cache() / "models--someone--else" / "blobs"
    other.mkdir(parents=True)
    (other / "x").symlink_to(shared)  # another model uses this one too
    assert models.status("test")["on_disk_bytes"] >= 7900
    freed = models.delete("test")
    assert freed >= 7000
    assert not mine.exists() and shared.exists()  # shared data kept for the other model


@pytest.fixture
def fake_engines(tmp_path, monkeypatch, fake_hub):
    u2net = tmp_path / "u2net"
    monkeypatch.setenv("U2NET_HOME", str(u2net))
    vendor = models.paths.home() / "vendor" / "TripoSR"

    def matting():
        (u2net / "models").mkdir(parents=True, exist_ok=True)  # newer rembg keeps models one folder down
        (u2net / "models" / "isnet-general-use.onnx").write_bytes(b"o" * (60 * 1024**2))

    def triposr(dest):
        (dest / "tsr").mkdir(parents=True)
        (dest / "tsr" / "system.py").write_text("# fake")

    monkeypatch.setattr(models, "_matting_download", matting)
    monkeypatch.setattr(models, "_triposr_download", triposr)
    yield u2net, vendor
    import shutil

    shutil.rmtree(vendor, ignore_errors=True)


def test_engine_packs_download_and_delete(fake_engines):
    u2net, vendor = fake_engines
    for pack in ("matting", "triposr-code"):
        assert models.status(pack)["status"] == "missing"
        models.download(pack)
        assert models.status(pack)["status"] == "ready"
    assert (vendor / "tsr").is_dir()
    assert models.delete("matting") >= 60 * 1024**2 and not u2net.exists()
    models.delete("triposr-code")
    assert not vendor.exists()


def test_packs_needed_follow_the_engines():
    assert models.packs_needed({"engines": {"stylize": "local-sdxl", "reconstruct": "triposr"}}) == \
        ["matting", "sdxl", "triposr-code", "triposr"]
    assert models.packs_needed({"engines": {"stylize": "local-sdxl", "reconstruct": "triposr"}, "fast_mode": True})[1] == "sdxl-turbo"
    assert models.packs_needed({"engines": {"stylize": "openai-image", "reconstruct": "meshy"}}) == ["matting"]


def test_start_over_deletes_everything_then_downloads(fake_engines, monkeypatch):
    u2net, vendor = fake_engines
    models.download("test")
    models.download("matting")
    stale = models.hub_cache() / "models--org--model-a" / "stale.bin"
    stale.write_bytes(b"old")
    monkeypatch.setattr(models, "packs_needed", lambda settings: ["matting", "triposr-code", "test"])
    dl = models.Downloads()
    q = dl.start_over({})
    assert not stale.exists()  # the old files went first
    for _ in range(300):
        if q.status != "running":
            break
        time.sleep(0.02)
    assert q.status == "done" and q.public(dl.states)["step"] == 3
    assert all(models.status(p)["status"] == "ready" for p in ("matting", "triposr-code", "test"))


def test_start_over_api_and_refusal_during_generation(fake_engines, monkeypatch):
    from fastapi.testclient import TestClient

    from formstudio.api import app as app_mod

    monkeypatch.setattr(models, "packs_needed", lambda settings: ["matting"])
    with TestClient(app_mod.app) as c:
        r = c.post("/api/models/start-over")
        assert r.status_code == 202 and r.json()["total"] == 1
        for _ in range(200):
            q = c.get("/api/models").json()["queue"]
            if q["status"] != "running":
                break
            time.sleep(0.02)
        assert q["status"] == "done"
        assert c.delete("/api/models").json()["freed_bytes"] >= 60 * 1024**2
        r = c.post("/api/models/reinstall-libraries").json()
        assert r["command"].endswith("./scripts/setup-mac.sh --reinstall-ai") and r["opened"] is False  # Linux
        monkeypatch.setattr(app_mod, "_generation_running", lambda: True)
        assert c.post("/api/models/start-over").status_code == 409
