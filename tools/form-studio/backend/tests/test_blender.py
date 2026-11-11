"""Blender clay finish, with a stand-in Blender (the real one runs on the Mac; opt in with FORMSTUDIO_BLENDER_REAL=1)."""
import json
import os
import shutil
import sys

import pytest
import trimesh
from fastapi.testclient import TestClient

from formstudio.api.app import app
from formstudio.core import blender, printify
from formstudio.core.style import get_style


@pytest.fixture
def fake_blender(tmp_path, monkeypatch):
    """An executable that answers like Blender: --version, and the clay run (copies in.stl to out.stl)."""
    log = tmp_path / "calls.jsonl"
    exe = tmp_path / "blender"
    exe.write_text(f"""#!{sys.executable}
import json, os, shutil, sys
if "--version" in sys.argv:
    print("Blender 5.3.0\\n\\tbuild date: test"); sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
with open({str(log)!r}, "a") as f:
    f.write(json.dumps({{"argv": sys.argv[1:], "params": json.load(open(args[2]))}}) + "\\n")
if os.environ.get("FAKE_BLENDER_FAIL"):
    print("Error: something broke", file=sys.stderr); sys.exit(1)
shutil.copyfile(args[0], args[1])
""")
    exe.chmod(0o755)
    monkeypatch.setenv("FORMSTUDIO_BLENDER", str(exe))
    blender._found.clear()
    printify.clear_cache()
    yield exe, log
    blender._found.clear()
    printify.clear_cache()


def _params(**geo):
    p = get_style("clay-minimal").print_params()
    p["geometry"].update(finish="blender", **geo)
    return p


def test_finds_blender_and_its_version(fake_blender):
    exe, _ = fake_blender
    assert blender.find_blender(refresh=True) == {"path": str(exe), "version": "5.3.0", "kind": "app"}
    with TestClient(app) as c:
        r = c.get("/api/blender").json()
        assert r["found"] and r["version"] == "5.3.0" and "blender.org" in r["download"]


def test_clay_finish_runs_blender_with_the_settings(fake_blender, mug_glb, tmp_path):
    exe, log = fake_blender
    raw = tmp_path / "04_raw.glb"
    shutil.copyfile(mug_glb, raw)
    rep = printify.run(raw, tmp_path / "out", _params(clay_texture_mm=0.4, clay_grain_mm=5, clay_smooth=12))
    call = json.loads(log.read_text().splitlines()[0])
    assert call["argv"][:4] == ["-b", "--factory-startup", "--python-exit-code", "1"]
    assert call["argv"][5].endswith("blender_clay.py")
    assert call["params"]["texture_mm"] == 0.4 and call["params"]["grain_mm"] == 5 and call["params"]["smooth"] == 12
    assert rep["watertight"] and not any("Blender clay wasn't used" in w for w in rep["warnings"])
    # a base change reuses the finished shape: Blender doesn't run again
    p = _params(clay_texture_mm=0.4, clay_grain_mm=5, clay_smooth=12)
    p["base"]["margin_mm"] = 6
    printify.run(raw, tmp_path / "out2", p)
    assert len(log.read_text().splitlines()) == 1


def test_no_blender_falls_back_to_soft(mug_glb, tmp_path, monkeypatch):
    monkeypatch.setenv("FORMSTUDIO_BLENDER", str(tmp_path / "nope"))
    monkeypatch.setattr(blender, "MAC_PATHS", [])
    monkeypatch.setattr(blender.shutil, "which", lambda name: None)
    blender._found.clear()
    printify.clear_cache()
    rep = printify.run(mug_glb, tmp_path / "o", _params())
    assert rep["watertight"] and any("Blender wasn't found" in w for w in rep["warnings"])
    blender._found.clear()


def test_failing_blender_falls_back_to_soft(fake_blender, mug_glb, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_BLENDER_FAIL", "1")
    rep = printify.run(mug_glb, tmp_path / "o", _params())
    assert rep["watertight"] and any("Blender stopped with an error" in w and "something broke" in w for w in rep["warnings"])


def test_style_keeps_the_blender_finish():
    with TestClient(app) as c:
        s = c.post("/api/styles", json={"name": "Clay test", "geometry": {"height_mm": 40, "soften_mm": 0.9, "min_feature_mm": 1.6,
                                                                          "finish": "blender", "clay_texture_mm": 0.5}}).json()
        assert s["geometry"]["finish"] == "blender" and s["geometry"]["clay_texture_mm"] == 0.5
        assert c.post("/api/styles", json={"name": "Bad", "geometry": {"height_mm": 40, "soften_mm": 0.9, "min_feature_mm": 1.6,
                                                                       "clay_texture_mm": 3}}).status_code == 422
        c.delete(f"/api/styles/{s['id']}")


@pytest.mark.skipif(not os.environ.get("FORMSTUDIO_BLENDER_REAL"), reason="set FORMSTUDIO_BLENDER_REAL=1 and FORMSTUDIO_BLENDER=/path/to/blender")
def test_real_blender_clay(mug_glb, tmp_path):
    blender._found.clear()
    printify.clear_cache()
    soft = printify.run(mug_glb, tmp_path / "soft", {**_params(), "geometry": {**_params()["geometry"], "finish": "soft"}})
    clay = printify.run(mug_glb, tmp_path / "clay", _params(clay_texture_mm=0.3))
    assert clay["watertight"] and not any("wasn't used" in w for w in clay["warnings"])
    assert abs(clay["size_mm"][2] - soft["size_mm"][2]) < 0.3  # same height
    a = trimesh.load(tmp_path / "clay" / "body.stl")
    printify.clear_cache()
    printify.run(mug_glb, tmp_path / "clay2", _params(clay_texture_mm=0.3))
    b = trimesh.load(tmp_path / "clay2" / "body.stl")
    assert len(a.faces) == len(b.faces)  # same input, same result


@pytest.fixture
def fake_bpy_env(tmp_path, monkeypatch):
    """A stand-in for ~/FormStudio/vendor/blender-py: its python 'has' bpy once installed."""
    env = tmp_path / "blender-py"
    (env / "bin").mkdir(parents=True)
    marker, log = env / "installed", tmp_path / "bpy_calls.jsonl"
    py = env / "bin" / "python"
    py.write_text(f"""#!{sys.executable}
import json, os, shutil, sys
args = sys.argv[1:]
with open({str(log)!r}, "a") as f:
    f.write(json.dumps(args) + "\\n")
if args[:2] == ["-m", "pip"]:
    print("Collecting bpy"); print("Successfully installed bpy-5.0.1")
    open({str(marker)!r}, "w").close(); sys.exit(0)
if args[:1] == ["-c"]:
    if not os.path.exists({str(marker)!r}): sys.exit(1)
    print("5.0.1"); sys.exit(0)
rest = args[args.index("--") + 1:]
shutil.copyfile(rest[0], rest[1])
""")
    py.chmod(0o755)
    monkeypatch.setattr(blender, "bpy_env", lambda: env)
    monkeypatch.setattr(blender, "bpy_spec", lambda: "bpy>=5.0,<5.1")
    monkeypatch.setenv("FORMSTUDIO_BLENDER", str(tmp_path / "no-app"))
    monkeypatch.setattr(blender, "MAC_PATHS", [])
    monkeypatch.setattr(blender.shutil, "which", lambda name: None)
    blender._found.clear()
    printify.clear_cache()
    yield env, marker, log
    blender._found.clear()
    printify.clear_cache()


def test_install_blender_inside_form_studio_and_use_it(fake_bpy_env, mug_glb, tmp_path):
    env, marker, log = fake_bpy_env
    assert blender.find_blender(refresh=True) is None  # not installed yet
    msgs = []
    found = blender.install(lambda p, m: msgs.append(m))
    assert found == {"path": str(env / "bin" / "python"), "version": "5.0.1", "kind": "bpy"}
    assert any("Successfully installed" in m for m in msgs)
    rep = printify.run(mug_glb, tmp_path / "o", _params())
    assert rep["watertight"] and not any("wasn't used" in w for w in rep["warnings"])
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    run = next(c for c in calls if "--" in c)
    assert run[0] == "-P" and run[1].endswith("blender_clay.py")  # this script's folder never on the import path
    assert ["-m", "pip", "install", "--progress-bar", "off", "bpy>=5.0,<5.1"] in calls


def test_install_api_and_status(fake_bpy_env):
    import time

    with TestClient(app) as c:
        st = c.get("/api/blender").json()
        assert not st["found"] and st["can_install"]
        assert c.post("/api/blender/install").status_code == 200
        for _ in range(200):
            st = c.get("/api/blender").json()
            if st["task"] and st["task"]["status"] in ("done", "error"):
                break
            time.sleep(0.05)
        assert st["task"]["status"] == "done", st["task"]["error"]
        st = c.post("/api/blender/find").json()
        assert st["found"] and st["kind"] == "bpy" and st["version"] == "5.0.1"
