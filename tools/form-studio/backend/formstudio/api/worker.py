"""One background worker runs the heavy steps one at a time (one GPU), and
publishes progress events that the UI receives over Server-Sent Events."""
from __future__ import annotations

import asyncio
import itertools
import logging
import queue
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from ..core import timing
from ..engines.base import Cancelled, EngineError

log = logging.getLogger(__name__)


_task_ids = itertools.count(1)


@dataclass
class Task:
    job_id: str
    kind: str
    fn: Callable[[Callable[[float | None, str], None]], Any]
    priority: int = 0  # lower runs first; background warm-ups use BACKGROUND
    meta: dict[str, Any] = field(default_factory=dict)  # engine, n, quality: for timings and estimates
    eta_s: float | None = None  # expected total seconds, from past runs on this Mac
    status: str = "queued"  # queued | running | done | error | cancelled
    progress: float = 0.0
    message: str = "Waiting in line"
    error: str | None = None
    error_detail: str = ""  # the service's own words, shown folded away
    error_links: list = field(default_factory=list)  # [(label, url)] where to fix it
    error_steps: list = field(default_factory=list)  # what to do, in order
    queued_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    id: int = field(default_factory=lambda: next(_task_ids))  # tells one run from the next

    def public(self) -> dict[str, Any]:
        end = self.finished_at or time.time()
        return {
            "id": self.id,
            "kind": self.kind, "status": self.status, "progress": round(self.progress, 3),
            "message": self.message, "error": self.error,
            "error_detail": self.error_detail,
            "error_links": [{"label": label, "url": url} for label, url in self.error_links],
            "error_steps": list(self.error_steps),
            "elapsed": round(end - self.started_at, 1) if self.started_at else 0.0,
            "eta_s": round(self.eta_s, 1) if self.eta_s else None,
            "cancelling": self.cancel.is_set() and self.status in ("queued", "running"),
        }


BACKGROUND = 9


class Bus:
    """Fan-out of events to SSE subscribers, safe to publish from any thread."""

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.subs: dict[str, set[asyncio.Queue]] = {}
        self.lock = threading.Lock()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def subscribe(self, job_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        with self.lock:
            self.subs.setdefault(job_id, set()).add(q)
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue) -> None:
        with self.lock:
            self.subs.get(job_id, set()).discard(q)

    def publish(self, job_id: str, event: dict[str, Any]) -> None:
        if not self.loop:
            return
        with self.lock:
            targets = list(self.subs.get(job_id, ()))
        for q in targets:
            try:
                self.loop.call_soon_threadsafe(_put, q, event)
            except RuntimeError:  # the event loop has closed (app shutting down)
                return


def _put(q: asyncio.Queue, event: dict) -> None:
    if q.full():
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass
    q.put_nowait(event)


class Worker:
    def __init__(self, bus: Bus) -> None:
        self.bus = bus
        self.q: queue.PriorityQueue[tuple[int, int, Task]] = queue.PriorityQueue()
        self.seq = itertools.count()
        self.tasks: dict[str, Task] = {}  # latest task per job
        self.current: Task | None = None
        self.thread = threading.Thread(target=self._run, name="formstudio-worker", daemon=True)
        self.thread.start()

    def busy(self, job_id: str) -> bool:
        t = self.tasks.get(job_id)
        return bool(t and t.status in ("queued", "running"))

    def submit(self, job_id: str, kind: str, fn, *, priority: int = 0, meta: dict[str, Any] | None = None,
               eta_s: float | None = None, replace: bool = False) -> Task:
        """Queue work for an object. With replace=True a running task for the same object is
        stopped first (its finished results are kept) and this one runs right after it."""
        prev = self.tasks.get(job_id)
        if prev and prev.status in ("queued", "running"):
            if not replace:
                raise RuntimeError("This object is already being worked on.")
            self.cancel(job_id)
        t = Task(job_id, kind, fn, priority=priority, meta=dict(meta or {}), eta_s=eta_s)
        self.tasks[job_id] = t
        self.q.put((priority, next(self.seq), t))
        self._emit(t)
        return t

    def cancel(self, job_id: str) -> bool:
        t = self.tasks.get(job_id)
        if not t or t.status not in ("queued", "running"):
            return False
        t.cancel.set()
        t.message = "Stopping…"
        self._emit(t)
        return True

    def idle(self) -> bool:
        return self.current is None and self.q.empty()

    def _emit(self, t: Task, **extra: Any) -> None:
        self.bus.publish(t.job_id, {"type": "task", **t.public(), **extra})

    def _run(self) -> None:
        while True:
            _, _, t = self.q.get()
            if t.cancel.is_set():  # cancelled while waiting in line
                t.status, t.message, t.finished_at = "cancelled", "Stopped", time.time()
                self._emit(t)
                continue
            self.current = t
            t.status, t.message, t.started_at = "running", "Starting", time.time()
            self._emit(t)
            last = [0.0]

            def progress(p: float | None, msg: str, t=t) -> None:
                if t.cancel.is_set():
                    raise Cancelled()
                if p is not None:
                    t.progress = max(0.0, min(1.0, p))
                t.message = msg
                now = time.time()
                if now - last[0] > 0.15 or (p is not None and p >= 1):
                    last[0] = now
                    self._emit(t)

            outcome = "ok"
            try:
                t.fn(progress)
                t.status, t.progress, t.message = "done", 1.0, "Done"
            except Cancelled:
                outcome = "cancelled"
                t.status, t.message = "cancelled", "Stopped"
            except Exception as e:
                outcome = "error"
                log.error("task %s/%s failed:\n%s", t.job_id, t.kind, traceback.format_exc())
                t.status, t.error, t.message = "error", _friendly(e), "Failed"
                if isinstance(e, EngineError):
                    t.error_detail, t.error_links, t.error_steps = e.detail, list(e.links), list(e.steps)
            t.finished_at = time.time()
            self.current = None
            timing.record(f"task.{t.kind}", t.finished_at - (t.started_at or t.finished_at), outcome=outcome, **t.meta)
            self._emit(t)


# errors we raise on purpose already read well; anything else gets a pointer to where it happened
EXPECTED = (RuntimeError, PermissionError, ValueError, FileNotFoundError, TimeoutError, ConnectionError)


def _where(e: BaseException) -> str:
    """'diffusers/pipelines/…/pipeline_x.py:123' for the innermost frame of the error."""
    tb = traceback.extract_tb(e.__traceback__)
    if not tb:
        return ""
    frame = tb[-1]
    parts = frame.filename.replace("\\", "/").split("/")
    for anchor in ("site-packages", "formstudio"):
        if anchor in parts:
            parts = parts[parts.index(anchor) + (1 if anchor == "site-packages" else 0):]
            break
    else:
        parts = parts[-2:]
    return f"{'/'.join(parts)}:{frame.lineno}"


def _friendly(e: Exception) -> str:
    msg = str(e) or e.__class__.__name__
    if "out of memory" in msg.lower() or "MPS backend out of memory" in msg:
        return "Ran out of memory. Choose Lightest quality in Settings → Local AI, close other apps, or use a cloud engine."
    if "state_dict" in msg and not isinstance(e, EngineError):
        return ("The 3D model's files don't match the installed AI library. Update Form Studio (git pull, then "
                "./scripts/setup-mac.sh), or build From the picture or with a cloud engine. Details are in the log.")
    if isinstance(e, EngineError):
        return msg
    if len(msg) > 400:  # a library's error can be pages long; the full text is in the log
        msg = msg[:400].rstrip() + "…"
    if type(e) in EXPECTED:
        return msg
    where = _where(e)
    detail = f" (in {where})" if where else ""
    return f"{e.__class__.__name__}: {msg}{detail}. The full details are in the log (Settings → Storage)."
