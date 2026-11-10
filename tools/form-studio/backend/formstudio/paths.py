"""Where Form Studio keeps things on disk. Override with FORMSTUDIO_HOME."""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
# tools/form-studio/ when running from the repo (editable install)
PROJECT_DIR = PACKAGE_DIR.parent.parent


def home() -> Path:
    return Path(os.environ.get("FORMSTUDIO_HOME", Path.home() / "FormStudio")).expanduser()


def jobs_dir() -> Path:
    d = home() / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def settings_file() -> Path:
    home().mkdir(parents=True, exist_ok=True)
    return home() / "settings.json"


def style_dirs() -> list[Path]:
    """User styles first (they win), then the styles bundled in the repo."""
    return [home() / "styles", PROJECT_DIR / "styles"]


def web_dist() -> Path:
    return PACKAGE_DIR / "web_dist"
