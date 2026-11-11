"""Image → 3D with Meshy (meshy.ai). Needs a Meshy API key (Pro plan or higher).

Create a task with the image, poll until it succeeds, download the GLB.
Mesh only (no textures): Form Studio colours the print from the style palette.
Credits: Meshy 7.1 ≈ 20 per object, Meshy 6 Lite ≈ 5 (failed tasks are refunded).
"""
from __future__ import annotations

import time

import httpx

from .base import EngineContext, EngineError, Progress, ReconstructInput

KEYS = ("Open Meshy API keys", "https://www.meshy.ai/settings/api")
PRICING = ("Open Meshy plans", "https://www.meshy.ai/pricing")
from .cloud import _data_uri, _transparent_upload

API = "https://api.meshy.ai/openapi/v1/image-to-3d"
MODELS = {"latest": "Meshy 7.1 (best, ~20 credits)", "meshy-6-lite": "Meshy 6 Lite (~5 credits)"}


class MeshyImageTo3D:
    id = "meshy"
    label = "Meshy (meshy.ai cloud)"
    kind = "cloud"
    note = "Paid, ~5–20 credits per object (≈$0.10–0.40 on the Pro plan). Strong shapes, made for printing."

    def available(self, ctx: EngineContext) -> tuple[bool, str]:
        ok = bool(ctx.secrets.get("meshy"))
        return ok, "" if ok else "Add a Meshy API key in Settings (needs a Meshy Pro plan)."

    def reconstruct(self, inp: ReconstructInput, ctx: EngineContext, progress: Progress) -> dict:
        key = ctx.secrets.get("meshy")
        if not key:
            raise PermissionError("Add your Meshy API key in Settings.")
        model = (ctx.settings.get("meshy") or {}).get("ai_model") or "latest"
        poll = float((ctx.settings.get("meshy") or {}).get("_poll", 3.0))
        transport = ctx.settings.get("_meshy_transport")
        upload = _transparent_upload(inp)
        try:
            body = {
                "image_url": _data_uri(upload),
                "ai_model": model,
                "should_texture": False,
                "should_remesh": True,
                "topology": "triangle",
                "target_polycount": 60000,
                "target_formats": ["glb"],
            }
        finally:
            upload.unlink(missing_ok=True)

        headers = {"Authorization": f"Bearer {key}"}
        with httpx.Client(timeout=60, transport=transport) as api:
            r = api.post(API, json=body, headers=headers)
            _check(r)
            task_id = r.json()["result"]
            progress(0.02, "Meshy accepted the image")
            start = time.time()
            while True:
                r = api.get(f"{API}/{task_id}", headers=headers)
                _check(r)
                task = r.json()
                status = task.get("status")
                if status == "SUCCEEDED":
                    break
                if status in ("FAILED", "CANCELED"):
                    msg = (task.get("task_error") or {}).get("message") or status.lower()
                    raise EngineError(f"Meshy couldn't build this shape: {msg}", detail=str(task.get("task_error") or ""),
                                      steps=["Try another chosen picture, or another 3D engine."])
                pct = float(task.get("progress") or 0) / 100
                progress(min(pct, 0.97), "Waiting in Meshy's queue" if status == "PENDING" else f"Meshy is building the shape ({int(pct * 100)}%)")
                if time.time() - start > 900:
                    raise TimeoutError("Meshy took longer than 15 minutes. Try again later.")
                time.sleep(poll)

        url = (task.get("model_urls") or {}).get("glb")
        if not url:
            raise RuntimeError("Meshy finished but returned no GLB file.")
        with httpx.Client(timeout=180, follow_redirects=True, transport=transport) as files:  # no key on the file host
            with files.stream("GET", url) as resp:
                resp.raise_for_status()
                with inp.out.open("wb") as f:
                    for chunk in resp.iter_bytes():
                        f.write(chunk)
        progress(1.0, "Done")
        return {"task_id": task_id, "model": model, "credits": task.get("consumed_credits")}


def _check(r: httpx.Response) -> None:
    if r.status_code < 400:
        return
    try:
        message = r.json().get("message", "")
    except ValueError:
        message = r.text[:200]
    if r.status_code == 401:
        raise EngineError("Meshy rejected the API key. Check it in Settings → Cloud accounts → Meshy.", message, [KEYS])
    if r.status_code == 402:
        raise EngineError(
            "Meshy says this account can't use the API or is out of credits. API use needs a Pro plan or higher.",
            message, [PRICING, KEYS],
            steps=["Check that your Meshy plan is Pro or higher (the API isn't in the free plan).",
                   "Check you have credits left; each 3D shape uses about 5–20.",
                   "Then press Try again."],
        )
    if r.status_code == 403:
        raise EngineError("Meshy declined this image. Try another picture.", message)
    if r.status_code == 429:
        raise EngineError("Meshy's rate limit was hit. Wait a minute and try again.", message)
    raise EngineError(f"Meshy error ({r.status_code}). Try again.", message)
