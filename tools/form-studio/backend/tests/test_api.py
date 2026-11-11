import time

import pytest
from fastapi.testclient import TestClient

from formstudio.api.app import app, worker


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def wait(client, job_id, timeout=120):
    start = time.time()
    while time.time() - start < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        t = j["task"]
        if t and t["status"] in ("done", "error"):
            assert t["status"] == "done", t["error"]
            return j
        time.sleep(0.2)
    raise AssertionError("task timed out")


def test_meta(client):
    assert client.get("/api/health").json()["ok"]
    styles = client.get("/api/styles").json()
    assert styles[0]["id"] == "clay-minimal" and styles[0]["has_reference"]
    eng = client.get("/api/engines").json()
    ids = {e["id"]: e for e in eng["stylize"]}
    assert ids["preview"]["available"]
    assert not ids["fal-kontext"]["available"]  # no key yet
    assert client.get("/api/styles/clay-minimal/reference").status_code == 200


def test_settings_roundtrip(client):
    s = client.put("/api/settings", json={"engines": {"stylize": "preview"}, "candidates": 3}).json()
    assert s["engines"]["stylize"] == "preview" and s["candidates"] == 3
    assert client.put("/api/settings", json={"engines": {"stylize": "nope"}}).status_code == 400
    s = client.put("/api/settings", json={"fal_key": "test-key-1234"}).json()
    assert s["fal"]["has_key"] and s["fal"]["key_hint"] == "…1234"
    assert "test-key-1234" not in str(s)
    s = client.put("/api/settings", json={"fal_key": ""}).json()
    assert not s["fal"]["has_key"]


def test_full_flow_offline(client, mug_photo):
    with mug_photo.open("rb") as f:
        r = client.post("/api/jobs", files={"photo": ("coffee_mug.png", f, "image/png")}, data={"subject": "coffee mug"})
    assert r.status_code == 200, r.text
    job = r.json()
    jid = job["id"]
    assert job["stage"] == "cutout" and "01_cutout.png" in job["files"]

    assert client.post(f"/api/jobs/{jid}/stylize", json={"engine": "preview", "n": 3}).status_code == 200
    job = wait(client, jid)
    assert job["stage"] == "stylized" and len(job["candidates"]) == 3

    job = client.post(f"/api/jobs/{jid}/confirm", json={"candidate": 1}).json()
    assert job["stage"] == "confirmed" and job["confirmed"]["index"] == 1

    assert client.post(f"/api/jobs/{jid}/reconstruct", json={"engine": "lathe"}).status_code == 200
    job = wait(client, jid)
    assert job["stage"] == "printified"
    assert job["report"]["watertight"]

    job = client.post(f"/api/jobs/{jid}/printify", json={"geometry": {"height_mm": 40}}).json()
    assert job["report"]["size_mm"][2] == pytest.approx(40, abs=0.2)
    assert job["print_params"]["geometry"]["height_mm"] == 40

    job = client.post(f"/api/jobs/{jid}/export", json={"formats": ["3mf"]}).json()
    assert job["stage"] == "exported"
    r = client.get(f"/api/jobs/{jid}/files/05_print/model.3mf", params={"download": True})
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert "attachment" in r.headers["content-disposition"]

    assert client.get(f"/api/jobs/{jid}/files/../../settings.json").status_code == 404
    assert any(j["id"] == jid for j in client.get("/api/jobs").json())


def test_photo_as_is_and_extrude(client, mug_photo):
    with mug_photo.open("rb") as f:
        jid = client.post("/api/jobs", files={"photo": ("logo.png", f, "image/png")}).json()["id"]
    job = client.post(f"/api/jobs/{jid}/confirm", json={"candidate": None}).json()
    assert job["confirmed"]["engine"] == "photo"
    client.post(f"/api/jobs/{jid}/reconstruct", json={"engine": "extrude"})
    job = wait(client, jid)
    assert job["report"]["watertight"]
    assert client.delete(f"/api/jobs/{jid}").json()["ok"]
    assert client.get(f"/api/jobs/{jid}").status_code == 404


def test_errors_are_reported(client, mug_photo):
    with mug_photo.open("rb") as f:
        jid = client.post("/api/jobs", files={"photo": ("x.png", f, "image/png")}).json()["id"]
    assert client.post(f"/api/jobs/{jid}/reconstruct", json={}).status_code == 400  # nothing confirmed
    client.post(f"/api/jobs/{jid}/stylize", json={"engine": "fal-kontext"})
    start = time.time()
    while time.time() - start < 10:
        t = client.get(f"/api/jobs/{jid}").json()["task"]
        if t["status"] == "error":
            assert "fal.ai" in t["error"]
            break
        time.sleep(0.1)
    else:
        raise AssertionError("expected an error")
    r = client.post("/api/jobs", files={"photo": ("x.gif", b"GIF89a", "image/gif")})
    assert r.status_code == 400


def test_events_stream_first_message(client, mug_photo):
    with mug_photo.open("rb") as f:
        jid = client.post("/api/jobs", files={"photo": ("y.png", f, "image/png")}).json()["id"]
    with client.stream("GET", f"/api/jobs/{jid}/events") as r:
        for line in r.iter_lines():
            if line.startswith("data:"):
                assert '"type": "job"' in line
                break


def test_parallel_edits_do_not_lose_changes(client, mug_photo):
    """The UI can fire a rebuild and an export back to back; neither may overwrite the other."""
    from concurrent.futures import ThreadPoolExecutor

    with mug_photo.open("rb") as f:
        jid = client.post("/api/jobs", files={"photo": ("z.png", f, "image/png")}).json()["id"]
    client.post(f"/api/jobs/{jid}/confirm", json={"candidate": None})
    client.post(f"/api/jobs/{jid}/reconstruct", json={"engine": "lathe"})
    wait(client, jid)
    with ThreadPoolExecutor(2) as ex:
        a = ex.submit(client.post, f"/api/jobs/{jid}/printify", json={"magnet": {"enabled": True}})
        b = ex.submit(client.post, f"/api/jobs/{jid}/export", json={"formats": ["3mf"]})
        assert a.result().status_code == 200 and b.result().status_code == 200
    job = client.get(f"/api/jobs/{jid}").json()
    assert job["print_params"]["magnet"]["enabled"] is True


def test_missing_cutout_is_recreated(client, mug_photo):
    with mug_photo.open("rb") as f:
        job = client.post("/api/jobs", files={"photo": ("k.png", f, "image/png")}).json()
    from formstudio.core.job import Job

    folder = Job.load(job["id"]).folder
    (folder / "01_cutout.png").unlink()
    (folder / "01_cutout_mask.png").unlink()
    client.post(f"/api/jobs/{job['id']}/stylize", json={"engine": "preview", "n": 1})
    job = wait(client, job["id"])
    assert job["stage"] == "stylized" and (folder / "01_cutout.png").exists()

    # without the original photo there's nothing to rebuild from: a clear message
    (folder / "01_cutout.png").unlink()
    next(folder.glob("00_input.*")).unlink()
    client.post(f"/api/jobs/{job['id']}/stylize", json={"engine": "preview", "n": 1})
    start = time.time()
    while time.time() - start < 20:
        t = client.get(f"/api/jobs/{job['id']}").json()["task"]
        if t["status"] == "error":
            assert "photo is missing" in t["error"]
            break
        time.sleep(0.1)
    else:
        raise AssertionError("expected an error")


def test_format_switch_through_the_api(client, mug_photo):
    with mug_photo.open("rb") as f:
        jid = client.post("/api/jobs", files={"photo": ("m.png", f, "image/png")}).json()["id"]
    client.post(f"/api/jobs/{jid}/confirm", json={"candidate": None})
    client.post(f"/api/jobs/{jid}/reconstruct", json={"engine": "lathe"})
    wait(client, jid)
    job = client.post(f"/api/jobs/{jid}/printify", json={"format": "keychain"}).json()
    assert job["print_params"]["format"] == "keychain" and job["report"]["format"] == "keychain"
    assert "05_print/base.stl" not in job["files"]
    job = client.post(f"/api/jobs/{jid}/printify", json={"ring": {"hole_mm": 6}}).json()
    assert job["print_params"]["format"] == "keychain" and job["print_params"]["ring"]["hole_mm"] == 6
    job = client.post(f"/api/jobs/{jid}/reset-print").json()
    assert job["print_params"]["format"] == "keychain" and job["print_params"]["ring"]["hole_mm"] == 4.5
    assert client.post(f"/api/jobs/{jid}/printify", json={"format": "bookend"}).status_code == 400
    job = client.post(f"/api/jobs/{jid}/export", json={"formats": ["3mf"]}).json()
    assert job["stage"] == "exported"
