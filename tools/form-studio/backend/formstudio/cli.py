"""Optional command line, same pipeline as the app. Handy for batches:

  formstudio-cli run mug.jpg --subject "coffee mug" --pick 1
  formstudio-cli serve            # the app's server in your browser
  formstudio-cli bench --runs 3   # speed test on your 3 most recent objects' photos
"""
from __future__ import annotations

import argparse
from pathlib import Path

from . import pipeline
from .api import settings as settings_mod
from .core.job import Job


def _progress(p, msg):
    pct = "" if p is None else f"{p * 100:5.1f}% "
    print(f"\r  {pct}{msg:<60}", end="", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="formstudio-cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="photo → print files")
    r.add_argument("photo", type=Path)
    r.add_argument("--subject", default="")
    r.add_argument("--style", default="clay-minimal")
    r.add_argument("--stylize", help="engine id (default from settings)")
    r.add_argument("--reconstruct", help="engine id (default from settings)")
    r.add_argument("--pick", type=int, default=1, help="candidate to use (1-based); 0 = photo as is")
    r.add_argument("-n", type=int, default=4)
    r.add_argument("--formats", default="3mf")
    b = sub.add_parser("bench", help="speed test: time photo → 3MF on this Mac")
    b.add_argument("--photos", type=Path, help="folder of photos (default: your 3 most recent objects)")
    b.add_argument("--runs", type=int, default=3)
    b.add_argument("--options", type=int, default=4)
    b.add_argument("--variants", default="before,best,quick", help="any of before, best, quick")
    b.add_argument("--style", default="clay-minimal")
    b.add_argument("--stylize", help="engine id (default from settings)")
    b.add_argument("--reconstruct", help="engine id (default from settings)")
    b.add_argument("--out", type=Path, help="report folder (default ~/FormStudio/bench/<date>)")
    sub.add_parser("serve", help="run the server and open it in your browser")
    sub.add_parser("list", help="list objects")
    a = ap.parse_args(argv)

    if a.cmd == "serve":
        from .desktop import main as desktop_main

        return desktop_main(["--browser"])
    if a.cmd == "list":
        for j in Job.all():
            print(f"{j.id:32} {j.manifest['stage']:13} {j.folder}")
        return 0

    s = settings_mod.load()
    ctx = settings_mod.context()
    print(f"Device: {ctx.device}")
    if a.cmd == "bench":
        from .core import bench

        photos = bench.photos_in(a.photos) if a.photos else bench.pick_photos(3)
        report = bench.run(ctx, photos, style_id=a.style, stylize_engine=a.stylize or s["engines"]["stylize"],
                           reconstruct_engine=a.reconstruct or s["engines"]["reconstruct"],
                           variants=a.variants.split(","), runs=a.runs, options=a.options, progress=_progress,
                           out_dir=a.out)
        print("\n\n" + bench.to_markdown(report))
        print(f"Saved in {report['folder']}")
        return 0
    job = pipeline.create(a.photo, a.subject or a.photo.stem, a.subject or a.photo.stem, a.style)
    print(f"Object {job.id}: {job.folder}")
    from .core.style import get_style

    if a.pick == 0:
        pipeline.use_photo_as_is(job)
    else:
        seeds = get_style(a.style).seeds[: a.n]
        print("Styling…")
        pipeline.stylize(job, ctx, a.stylize or s["engines"]["stylize"], seeds, _progress)
        print()
        pipeline.confirm(job, a.pick - 1)
    print("Building 3D…")
    pipeline.reconstruct(job, ctx, a.reconstruct or s["engines"]["reconstruct"], _progress)
    print()
    files = pipeline.export(job, a.formats.split(","))
    for w in job.manifest["report"]["warnings"]:
        print(f"  ! {w}")
    print("Wrote:", *[job.path(f) for f in files], sep="\n  ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
