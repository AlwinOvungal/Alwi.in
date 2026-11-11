"""Restyle with OpenAI's GPT Image models (your ChatGPT / OpenAI API key).

One call to POST /v1/images/edits with the photo plus the style's reference
pictures returns all the options. The model redraws the object completely in
the style; Form Studio then puts each result on clean white for the 3D step.
"""
from __future__ import annotations

import base64
import time

import httpx

from .base import EngineContext, Progress, StylizeInput
from .cloud import _clean_to_white, _data_uri

API = "https://api.openai.com/v1/images/edits"
MODELS = ["gpt-image-2.5-sunburst", "gpt-image-2.5-flare", "gpt-image-2"]
DEFAULT_MODEL = MODELS[0]

LEAD = (
    "Image 1 is the object to convert. "
    "{refs}"
    "Redraw the object from image 1 completely in the target style, as a single object, "
    "on a transparent background. "
)
REFS = (
    "Images 2 to {last} show the target style: copy their look, materials, colours and level of "
    "detail, but never copy the objects in them. "
)


class OpenAIImage:
    id = "openai-image"
    label = "ChatGPT images (OpenAI)"
    kind = "cloud"
    batch = True  # one request returns every option
    note = "Paid, a few cents to ~20¢ per image depending on quality. Redraws your object in the style using its reference pictures."

    def available(self, ctx: EngineContext) -> tuple[bool, str]:
        ok = bool(ctx.secrets.get("openai"))
        return ok, "" if ok else "Add an OpenAI API key in Settings."

    def stylize(self, inp: StylizeInput, ctx: EngineContext, progress: Progress) -> list[dict]:
        key = ctx.secrets.get("openai")
        if not key:
            raise PermissionError("Add your OpenAI API key in Settings.")
        cfg = ctx.settings.get("openai") or {}
        model = cfg.get("model") or DEFAULT_MODEL
        refs = inp.style.reference_paths[:3]
        lead = LEAD.format(refs=REFS.format(last=len(refs) + 1) if refs else "")
        body = {
            "model": model,
            "prompt": lead + inp.style.edit_prompt_for(inp.subject),
            "images": [{"image_url": _data_uri(p)} for p in [inp.cutout, *refs]],
            "n": max(1, min(len(inp.seeds), 10)),
            "size": "1024x1024",
            "quality": cfg.get("quality") or "medium",
            "background": "transparent",
            "output_format": "png",
        }
        progress(None, f"OpenAI is restyling ({body['n']} options)")
        sleep = ctx.settings.get("_sleep") or time.sleep
        with httpx.Client(timeout=240, transport=ctx.settings.get("_openai_transport")) as http:
            for attempt in range(4):
                r = http.post(API, json=body, headers={"Authorization": f"Bearer {key}"})
                wait = _retry_after(r)
                if wait is None or attempt == 3:
                    break
                # a short per-minute limit (new accounts have low ones): wait it out instead of failing
                for left in range(int(wait), 0, -1):
                    progress(None, f"OpenAI asked to slow down; trying again in {left} s")
                    sleep(1)
        if r.status_code >= 400:
            raise RuntimeError(_friendly(r))
        data = r.json().get("data") or []
        if not data:
            raise RuntimeError("OpenAI returned no images. Try again.")
        out = []
        for i, item in enumerate(data):
            name = f"candidate_{i + 1}.png"
            (inp.out_dir / name).write_bytes(base64.b64decode(item["b64_json"]))
            _clean_to_white(inp.out_dir / name)
            out.append({"file": name, "seed": None, "model": model})
            progress((i + 1) / len(data), f"Option {i + 1} of {len(data)} ready")
        return out


def _error(r: httpx.Response) -> tuple[str, str]:
    """OpenAI's own error code and message."""
    try:
        err = r.json().get("error") or {}
        return str(err.get("code") or err.get("type") or ""), str(err.get("message") or "")
    except ValueError:
        return "", r.text[:200]


QUOTA_CODES = {"insufficient_quota", "billing_hard_limit_reached", "billing_not_active"}


def _retry_after(r: httpx.Response) -> float | None:
    """Seconds to wait before trying again, for a temporary rate limit (never for missing credit)."""
    if r.status_code != 429 or _error(r)[0] in QUOTA_CODES:
        return None
    for h in ("retry-after", "x-ratelimit-reset-requests", "x-ratelimit-reset-images"):
        v = r.headers.get(h)
        if v:
            try:
                secs = float(v.rstrip("s")) if not v.endswith("ms") else float(v[:-2]) / 1000
                return max(1.0, min(secs, 30.0))
            except ValueError:
                continue
    return 15.0


def _friendly(r: httpx.Response) -> str:
    code, message = _error(r)
    said = f" OpenAI said: “{message}”" if message else ""
    if r.status_code == 401:
        return "OpenAI rejected the API key. Check it in Settings." + said
    if r.status_code == 429 and code in QUOTA_CODES:
        return (
            "OpenAI says this key's project has no usable credit. If you just added credit, give it 5–10 minutes. "
            "Otherwise check on platform.openai.com: Settings → Limits (the project's monthly budget isn't $0) and "
            "that this key belongs to the project you paid for." + said
        )
    if r.status_code == 429:
        return (
            "OpenAI's rate limit for image generation was hit, even after waiting. New accounts start with a low "
            "images-per-minute limit: generate 1 option at a time, or wait a few minutes." + said
        )
    if r.status_code == 403 and "verif" in message.lower():
        return (
            "OpenAI needs your organization verified before it allows its image models: platform.openai.com → "
            "Settings → Organization → General → Verify Organization. It can take ~15 minutes to start working." + said
        )
    if r.status_code == 400:
        return f"OpenAI couldn't use this request: {message or 'invalid input'}"
    if r.status_code == 403:
        return "This OpenAI key can't use that image model." + said
    if r.status_code == 404:
        return "OpenAI doesn't offer that image model to this key. Pick another model in Settings → Cloud accounts → OpenAI." + said
    return f"OpenAI error ({r.status_code}). Try again.{said}"
