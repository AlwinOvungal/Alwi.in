"""Where the time goes.

Every step records one line in ~/FormStudio/timings.jsonl: the stage, how long it took and the few facts
that explain the number (engine, device, whether the model was already in memory, quality, size, steps).
Nothing else: no keys, no file paths, no object names. Estimates in the app ("about 40 s left") and the
speed test both read from here.
"""
from __future__ import annotations

import contextlib
import json
import logging
import statistics
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Iterator

from .. import paths

log = logging.getLogger(__name__)

# the only facts a row may carry (anything else passed in is dropped)
SAFE_KEYS = {
    "engine", "device", "warm", "quality", "size", "steps", "faces", "n", "finish", "format",
    "outcome", "cached", "kind", "variant",
}
_lock = threading.Lock()
_local = threading.local()  # rows collected by the current thread (see collect())


def log_path() -> Path:
    return paths.home() / "timings.jsonl"


def _clean(meta: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in meta.items():
        if k in SAFE_KEYS and (v is None or isinstance(v, (bool, int, float, str))):
            out[k] = v[:40] if isinstance(v, str) else v
    return out


def record(stage: str, seconds: float, **meta: Any) -> dict[str, Any]:
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "stage": stage, "seconds": round(float(seconds), 3), **_clean(meta)}
    try:
        with _lock:
            f = log_path()
            f.parent.mkdir(parents=True, exist_ok=True)
            with f.open("a") as fh:
                fh.write(json.dumps(row) + "\n")
    except OSError as e:  # timing must never break the real work
        log.warning("couldn't write timings: %s", e)
    for rows in getattr(_local, "stack", []):
        rows.append(row)
    return row


@contextlib.contextmanager
def stage(name: str, **meta: Any) -> Iterator[dict[str, Any]]:
    """Time a block. Add facts you learn inside it to the yielded dict (e.g. info["warm"] = True)."""
    info = dict(meta)
    t0 = time.perf_counter()
    outcome = "ok"
    try:
        yield info
    except BaseException as e:
        outcome = "cancelled" if e.__class__.__name__ == "Cancelled" else "error"
        raise
    finally:
        record(name, time.perf_counter() - t0, **{**info, "outcome": info.get("outcome", outcome)})


@contextlib.contextmanager
def collect() -> Iterator[list[dict[str, Any]]]:
    """Also hand every row recorded in this thread to the caller (the speed test uses this)."""
    rows: list[dict[str, Any]] = []
    stack = getattr(_local, "stack", None)
    if stack is None:
        stack = _local.stack = []
    stack.append(rows)
    try:
        yield rows
    finally:
        stack.remove(rows)


def read(limit: int = 5000) -> list[dict[str, Any]]:
    f = log_path()
    if not f.exists():
        return []
    rows: deque[dict[str, Any]] = deque(maxlen=limit)
    with _lock, f.open() as fh:
        for line in fh:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return list(rows)


def estimate(stage_name: str, per: str | None = None, last: int = 20, **match: Any) -> float | None:
    """Median seconds of the last runs of a stage that match (only successful ones).

    per="n" gives seconds per item (e.g. per option), for stages that make several at once.
    """
    vals = []
    for r in reversed(read()):
        if r.get("stage") != stage_name or r.get("outcome", "ok") != "ok":
            continue
        if any(v is not None and r.get(k) != v for k, v in match.items()):
            continue
        s = float(r["seconds"])
        if per:
            s /= max(1, int(r.get(per) or 1))
        vals.append(s)
        if len(vals) >= last:
            break
    return statistics.median(vals) if vals else None


def p90(values: list[float]) -> float | None:
    if not values:
        return None
    vs = sorted(values)
    k = max(0, min(len(vs) - 1, round(0.9 * (len(vs) - 1))))
    return vs[k]
