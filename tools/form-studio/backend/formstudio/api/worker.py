"""One background worker runs the heavy steps one at a time (one GPU), and
publishes progress events that the UI receives over Server-Sent Events."""
from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)


@dataclass
class Task:
    job_id: str
    kind: str
    fn: Callable[[Callable[[float | None, str], None]], Any]
    status: str = "queued"  # queued | running | done | error
    progress: float = 0.0
    message: str = "Waiting in line"
    error: str | None = None
    started: float = field(default_factory=time.time)

    def public(self) -> dict[str, Any]:
        return {"kind": self.kind, "status": self.status, "progress": round(self.progress, 3),
                "message": self.message, "error": self.error}


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
            self.loop.call_soon_threadsafe(_put, q, event)


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
        self.q: queue.Queue[Task] = queue.Queue()
        self.tasks: dict[str, Task] = {}  # latest task per job
        self.thread = threading.Thread(target=self._run, name="formstudio-worker", daemon=True)
        self.thread.start()

    def busy(self, job_id: str) -> bool:
        t = self.tasks.get(job_id)
        return bool(t and t.status in ("queued", "running"))

    def submit(self, job_id: str, kind: str, fn) -> Task:
        if self.busy(job_id):
            raise RuntimeError("This object is already being worked on.")
        t = Task(job_id, kind, fn)
        self.tasks[job_id] = t
        self.q.put(t)
        self._emit(t)
        return t

    def _emit(self, t: Task, **extra: Any) -> None:
        self.bus.publish(t.job_id, {"type": "task", **t.public(), **extra})

    def _run(self) -> None:
        while True:
            t = self.q.get()
            t.status, t.message = "running", "Starting"
            self._emit(t)
            last = [0.0]

            def progress(p: float | None, msg: str, t=t) -> None:
                if p is not None:
                    t.progress = max(0.0, min(1.0, p))
                t.message = msg
                now = time.time()
                if now - last[0] > 0.15 or (p is not None and p >= 1):
                    last[0] = now
                    self._emit(t)

            try:
                t.fn(progress)
                t.status, t.progress, t.message = "done", 1.0, "Done"
            except Exception as e:
                log.error("task %s/%s failed:\n%s", t.job_id, t.kind, traceback.format_exc())
                t.status, t.error, t.message = "error", _friendly(e), "Failed"
            self._emit(t)


def _friendly(e: Exception) -> str:
    msg = str(e) or e.__class__.__name__
    if "out of memory" in msg.lower() or "MPS backend out of memory" in msg:
        return "Ran out of memory. Turn on Fast mode in Settings, close other apps, or use a cloud engine."
    return msg
