"""User settings (settings.json) and secrets (macOS Keychain via `keyring`)."""
from __future__ import annotations

import copy
import json
import logging
import os
import threading
from typing import Any

from .. import paths
from ..engines import DEFAULTS, EngineContext, pick_device

log = logging.getLogger(__name__)

KEYRING_SERVICE = "Form Studio"

DEFAULT_SETTINGS: dict[str, Any] = {
    "engines": dict(DEFAULTS),
    "device": "auto",
    "fast_mode": False,
    "candidates": 4,
    "export_formats": ["3mf"],
    "fal": {"stylize_endpoint": "", "reconstruct_endpoint": ""},
    "slicer": "bambu",  # bambu | orca | prusa
}

_lock = threading.Lock()
_memory_secrets: dict[str, str] = {}  # used only if no keychain is available (e.g. CI)


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        elif k in out:
            out[k] = v
    return out


def load() -> dict[str, Any]:
    f = paths.settings_file()
    if f.exists():
        try:
            return _merge(DEFAULT_SETTINGS, json.loads(f.read_text()))
        except json.JSONDecodeError:
            log.warning("settings.json is unreadable; using defaults")
    return copy.deepcopy(DEFAULT_SETTINGS)


def save(update: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        s = _merge(load(), update)
        paths.settings_file().write_text(json.dumps(s, indent=2))
        return s


# ---- secrets -----------------------------------------------------------------

def _keyring():
    try:
        import keyring
        from keyring.backends import fail

        if isinstance(keyring.get_keyring(), fail.Keyring):
            return None
        return keyring
    except Exception:
        return None


def get_secret(name: str) -> str | None:
    env = os.environ.get(f"FORMSTUDIO_{name.upper()}_KEY")
    if env:
        return env
    kr = _keyring()
    if kr:
        try:
            return kr.get_password(KEYRING_SERVICE, name)
        except Exception as e:
            log.warning("keychain read failed: %s", e)
    return _memory_secrets.get(name)


def set_secret(name: str, value: str | None) -> str:
    """Returns where it was stored: 'keychain' or 'memory'."""
    kr = _keyring()
    if kr:
        try:
            if value:
                kr.set_password(KEYRING_SERVICE, name, value)
            else:
                try:
                    kr.delete_password(KEYRING_SERVICE, name)
                except Exception:
                    pass
            return "keychain"
        except Exception as e:
            log.warning("keychain write failed: %s", e)
    if value:
        _memory_secrets[name] = value
    else:
        _memory_secrets.pop(name, None)
    return "memory"


def context() -> EngineContext:
    s = load()
    secrets = {k: v for k in ("fal",) if (v := get_secret(k))}
    return EngineContext(settings=s, device=pick_device(s.get("device", "auto")), secrets=secrets)


def public(s: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(s)
    key = get_secret("fal")
    out["fal"]["has_key"] = bool(key)
    out["fal"]["key_hint"] = f"…{key[-4:]}" if key else ""
    out["keychain"] = _keyring() is not None
    out["resolved_device"] = pick_device(s.get("device", "auto"))
    return out
