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


def storage_locations() -> list[dict[str, str]]:
    """Every folder Form Studio uses, for the Storage card in Settings."""
    home_dir = Path.home()
    hf = Path(os.environ.get("HF_HOME") or home_dir / ".cache" / "huggingface").expanduser()
    u2net = Path(os.environ.get("U2NET_HOME") or home_dir / ".u2net").expanduser()
    app_dir = Path(os.environ.get("FORMSTUDIO_APP_DIR") or home_dir / "Applications").expanduser()
    items = [
        ("app", "The app", app_dir / "Form Studio.app", "The app you open from the Dock or Launchpad."),
        ("code", "Form Studio's code", PROJECT_DIR, "Updated with git pull."),
        ("python", "Python and AI libraries", PROJECT_DIR / ".venv", "Made by setup-mac.sh."),
        ("jobs", "Your objects", home() / "jobs", "Photos, options, 3D models and print files."),
        ("styles", "Your styles", home() / "styles", "Styles you made, with their reference pictures."),
        ("hf", "Local AI models", hf, "SDXL, TripoSR and Hunyuan3D. Downloaded the first time a local engine runs."),
        ("rembg", "Background remover model", u2net, "Downloaded the first time a photo is added."),
        ("vendor", "TripoSR code", home() / "vendor", "Installed by setup-mac.sh."),
    ]
    leftovers = home() / "old-version-leftovers"
    if leftovers.exists():
        items.append(("leftovers", "Files from the earlier version", leftovers, "Safe to delete."))
    return [{"id": i, "label": label, "path": str(p), "note": note} for i, label, p, note in items]
