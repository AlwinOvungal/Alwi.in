"""formstudio command line.

  formstudio new ~/photos/mug.jpg --style clay-minimal --name mug
  formstudio stylize mug -n 4
  formstudio confirm mug 2
  formstudio build mug
  formstudio export mug
  formstudio run sample.jpg --pick 1 --backend stub

Jobs live under ./jobs by default (override with FORMSTUDIO_JOBS).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

import typer

from . import __version__
from .export import export_all
from .ingest import Matte, ingest
from .job import Job, create_job, open_job
from .printify import PrintabilityError, load_mesh, printify
from .reconstruct import get_reconstructor
from .style import load_style
from .stylize import Backend, stylize

app = typer.Typer(help="Photo -> house style -> printable 3D.", no_args_is_help=True,
                  add_completion=False)

BACKENDS = ("stub", "triposr", "hunyuan")


def _jobs_dir() -> Path:
    return Path(os.environ.get("FORMSTUDIO_JOBS", "jobs"))


def _job(name: str) -> Job:
    return open_job(_jobs_dir() / name)


def _style_for(job: Job):
    style_name = job.read()["style"]["name"]
    return load_style(style_name)


def _do_ingest(job: Job, matte: Matte, size: int) -> None:
    if job.cutout.exists() and job.stage_done("ingest"):
        typer.echo("ingest: already done")
        return
    ingest(job.input, job.cutout, size=size, matte=matte)
    job.record("ingest", matte=matte, size=size)
    typer.echo(f"ingest: {job.cutout}")


def _do_stylize(job: Job, n: int | None, backend: Backend, device: str | None = None) -> list[Path]:
    style = _style_for(job)
    job.reset_from("stylize")
    paths = stylize(job.cutout, style, job.candidates, n=n, backend=backend, device=device)
    job.record("stylize", backend=backend, count=len(paths), style_hash=style.content_hash())
    typer.echo(f"stylize: {len(paths)} candidates in {job.candidates} ({backend})")
    return paths


def _do_confirm(job: Job, index: int) -> None:
    src = job.candidate(index)
    if not src.exists():
        raise typer.BadParameter(f"No candidate {index} in {job.candidates}")
    job.reset_from("confirm")
    shutil.copyfile(src, job.confirmed)
    job.record("confirm", candidate=index)
    typer.echo(f"confirm: candidate {index} -> {job.confirmed}")


def _do_build(job: Job, reconstructor: str, device: str | None = None) -> None:
    if not job.stage_done("confirm"):
        raise typer.BadParameter("Confirm a candidate first: formstudio confirm <job> <N>")
    style = _style_for(job)
    job.reset_from("reconstruct")
    recon = get_reconstructor(reconstructor, device=device)
    recon.reconstruct(job.confirmed, job.raw_glb)
    job.record("reconstruct", backend=recon.name)
    typer.echo(f"reconstruct: {recon.name} -> {job.raw_glb}")

    try:
        result = printify(load_mesh(job.raw_glb), style)
    except PrintabilityError as exc:
        job.record("printify", ok=False, error=str(exc))
        raise typer.Exit(code=2) from exc
    job.print_dir.mkdir(parents=True, exist_ok=True)
    result.body.export(job.print_dir / "body.stl")
    result.base.export(job.print_dir / "base.stl")
    job.record("printify", ok=True, report=result.report, warnings=result.warnings)
    typer.echo(f"printify: {result.report}")
    for w in result.warnings:
        typer.secho(f"  warning: {w}", fg=typer.colors.YELLOW)


def _do_export(job: Job, step: bool) -> None:
    if not job.stage_done("printify"):
        raise typer.BadParameter("Build the job first: formstudio build <job>")
    import trimesh

    style = _style_for(job)
    body = trimesh.load(job.print_dir / "body.stl", force="mesh")
    base = trimesh.load(job.print_dir / "base.stl", force="mesh")
    files = export_all(body, base, job.print_dir, style.palette, step=step, name=job.root.name)
    job.record("export", files=sorted(files), step=step)
    for name, path in files.items():
        typer.echo(f"export: {path}")


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)


@app.command()
def new(
    photo: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    style: str = typer.Option("clay-minimal", help="Style folder under styles/."),
    name: Optional[str] = typer.Option(None, help="Job name (default: photo name)."),
    matte: Matte = typer.Option("rembg", help="rembg (BiRefNet) or threshold (plain backgrounds)."),
    size: int = typer.Option(1024, help="Square size of the cutout."),
) -> None:
    """Create a job from a photo and remove the background."""
    spec = load_style(style)
    job_name = name or photo.stem
    job = create_job(_jobs_dir() / job_name, photo, spec)
    typer.echo(f"job: {job.root} (style {spec.name} v{spec.version}, hash {spec.content_hash()[:12]})")
    _do_ingest(job, matte, size)


def stylize_cmd(
    name: str = typer.Argument(..., help="Job name."),
    n: Optional[int] = typer.Option(None, "-n", help="Number of candidates (default from style)."),
    fast: bool = typer.Option(False, "--fast", help="SDXL-Turbo, for 8 GB Macs."),
    backend: str = typer.Option("sdxl", help="sdxl | passthrough (no model, for testing)."),
    device: Optional[str] = typer.Option(None, help="mps | cuda | cpu (auto by default)."),
) -> None:
    """Generate styled candidates."""
    job = _job(name)
    chosen: Backend = "passthrough" if backend == "passthrough" else ("turbo" if fast else "sdxl")
    _do_stylize(job, n, chosen, device)


# Typer names the command "stylize" but the function is stylize_cmd, to avoid
# shadowing the stylize() function imported from .stylize.
app.command("stylize")(stylize_cmd)


@app.command()
def confirm(
    name: str = typer.Argument(...),
    index: int = typer.Argument(..., min=1, help="Candidate number to keep."),
) -> None:
    """Pick the candidate to turn into 3D."""
    _do_confirm(_job(name), index)


@app.command()
def build(
    name: str = typer.Argument(...),
    reconstructor: str = typer.Option("triposr", help=f"{' | '.join(BACKENDS)}"),
    device: Optional[str] = typer.Option(None, help="mps | cuda | cpu (auto by default)."),
) -> None:
    """Reconstruct the 3D mesh and printify it."""
    if reconstructor not in BACKENDS:
        raise typer.BadParameter(f"Choose from {', '.join(BACKENDS)}")
    _do_build(_job(name), reconstructor, device)


@app.command()
def export(
    name: str = typer.Argument(...),
    step: bool = typer.Option(False, "--step", help="Also write a faceted STEP (needs [step])."),
) -> None:
    """Write model.3mf (and optional STEP) into 05_print/."""
    _do_export(_job(name), step)


@app.command()
def run(
    photo: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    style: str = typer.Option("clay-minimal"),
    name: Optional[str] = typer.Option(None),
    pick: Optional[int] = typer.Option(None, min=1, help="Candidate to confirm. Without it, stop after stylize."),
    backend: str = typer.Option("triposr", help="stub (no models) | triposr | hunyuan"),
    fast: bool = typer.Option(False, "--fast", help="SDXL-Turbo for stylize."),
    matte: Matte = typer.Option("rembg"),
    n: Optional[int] = typer.Option(None, "-n"),
    step: bool = typer.Option(False, "--step"),
) -> None:
    """Run every stage. Stops after stylize unless --pick is given."""
    if backend not in BACKENDS:
        raise typer.BadParameter(f"Choose from {', '.join(BACKENDS)}")
    spec = load_style(style)
    job_name = name or photo.stem
    job_root = _jobs_dir() / job_name
    job = create_job(job_root, photo, spec) if not job_root.exists() else open_job(job_root)
    _do_ingest(job, matte, spec.image_size)

    stub = backend == "stub"
    stylize_backend: Backend = "passthrough" if stub else ("turbo" if fast else "sdxl")
    _do_stylize(job, n, stylize_backend)
    if pick is None:
        typer.echo(f"Pick one: formstudio confirm {job_name} <N>, then build and export.")
        return
    _do_confirm(job, pick)
    _do_build(job, "stub" if stub else backend)
    _do_export(job, step)


if __name__ == "__main__":
    app()
