"""Remove leftovers of older Form Studio installs from this Python environment.

An earlier version lived in tools/form-studio/formstudio/ and may have been
installed into the same .venv. Its install (or a stray folder) makes Python
load an empty 'formstudio' package instead of backend/formstudio. This
uninstalls any such install and removes .pth / editable-finder files that
point at the old location. The current backend install is left alone.

Usage: python -I cleanup_old_install.py <tools/form-studio folder>
"""
from __future__ import annotations

import importlib.metadata as md
import json
import site
import subprocess
import sys
import sysconfig
from pathlib import Path
from urllib.parse import unquote, urlparse


def _url_path(url: str) -> Path | None:
    if not url.startswith("file:"):
        return None
    return Path(unquote(urlparse(url).path)).resolve()


def stale_distributions(root: Path) -> list[str]:
    backend = (root / "backend").resolve()
    stale = []
    for dist in md.distributions():
        name = dist.metadata["Name"] or ""
        top = (dist.read_text("top_level.txt") or "").split()
        files = [str(f) for f in (dist.files or [])]
        direct = dist.read_text("direct_url.json")
        source = _url_path(json.loads(direct).get("url", "")) if direct else None
        provides = (
            "formstudio" in top
            or any(f.startswith("formstudio/") or ("formstudio" in f.lower() and f.endswith((".pth", "_finder.py"))) for f in files)
            or (source is not None and (source == root or root in source.parents))
        )
        if provides and source != backend:
            stale.append(name)
    return sorted(set(stale))


def stale_path_files(root: Path) -> list[Path]:
    """.pth files and editable finders that point at the old tools/form-studio/formstudio location."""
    old_pkg = str(root / "formstudio")
    dirs = {Path(p) for p in site.getsitepackages() + [sysconfig.get_paths()["purelib"]]}
    found = []
    for d in dirs:
        if not d.is_dir():
            continue
        for f in list(d.glob("*.pth")) + list(d.glob("__editable__*finder.py")):
            try:
                text = f.read_text(errors="ignore")
            except OSError:
                continue
            lines = {line.strip() for line in text.splitlines()}
            if str(root) in lines or old_pkg + "'" in text or old_pkg + '"' in text:
                found.append(f)
    return found


def main() -> int:
    root = Path(sys.argv[1]).resolve()
    changed = False
    for name in stale_distributions(root):
        print(f"  removing old install: {name}")
        subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "-q", name], check=False)
        changed = True
    for f in stale_path_files(root):
        print(f"  removing stale path file: {f.name}")
        f.unlink(missing_ok=True)
        changed = True
    if not changed:
        print("  no old installs found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
