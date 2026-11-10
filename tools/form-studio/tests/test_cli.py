from typer.testing import CliRunner

from formstudio.cli import app

runner = CliRunner()


def test_stub_run_end_to_end(tmp_path, sample_photo, monkeypatch):
    monkeypatch.setenv("FORMSTUDIO_JOBS", str(tmp_path / "jobs"))
    result = runner.invoke(
        app,
        ["run", str(sample_photo), "--pick", "1", "--backend", "stub", "--matte", "threshold"],
    )
    assert result.exit_code == 0, result.output
    job = tmp_path / "jobs" / "mug"
    assert (job / "02_candidates" / "4.png").exists()
    assert (job / "03_confirmed.png").exists()
    assert (job / "05_print" / "body.stl").exists()
    assert (job / "05_print" / "base.stl").exists()
    assert (job / "05_print" / "model.3mf").exists()


def test_run_without_pick_stops_after_stylize(tmp_path, sample_photo, monkeypatch):
    monkeypatch.setenv("FORMSTUDIO_JOBS", str(tmp_path / "jobs"))
    result = runner.invoke(
        app, ["run", str(sample_photo), "--backend", "stub", "--matte", "threshold"]
    )
    assert result.exit_code == 0, result.output
    assert "Pick one" in result.output
    assert not (tmp_path / "jobs" / "mug" / "03_confirmed.png").exists()


def test_build_before_confirm_is_refused(tmp_path, sample_photo, monkeypatch):
    monkeypatch.setenv("FORMSTUDIO_JOBS", str(tmp_path / "jobs"))
    runner.invoke(app, ["new", str(sample_photo), "--matte", "threshold"])
    result = runner.invoke(app, ["build", "mug", "--reconstructor", "stub"])
    assert result.exit_code != 0
    assert "Confirm a candidate first" in result.output
