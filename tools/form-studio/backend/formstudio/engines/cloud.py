"""Cloud engines on fal.ai: fast, pay per use, need an API key (saved in Keychain).

Approximate prices (check fal.ai). 3D engines ask for the shape only (no textures): colours come
from the picture and the AMS mapping, and an untextured model is the cheapest tier.
  Nano Banana (Gemini Flash Image) edit  ≈ $0.04 per image
  FLUX Kontext dev                       ≈ $0.025 per image
  Hunyuan3D 3.1 Rapid / Pro              ≈ $0.23 / $0.38 per shape (+$0.15 with 3 views)
  Tripo H3.1                             ≈ $0.20 per shape (+$0.20 detailed geometry)
  TRELLIS 2                              ≈ $0.25 / $0.30 / $0.35 per shape (512 / 1024 / 1536)
  Meshy 6                                ≈ $0.80 per shape (no Meshy plan needed)
  TRELLIS, Hunyuan3D-2 mini (older)      ≈ $0.02, $0.10 per shape
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
from .base import OWN_COLOURS, ONE_OBJECT, EngineContext, EngineError, Progress, ReconstructInput, StylizeInput

FAL_KEYS = ("Open fal.ai keys", "https://fal.ai/dashboard/keys")
FAL_BILLING = ("Open fal.ai billing", "https://fal.ai/dashboard/billing")

QUEUE = "https://queue.fal.run"
ENDPOINTS = {
    "fal-nano-banana": "fal-ai/nano-banana/edit",
    "fal-kontext": "fal-ai/flux-kontext/dev",
    "fal-trellis": "fal-ai/trellis",
    "fal-hunyuan": "fal-ai/hunyuan3d/v2/mini",
    # used instead when the object has front / side / back views
    "fal-trellis-multi": "fal-ai/trellis/multi",
    "fal-hunyuan-multi": "fal-ai/hunyuan3d/v2/multi-view",
    "fal-hunyuan3": "fal-ai/hunyuan-3d/v3.1/rapid/image-to-3d",
    "fal-hunyuan3-pro": "fal-ai/hunyuan-3d/v3.1/pro/image-to-3d",
    "fal-tripo": "tripo3d/h3.1/image-to-3d",
    "fal-tripo-multi": "tripo3d/h3.1/multiview-to-3d",
    "fal-trellis2": "fal-ai/trellis-2",
    "fal-meshy": "fal-ai/meshy/v6/image-to-3d",
}
FACES = 200_000  # plenty for a print; printify simplifies further


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


def view_uris(inp: ReconstructInput) -> dict[str, str] | None:
    """The front / side / back views as transparent PNG data URIs (None when there are no views)."""
    if not inp.views:
        return None
    import io

    out = {}
    for name, (img, mask) in inp.views.items():
        im = Image.open(img).convert("RGBA")
        im.putalpha(Image.open(mask).convert("L"))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        out[name] = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    return out


def _transparent_upload(inp: ReconstructInput) -> Path:
    """Image-to-3D models expect the object on a transparent background."""
    img = Image.open(inp.image).convert("RGBA")
    img.putalpha(Image.open(inp.mask).convert("L"))
    tmp = inp.out.with_name("04_upload.png")
    img.save(tmp)
    return tmp


def _mesh_url(res: dict[str, Any]) -> str:
    """The untextured model's URL in a fal 3D result (each model names it differently)."""
    urls = res.get("model_urls") or {}
    for v in (urls.get("base_model"), res.get("model_glb"), res.get("model_mesh"), urls.get("glb")):
        url = v.get("url") if isinstance(v, dict) else v
        if isinstance(url, str) and url:
            return url
    raise EngineError("fal.ai finished but sent no 3D model back. Try again, or use another engine.",
                      str(sorted(res))[:300])


def _fal_opt(ctx: EngineContext, name: str, default: Any) -> Any:
    v = (ctx.settings.get("fal") or {}).get(name)
    return default if v in (None, "") else v


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
            + (f"The other {len(refs)} image(s) show the target style; match their shape language, materials, "
               "surface and level of detail, but not their colours and not their objects. " if refs else "")
        )
        prompt = lead + ONE_OBJECT + OWN_COLOURS + inp.style.edit_prompt_for(inp.subject)
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
                "prompt": ONE_OBJECT + OWN_COLOURS + inp.style.edit_prompt_for(inp.subject),
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

class _FalShape(_FalEngine):
    """One picture (or the 3 views) in, one untextured model out."""

    def _run(self, inp: ReconstructInput, ctx: EngineContext, progress: Progress, endpoint: str,
             args: dict[str, Any], views: int = 0) -> dict:
        client = make_client(ctx)
        res = client.run(endpoint, args, _scaled(progress, 0.05, 0.9))
        client.download(_mesh_url(res), inp.out)
        progress(1.0, "Done")
        return {"endpoint": endpoint, **({"views": views} if views else {})}


class FalHunyuan3(_FalShape):
    id = "fal-hunyuan3"
    label = "Hunyuan3D 3.1 (fal.ai cloud)"
    multi_view = True
    note = ("Paid, ~$0.23 per shape (Pro ~$0.38, set in Settings). Reliable, detailed shapes; the best value in "
            "the cloud. With 3 views it uses Pro (+$0.15).")

    def reconstruct(self, inp: ReconstructInput, ctx: EngineContext, progress: Progress) -> dict:
        views = view_uris(inp)
        pro = views is not None or _fal_opt(ctx, "hunyuan3_quality", "rapid") == "pro"
        endpoint = _endpoint(ctx, "fal-hunyuan3-pro" if pro else self.id)
        args: dict[str, Any] = {"generate_type": "Geometry", "face_count": FACES}
        if views:
            args.update(input_image_url=views["front"],
                        **{f"{k}_image_url": views[k] for k in ("back", "left", "right") if k in views})
            return self._run(inp, ctx, progress, endpoint, args, len(views))
        tmp = _transparent_upload(inp)
        try:
            return self._run(inp, ctx, progress, endpoint, {"input_image_url": _data_uri(tmp), **args})
        finally:
            tmp.unlink(missing_ok=True)


class FalTripo(_FalShape):
    id = "fal-tripo"
    label = "Tripo H3.1 (fal.ai cloud)"
    multi_view = True
    note = ("Paid, ~$0.20 per shape (+$0.20 for Detailed, set in Settings). Clean, solid shapes that print well. "
            "Uses all 3 views when you make them.")

    def reconstruct(self, inp: ReconstructInput, ctx: EngineContext, progress: Progress) -> dict:
        views = view_uris(inp)
        args: dict[str, Any] = {"texture": False, "pbr": False, "model_seed": int(inp.seed),
                                "geometry_quality": "detailed" if _fal_opt(ctx, "tripo_detail", "standard") == "detailed"
                                else "standard"}
        if views and "front" in views:
            order = [views[k] for k in ("front", "left", "back", "right") if k in views]
            return self._run(inp, ctx, progress, _endpoint(ctx, "fal-tripo-multi"), {"image_urls": order, **args},
                             len(order))
        tmp = _transparent_upload(inp)
        try:
            return self._run(inp, ctx, progress, _endpoint(ctx, self.id),
                             {"image_url": _data_uri(tmp), "face_limit": FACES, **args})
        finally:
            tmp.unlink(missing_ok=True)


class FalTrellis2(_FalShape):
    id = "fal-trellis2"
    label = "TRELLIS 2 (fal.ai cloud)"
    note = "Paid, ~$0.30 per shape (512: $0.25, 1536: $0.35, set in Settings). Sharp, detailed shapes from one picture."

    def reconstruct(self, inp: ReconstructInput, ctx: EngineContext, progress: Progress) -> dict:
        res = int(_fal_opt(ctx, "trellis2_resolution", 1024))
        res = res if res in (512, 1024, 1536) else 1024
        tmp = _transparent_upload(inp)
        try:
            return self._run(inp, ctx, progress, _endpoint(ctx, self.id), {
                "image_url": _data_uri(tmp), "seed": int(inp.seed), "resolution": res,
                "texture_size": 1024, "decimation_target": FACES // 2})  # vertices, not faces
        finally:
            tmp.unlink(missing_ok=True)


class FalMeshy(_FalShape):
    id = "fal-meshy"
    label = "Meshy 6 (fal.ai cloud)"
    note = "Paid, ~$0.80 per shape, with your fal.ai key: no Meshy plan needed. Smooth, tidy shapes."

    def reconstruct(self, inp: ReconstructInput, ctx: EngineContext, progress: Progress) -> dict:
        tmp = _transparent_upload(inp)
        try:
            return self._run(inp, ctx, progress, _endpoint(ctx, self.id), {
                "image_url": _data_uri(tmp), "should_texture": False, "should_remesh": True,
                "topology": "triangle", "target_polycount": 100_000, "symmetry_mode": "auto"})
        finally:
            tmp.unlink(missing_ok=True)


class FalTrellis(_FalEngine):
    id = "fal-trellis"
    label = "TRELLIS (fal.ai cloud, older)"
    multi_view = True
    legacy = True  # replaced by TRELLIS 2; kept so older objects still rebuild
    note = "Paid, ~$0.02 per shape. The first TRELLIS: cheap but rougher. Uses all 3 views when you make them."

    def reconstruct(self, inp: ReconstructInput, ctx: EngineContext, progress: Progress) -> dict:
        client = make_client(ctx)
        views = view_uris(inp)
        if views:
            endpoint = _endpoint(ctx, "fal-trellis-multi")
            res = client.run(endpoint, {"image_urls": [views[k] for k in ("front", "left", "back") if k in views],
                                        "seed": int(inp.seed), "texture_size": 512}, _scaled(progress, 0.05, 0.9))
            client.download(res["model_mesh"]["url"], inp.out)
            progress(1.0, "Done")
            return {"endpoint": endpoint, "views": len(views)}
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
    label = "Hunyuan3D-2 mini (fal.ai cloud, older)"
    multi_view = True
    legacy = True  # replaced by Hunyuan3D 3.1
    note = "Paid, ~$0.10 per shape. Detailed shapes in about 30 seconds. Uses all 3 views when you make them."

    def reconstruct(self, inp: ReconstructInput, ctx: EngineContext, progress: Progress) -> dict:
        client = make_client(ctx)
        views = view_uris(inp)
        if views and {"front", "left", "back"} <= views.keys():
            endpoint = _endpoint(ctx, "fal-hunyuan-multi")
            res = client.run(endpoint, {
                "front_image_url": views["front"], "left_image_url": views["left"], "back_image_url": views["back"],
                "seed": int(inp.seed), "num_inference_steps": 50, "octree_resolution": 256, "textured_mesh": False,
            }, _scaled(progress, 0.05, 0.9))
            client.download(res["model_mesh"]["url"], inp.out)
            progress(1.0, "Done")
            return {"endpoint": endpoint, "views": 3}
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
