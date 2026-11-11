"""Cloud engines on fal.ai: fast, pay per use, need an API key (saved in Keychain).

Approximate prices (check fal.ai):
  Nano Banana (Gemini Flash Image) edit  ≈ $0.04 per image
  FLUX Kontext dev                       ≈ $0.025 per image
  TRELLIS                                ≈ $0.02 per shape
  Hunyuan3D-2 mini                       ≈ $0.10 per shape
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
from .base import EngineContext, EngineError, Progress, ReconstructInput, StylizeInput

FAL_KEYS = ("Open fal.ai keys", "https://fal.ai/dashboard/keys")
FAL_BILLING = ("Open fal.ai billing", "https://fal.ai/dashboard/billing")

QUEUE = "https://queue.fal.run"
ENDPOINTS = {
    "fal-nano-banana": "fal-ai/nano-banana/edit",
    "fal-kontext": "fal-ai/flux-kontext/dev",
    "fal-trellis": "fal-ai/trellis",
    "fal-hunyuan": "fal-ai/hunyuan3d/v2/mini",
}


def _data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


class FalClient:
    """Minimal fal queue client: submit → poll → fetch result. No extra dependency."""

    def __init__(self, key: str, timeout: float = 600, transport: httpx.BaseTransport | None = None, poll: float = 1.5):
        self.key = key
        self.timeout = timeout
        self.poll = poll
        self.http = httpx.Client(headers={"Authorization": f"Key {key}"}, timeout=60, transport=transport)
        # files come from fal's CDN: don't send the key there
        self.files = httpx.Client(timeout=120, follow_redirects=True, transport=transport)

    def run(self, endpoint: str, args: dict[str, Any], progress: Progress | None = None) -> dict[str, Any]:
        r = self.http.post(f"{QUEUE}/{endpoint}", json=args)
        said = r.text[:300] if r.status_code >= 400 else ""
        if r.status_code in (401, 403):
            raise EngineError("fal.ai rejected the API key. Check it in Settings → Cloud accounts → fal.ai.", said, [FAL_KEYS])
        if r.status_code == 402:
            raise EngineError("Your fal.ai balance is empty.", said, [FAL_BILLING],
                              steps=["Top up your fal.ai balance.", "Then press Try again."])
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
                raise EngineError("fal.ai couldn't finish this job. Try again, or use another engine.", str(s)[:500])
            if progress:
                pos = s.get("queue_position")
                progress(None, f"Queued (position {pos})" if state == "IN_QUEUE" and pos is not None else "Running on fal.ai")  # type: ignore[arg-type]
            if time.time() - start > self.timeout:
                raise TimeoutError("fal.ai took too long")
            time.sleep(self.poll)
        res = self.http.get(response_url)
        if res.status_code >= 400:
            detail = res.json().get("detail") if "json" in res.headers.get("content-type", "") else res.text[:300]
            raise EngineError(f"fal.ai couldn't process this: {detail}", str(detail)[:500])
        return res.json()

    def download(self, url: str, dest: Path) -> None:
        if url.startswith("data:"):
            dest.write_bytes(base64.b64decode(url.split(",", 1)[1]))
            return
        with self.files.stream("GET", url) as r:
            r.raise_for_status()
            with dest.open("wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)

    def check(self) -> bool:
        """Cheap key check: an authenticated status call on a non-existent request."""
        r = self.http.get(f"{QUEUE}/{ENDPOINTS['fal-trellis']}/requests/00000000-0000-0000-0000-000000000000/status")
        return r.status_code not in (401, 403)


def make_client(ctx: EngineContext) -> FalClient:
    key = ctx.secrets.get("fal")
    if not key:
        raise PermissionError("Add your fal.ai API key in Settings to use cloud engines.")
    return FalClient(key, transport=ctx.settings.get("_fal_transport"))


def _endpoint(ctx: EngineContext, engine_id: str) -> str:
    custom = ((ctx.settings.get("fal") or {}).get("endpoints") or {}).get(engine_id)
    return (custom or "").strip() or ENDPOINTS[engine_id]


def _scaled(progress: Progress, lo: float, hi: float) -> Progress:
    def f(p, msg):
        progress(lo if p is None else lo + (hi - lo) * p, msg)
    return f


def _clean_to_white(path: Path) -> None:
    """Generated images: object on pure white, square, 1024 px (what the 3D step expects)."""
    img = Image.open(path).convert("RGB")
    rgba = remove_background(img)
    side = max(img.size)
    canvas = Image.new("RGB", (side, side), (255, 255, 255))
    canvas.paste(rgba, ((side - img.width) // 2, (side - img.height) // 2), rgba)
    canvas.resize((1024, 1024), Image.LANCZOS).save(path)


def _transparent_upload(inp: ReconstructInput) -> Path:
    """Image-to-3D models expect the object on a transparent background."""
    img = Image.open(inp.image).convert("RGBA")
    img.putalpha(Image.open(inp.mask).convert("L"))
    tmp = inp.out.with_name("04_upload.png")
    img.save(tmp)
    return tmp


class _FalEngine:
    kind = "cloud"
    parallel = 4  # one picture per request: run options side by side

    def available(self, ctx: EngineContext) -> tuple[bool, str]:
        ok = bool(ctx.secrets.get("fal"))
        return ok, "" if ok else "Add a fal.ai API key in Settings."


# ---------------------------------------------------------------- restyle ----

class FalNanoBanana(_FalEngine):
    id = "fal-nano-banana"
    label = "Nano Banana (fal.ai cloud)"
    note = "Paid, ~$0.04 per image. Sees your photo and all the style's references, so the look stays consistent."

    def stylize(self, inp: StylizeInput, ctx: EngineContext, progress: Progress) -> list[dict]:
        client = make_client(ctx)
        endpoint = _endpoint(ctx, self.id)
        refs = inp.style.reference_paths[:3]
        images = [_data_uri(inp.cutout)] + [_data_uri(p) for p in refs]
        lead = (
            "The first image is the object. "
            + (f"The other {len(refs)} image(s) show the target style; match their look, materials and colours, "
               "but do not copy their objects. " if refs else "")
        )
        prompt = lead + inp.style.edit_prompt_for(inp.subject)
        n = len(inp.seeds)
        out = []
        for i, seed in enumerate(inp.seeds):
            res = client.run(endpoint, {
                "prompt": prompt,
                "image_urls": images,
                "seed": int(seed),
                "num_images": 1,
                "output_format": "png",
                "aspect_ratio": "1:1",
            }, _scaled(progress, i / n, (i + 0.9) / n))
            name = f"candidate_{i + 1}.png"
            client.download(res["images"][0]["url"], inp.out_dir / name)
            _clean_to_white(inp.out_dir / name)
            out.append({"file": name, "seed": seed, "endpoint": endpoint})
        progress(1.0, "Done")
        return out


class FalKontext(_FalEngine):
    id = "fal-kontext"
    label = "FLUX Kontext (fal.ai cloud)"
    note = "Paid, ~$0.025 per image. Strong at keeping your object's shape; uses the style's words only."

    def stylize(self, inp: StylizeInput, ctx: EngineContext, progress: Progress) -> list[dict]:
        client = make_client(ctx)
        endpoint = _endpoint(ctx, self.id)
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
            client.download(res["images"][0]["url"], inp.out_dir / name)
            _clean_to_white(inp.out_dir / name)
            out.append({"file": name, "seed": seed, "endpoint": endpoint})
        progress(1.0, "Done")
        return out


# ------------------------------------------------------------- image → 3D ----

class FalTrellis(_FalEngine):
    id = "fal-trellis"
    label = "TRELLIS (fal.ai cloud)"
    note = "Paid, ~$0.02 per shape. Good shapes in under a minute; the cheapest cloud 3D."

    def reconstruct(self, inp: ReconstructInput, ctx: EngineContext, progress: Progress) -> dict:
        client = make_client(ctx)
        endpoint = _endpoint(ctx, self.id)
        tmp = _transparent_upload(inp)
        try:
            res = client.run(endpoint, {"image_url": _data_uri(tmp), "seed": int(inp.seed), "texture_size": 512},
                             _scaled(progress, 0.05, 0.9))
        finally:
            tmp.unlink(missing_ok=True)
        client.download(res["model_mesh"]["url"], inp.out)
        progress(1.0, "Done")
        return {"endpoint": endpoint}


class FalHunyuan(_FalEngine):
    id = "fal-hunyuan"
    label = "Hunyuan3D-2 mini (fal.ai cloud)"
    note = "Paid, ~$0.10 per shape. Detailed shapes in about 30 seconds."

    def reconstruct(self, inp: ReconstructInput, ctx: EngineContext, progress: Progress) -> dict:
        client = make_client(ctx)
        endpoint = _endpoint(ctx, self.id)
        tmp = _transparent_upload(inp)
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
