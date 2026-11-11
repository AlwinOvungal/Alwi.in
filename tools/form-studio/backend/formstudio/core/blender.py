"""Blender on your Mac, run in the background for the "Blender clay" 3D finish.

Blender (free, blender.org) isn't bundled: Form Studio looks for the app where macOS puts it,
or uses the path in Settings → Printing. Every run starts Blender with factory settings, so
your own add-ons and preferences never change the result.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import trimesh

from .. import paths

log = logging.getLogger(__name__)

SCRIPT = Path(__file__).with_name("blender_clay.py")
MAC_PATHS = [
    Path("/Applications/Blender.app/Contents/MacOS/Blender"),
    Path.home() / "Applications" / "Blender.app" / "Contents" / "MacOS" / "Blender",
]
_found: dict[str, Any] = {}


class BlenderError(RuntimeError):
    pass


def _configured_path() -> str:
    try:
        return (json.loads(paths.settings_file().read_text()).get("blender_path") or "").strip()
    except (OSError, ValueError):
        return ""


def find_blender(refresh: bool = False) -> dict[str, str] | None:
    """{"path", "version"} of the Blender to use, or None. Checked once, then remembered."""
    if _found and not refresh:
        return _found.get("result")
    candidates = [_configured_path(), os.environ.get("FORMSTUDIO_BLENDER", ""), *map(str, MAC_PATHS),
                  shutil.which("blender") or ""]
    result = None
    for c in candidates:
        if not c or not (Path(c).is_file() and os.access(c, os.X_OK)):
            continue
        try:
            out = subprocess.run([c, "--version"], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.warning("blender at %s didn't answer: %s", c, e)
            continue
        first = next((line.strip() for line in out.stdout.splitlines() if line.strip().startswith("Blender")), "")
        if out.returncode == 0 and first:
            result = {"path": c, "version": first.replace("Blender", "").strip().split()[0]}
            break
    _found["result"] = result
    return result


def clay(mesh: trimesh.Trimesh, params: dict[str, Any], timeout: float = 600) -> trimesh.Trimesh:
    """Run the Blender clay finish on a closed mesh (millimetres). Raises BlenderError if it can't."""
    found = find_blender()
    if not found:
        raise BlenderError("Blender wasn't found")
    with tempfile.TemporaryDirectory(prefix="formstudio-blender-") as d:
        src, dst, cfg = Path(d) / "in.stl", Path(d) / "out.stl", Path(d) / "params.json"
        mesh.export(src)
        cfg.write_text(json.dumps(params))
        cmd = [found["path"], "-b", "--factory-startup", "--python-exit-code", "1", "-P", str(SCRIPT),
               "--", str(src), str(dst), str(cfg)]
        t0 = time.perf_counter()
        try:
            run = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise BlenderError(f"Blender took longer than {int(timeout)} s")
        except OSError as e:
            raise BlenderError(f"Blender couldn't start: {e}")
        if run.returncode != 0 or not dst.exists():
            tail = " ".join((run.stderr or run.stdout or "").strip().splitlines()[-3:])[:300]
            raise BlenderError(f"Blender stopped with an error: {tail or run.returncode}")
        log.info("blender clay finish took %.1f s", time.perf_counter() - t0)
        out = trimesh.load(dst, force="mesh")  # STL is loose triangles: load merges shared corners
    if len(out.faces) == 0:
        raise BlenderError("Blender returned an empty shape")
    return out
