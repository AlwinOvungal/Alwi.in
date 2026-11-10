"""Cloud engines on fal.ai: fast, pay per use, need an API key (saved in Keychain).

Approximate prices (check fal.ai): FLUX Kontext dev ≈ $0.025 per image,
Hunyuan3D-2 mini ≈ $0.10 per shape.
"""
from __future__ import annotations

import base64
import mimetypes
import time
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from ..core.ingest import remove_background
from .base import EngineContext, Progress, ReconstructInput, StylizeInput

QUEUE = "https://queue.fal.run"
DEFAULT_STYLIZE = "fal-ai/flux-kontext/dev"
DEFAULT_RECONSTRUCT = "fal-ai/hunyuan3d/v2/mini"


def _data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


class FalClient:
    """Minimal fal queue client: submit → poll → fetch result. No extra dependency."""

    def __init__(self, key: str, timeout: float = 600):
        self.key = key
        self.timeout = timeout
        self.http = httpx.Client(headers={"Authorization": f"Key {key}"}, timeout=60)

    def run(self, endpoint: str, args: dict[str, Any], progress: Progress | None = None) -> dict[str, Any]:
        r = self.http.post(f"{QUEUE}/{endpoint}", json=args)
        if r.status_code in (401, 403):
            raise PermissionError("fal.ai rejected the API key")
        r.raise_for_status()
        job = r.json()
        status_url, response_url = job["status_url"], job["response_url"]
        start = time.time()
        while True:
            s = self.http.get(status_url, params={"logs": 1}).json()
            state = s.get("status")
            if state == "COMPLETED":
                break
            if state not in ("IN_QUEUE", "IN_PROGRESS"):
                raise RuntimeError(f"fal.ai job failed: {s}")
            if progress:
                pos = s.get("queue_position")
                progress(None, f"Queued (position {pos})" if state == "IN_QUEUE" and pos is not None else "Running on fal.ai")  # type: ignore[arg-type]
            if time.time() - start > self.timeout:
                raise TimeoutError("fal.ai took too long")
            time.sleep(1.5)
        res = self.http.get(response_url)
        res.raise_for_status()
        return res.json()

    def download(self, url: str, dest: Path) -> None:
        with httpx.stream("GET", url, timeout=120, follow_redirects=True) as r:
            r.raise_for_status()
            with dest.open("wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)

    def check(self) -> bool:
        """Cheap key check: an authenticated status call on a non-existent request."""
        r = self.http.get(f"{QUEUE}/{DEFAULT_RECONSTRUCT}/requests/00000000-0000-0000-0000-000000000000/status")
        return r.status_code not in (401, 403)


def _client(ctx: EngineContext) -> FalClient:
    key = ctx.secrets.get("fal")
    if not key:
        raise PermissionError("Add your fal.ai API key in Settings to use cloud engines.")
    return FalClient(key)


def _scaled(progress: Progress, lo: float, hi: float) -> Progress:
    def f(p, msg):
        progress(lo if p is None else lo + (hi - lo) * p, msg)
    return f


class FalKontext:
    id = "fal-kontext"
    label = "FLUX Kontext (fal.ai cloud)"
    kind = "cloud"
    note = "Paid, ~$0.025 per image. Fast and strong at keeping your object's shape."

    def available(self, ctx: EngineContext) -> tuple[bool, str]:
        return (bool(ctx.secrets.get("fal")), "" if ctx.secrets.get("fal") else "Add a fal.ai API key in Settings.")

    def stylize(self, inp: StylizeInput, ctx: EngineContext, progress: Progress) -> list[dict]:
        client = _client(ctx)
        endpoint = ctx.settings.get("fal", {}).get("stylize_endpoint") or DEFAULT_STYLIZE
        n = len(inp.seeds)
        out = []
        for i, seed in enumerate(inp.seeds):
            res = client.run(endpoint, {
                "prompt": inp.style.edit_prompt_for(inp.subject),
                "image_url": _data_uri(inp.cutout),
                "seed": int(seed),
                "num_images": 1,
                "output_format": "png",
                "resolution_mode": "1:1",
            }, _scaled(progress, i / n, (i + 0.9) / n))
            name = f"candidate_{i + 1}.png"
            dest = inp.out_dir / name
            client.download(res["images"][0]["url"], dest)
            img = Image.open(dest).convert("RGB")
            rgba = remove_background(img)
            canvas = Image.new("RGB", img.size, (255, 255, 255))
            canvas.paste(rgba, (0, 0), rgba)
            canvas.resize((1024, 1024), Image.LANCZOS).save(dest)
            out.append({"file": name, "seed": seed, "endpoint": endpoint})
        progress(1.0, "Done")
        return out


class FalHunyuan:
    id = "fal-hunyuan"
    label = "Hunyuan3D-2 mini (fal.ai cloud)"
    kind = "cloud"
    note = "Paid, ~$0.10 per shape. Good shapes in about 30 seconds."

    def available(self, ctx: EngineContext) -> tuple[bool, str]:
        return (bool(ctx.secrets.get("fal")), "" if ctx.secrets.get("fal") else "Add a fal.ai API key in Settings.")

    def reconstruct(self, inp: ReconstructInput, ctx: EngineContext, progress: Progress) -> dict:
        client = _client(ctx)
        endpoint = ctx.settings.get("fal", {}).get("reconstruct_endpoint") or DEFAULT_RECONSTRUCT
        # send the object on transparent background, which image-to-3D models expect
        img = Image.open(inp.image).convert("RGBA")
        img.putalpha(Image.open(inp.mask).convert("L"))
        tmp = inp.out.with_name("04_upload.png")
        img.save(tmp)
        try:
            res = client.run(endpoint, {
                "input_image_url": _data_uri(tmp),
                "seed": int(inp.seed),
                "num_inference_steps": 50,
                "octree_resolution": 256,
                "textured_mesh": False,
            }, _scaled(progress, 0.05, 0.9))
        finally:
            tmp.unlink(missing_ok=True)
        client.download(res["model_mesh"]["url"], inp.out)
        progress(1.0, "Done")
        return {"endpoint": endpoint}
