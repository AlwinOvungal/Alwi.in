"""Blender, run in the background for the "Blender clay" 3D finish.

Two ways, no clicks in Blender either way:
  * "inside Form Studio": Blender's official Python package (bpy) in its own small environment,
    ~/FormStudio/vendor/blender-py. Kept apart from Form Studio's AI libraries because bpy pins
    numpy (<2 for Python 3.11). Installed by setup-mac.sh or Settings → Printing → Install.
  * the Blender app, if you have it (Applications, PATH, or a path in Settings).
Every run starts from factory settings, so your own add-ons and preferences never change the result.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
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


def bpy_env() -> Path:
    return paths.home() / "vendor" / "blender-py"


def bpy_python() -> Path:
    return bpy_env() / "bin" / "python"


def bpy_spec() -> str | None:
    """The bpy release built for this Python (bpy wheels exist for one Python version each)."""
    return {(3, 11): "bpy>=5.0,<5.1", (3, 13): "bpy>=5.3,<5.4"}.get(sys.version_info[:2])


def _bpy_version(py: Path) -> str | None:
    if not (py.exists() and os.access(py, os.X_OK)):
        return None
    try:
        out = subprocess.run([str(py), "-c", "import bpy; print(bpy.app.version_string)"],
                             capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = [line for line in out.stdout.splitlines() if line.strip()]
    return lines[-1].strip().split()[0] if out.returncode == 0 and lines else None


def find_blender(refresh: bool = False) -> dict[str, str] | None:
    """{"path", "version", "kind"} of the Blender to use, or None. Checked once, then remembered.

    kind "bpy": Blender inside Form Studio (run the clay script with that Python);
    kind "app": a Blender program (run it with -b -P).
    """
    if _found and not refresh:
        return _found.get("result")
    version = _bpy_version(bpy_python())
    if version:
        _found["result"] = {"path": str(bpy_python()), "version": version, "kind": "bpy"}
        return _found["result"]
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
            result = {"path": c, "version": first.replace("Blender", "").strip().split()[0], "kind": "app"}
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
        if found.get("kind") == "bpy":
            # -P: don't put this script's folder on the import path (its other files must not shadow anything)
            cmd = [found["path"], "-P", str(SCRIPT), "--", str(src), str(dst), str(cfg)]
        else:
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


def install(progress) -> dict[str, str]:
    """Put Blender inside Form Studio: a small environment with Blender's Python package (about 230 MB)."""
    spec = bpy_spec()
    if not spec:
        raise BlenderError(f"Blender's Python package isn't made for Python {sys.version_info[0]}.{sys.version_info[1]}; "
                           "install the Blender app from blender.org instead.")
    env = bpy_env()
    if not bpy_python().exists():
        progress(0.05, "Making a separate environment for Blender")
        run = subprocess.run([sys.executable, "-m", "venv", str(env)], capture_output=True, text=True)
        if run.returncode != 0:
            raise BlenderError(f"Couldn't make Blender's environment: {run.stderr.strip()[-300:]}")
    progress(0.1, "Downloading Blender (about 230 MB, a few minutes)")
    proc = subprocess.Popen([str(bpy_python()), "-m", "pip", "install", "--progress-bar", "off", spec],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    tail: list[str] = []
    for line in proc.stdout:  # type: ignore[union-attr]
        line = line.strip()
        if line:
            tail = (tail + [line])[-5:]
            if line.startswith(("Collecting", "Downloading", "Installing", "Successfully")):
                progress(None, line[:120])
    if proc.wait() != 0:
        raise BlenderError("Installing Blender failed: " + " ".join(tail)[-300:])
    progress(0.95, "Checking Blender")
    found = find_blender(refresh=True)
    if not found or found.get("kind") != "bpy":
        raise BlenderError("Blender installed but didn't start. Try again, or install the Blender app from blender.org.")
    return found
