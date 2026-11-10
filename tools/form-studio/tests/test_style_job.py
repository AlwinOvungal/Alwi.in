import dataclasses

import pytest

from formstudio.job import create_job, open_job
from formstudio.style import load_style


def test_clay_minimal_loads_with_palette():
    spec = load_style("clay-minimal")
    assert spec.palette["body"] == "#5E6B2E"
    assert spec.palette["base"] == "#F2F0EA"
    assert spec.height_mm == 50
    assert spec.reference_path.exists()


def test_style_hash_is_stable_and_sensitive_to_values(tmp_path, monkeypatch):
    spec = load_style("clay-minimal")
    assert spec.content_hash() == load_style("clay-minimal").content_hash()
    changed = dataclasses.replace(spec, height_mm=60)
    assert changed.content_hash() != spec.content_hash()


def test_missing_style_raises():
    with pytest.raises(FileNotFoundError):
        load_style("does-not-exist")


def test_job_manifest_and_stage_tracking(tmp_path, sample_photo):
    spec = load_style("clay-minimal")
    job = create_job(tmp_path / "mug", sample_photo, spec)
    assert job.input.exists()
    assert not job.stage_done("ingest")
    job.record("ingest", matte="threshold")
    job.record("stylize", count=4)
    job.record("confirm", candidate=2)
    assert job.stage_done("confirm")
    job.reset_from("stylize")
    assert job.stage_done("ingest")
    assert not job.stage_done("stylize")
    assert not job.stage_done("confirm")
    assert open_job(job.root).read()["style"]["name"] == "clay-minimal"


def test_create_job_refuses_to_overwrite(tmp_path, sample_photo):
    spec = load_style("clay-minimal")
    create_job(tmp_path / "mug", sample_photo, spec)
    with pytest.raises(FileExistsError):
        create_job(tmp_path / "mug", sample_photo, spec)
