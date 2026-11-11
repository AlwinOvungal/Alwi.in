import os

from fastapi.testclient import TestClient

from formstudio import paths
from formstudio.api.app import app


def test_storage_reports_folders_and_sizes(tmp_path, monkeypatch):
    hf = tmp_path / "hf"
    (hf / "hub" / "models--x").mkdir(parents=True)
    (hf / "hub" / "models--x" / "weights.bin").write_bytes(b"x" * 5000)
    os.symlink(hf, hf / "hub" / "loop")  # a symlink loop must not hang or double count
    monkeypatch.setenv("HF_HOME", str(hf))
    monkeypatch.setenv("U2NET_HOME", str(tmp_path / "u2net-missing"))
    monkeypatch.setenv("FORMSTUDIO_APP_DIR", str(tmp_path / "apps"))
    with TestClient(app) as c:
        data = c.get("/api/storage", params={"refresh": True}).json()
        by_id = {i["id"]: i for i in data["items"]}
        assert by_id["hf"]["exists"] and by_id["hf"]["bytes"] == 5000
        assert not by_id["rembg"]["exists"] and by_id["rembg"]["bytes"] == 0
        assert not by_id["app"]["exists"]
        assert data["total_models_bytes"] == 5000 and data["home"] == str(__import__("pathlib").Path.home())
        assert by_id["jobs"]["path"] == str(paths.home() / "jobs")
        assert "leftovers" not in by_id

        (paths.home() / "old-version-leftovers").mkdir(exist_ok=True)
        try:
            ids = {i["id"] for i in c.get("/api/storage", params={"refresh": True}).json()["items"]}
            assert "leftovers" in ids
        finally:
            (paths.home() / "old-version-leftovers").rmdir()

        assert c.post("/api/storage/open", json={"id": "nope"}).status_code == 400
        assert c.post("/api/storage/open", json={"id": "jobs"}).status_code == 501  # Finder: macOS only
