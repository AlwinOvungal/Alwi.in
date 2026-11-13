"""Restyle with OpenAI's GPT Image models (your ChatGPT / OpenAI API key).

One call to POST /v1/images/edits with the photo plus the style's reference
pictures returns all the options. The model redraws the object completely in
the style; Form Studio then puts each result on clean white for the 3D step.
"""
from __future__ import annotations

import base64
import re
import time

import httpx

from .base import ONE_OBJECT, OWN_COLOURS, EngineContext, EngineError, Progress, StylizeInput
from .cloud import _clean_to_white, _data_uri

API = "https://api.openai.com/v1/images/edits"
MODELS = ["gpt-image-2.6-sunburst", "gpt-image-2.6-flare", "gpt-image-2.5-sunburst", "gpt-image-2.5-flare", "gpt-image-2"]
DEFAULT_MODEL = "gpt-image-2.5-sunburst"

LEAD = (
    "Image 1 is the object to convert. "
    "{refs}"
    "Redraw the object from image 1 completely in the target style, as a single object, "
    "on a transparent background. "
    + ONE_OBJECT + OWN_COLOURS
)
REFS = (
    "Images 2 to {last} show the target style: copy their shape language, materials, surface, texture and "
    "level of detail, but never their colours and never the objects in them. "
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
        data = post_images(body, key, ctx, progress)
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


def post_images(body: dict, key: str, ctx: EngineContext, progress: Progress) -> list[dict]:
    """POST an image edit; wait out short rate limits; raise an EngineError people can act on."""
    sleep = ctx.settings.get("_sleep") or time.sleep
    with httpx.Client(timeout=300, transport=ctx.settings.get("_openai_transport")) as http:
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
        raise _failure(r)
    return r.json().get("data") or []


def _error(r: httpx.Response) -> tuple[str, str]:
    """OpenAI's own error code and message."""
    try:
        err = r.json().get("error") or {}
        return str(err.get("code") or err.get("type") or ""), str(err.get("message") or "")
    except ValueError:
        return "", r.text[:200]


QUOTA_CODES = {"insufficient_quota", "billing_hard_limit_reached", "billing_not_active"}
BILLING = ("Open Billing", "https://platform.openai.com/settings/organization/billing")
LIMITS = ("Open Limits", "https://platform.openai.com/settings/organization/limits")
VERIFY = ("Open Organization settings", "https://platform.openai.com/settings/organization/general")


def _never_works(r: httpx.Response) -> bool:
    """429s that waiting can't fix: no allowance at all (the free tier), a request bigger than the limit, or no credit."""
    code, message = _error(r)
    return code in QUOTA_CODES or bool(re.search(r"\bLimit 0\b", message)) or "request too large" in message.lower()


def _retry_after(r: httpx.Response) -> float | None:
    """Seconds to wait before trying again, for a temporary rate limit only."""
    if r.status_code != 429 or _never_works(r):
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


def _failure(r: httpx.Response) -> EngineError:
    code, message = _error(r)
    detail = message
    if r.status_code == 401:
        return EngineError("OpenAI rejected the API key. Check it in Settings → Cloud accounts → OpenAI.", detail)
    if r.status_code == 429 and re.search(r"\bLimit 0\b", message):
        return EngineError(
            "Your OpenAI account isn't allowed to make images yet: OpenAI gives it an image allowance of 0, "
            "which is the free tier. This is fixed on OpenAI's side, not in Form Studio. Until then, use the "
            "free local engine.",
            detail, [BILLING, LIMITS],
            steps=[
                "On platform.openai.com, open Settings → Billing, add a payment method and buy at least $5 of credit. "
                "Free or promotional credit doesn't count.",
                "Open Settings → Limits: it should show Usage tier 1. This can take a little while after paying.",
                "Come back and press Generate again.",
            ],
        )
    if r.status_code == 429 and code in QUOTA_CODES:
        return EngineError(
            "OpenAI says this key's project has no usable credit.", detail, [BILLING, LIMITS],
            steps=[
                "If you just added credit, give it 5–10 minutes.",
                "Check Settings → Limits: the project's monthly budget mustn't be $0.",
                "Check that this API key belongs to the project and organization you paid for.",
            ],
        )
    if r.status_code == 429:
        return EngineError(
            "OpenAI's rate limit for images was hit, even after waiting. New accounts start with a low "
            "images-per-minute limit: make 1 option at a time, or wait a few minutes.", detail, [LIMITS],
        )
    if r.status_code == 403 and "verif" in message.lower():
        return EngineError(
            "OpenAI needs your organization verified before it allows its image models.", detail, [VERIFY],
            steps=["Settings → Organization → General → Verify Organization.",
                   "It can take about 15 minutes to start working after verifying."],
        )
    if r.status_code == 400:
        return EngineError(f"OpenAI couldn't use this request: {message or 'invalid input'}")
    if r.status_code == 403:
        return EngineError("This OpenAI key can't use that image model. Pick another one in Settings → Cloud accounts → OpenAI.", detail)
    if r.status_code == 404:
        return EngineError("OpenAI doesn't offer that image model to this key. Pick another model in Settings → Cloud accounts → OpenAI.", detail)
    return EngineError(f"OpenAI error ({r.status_code}). Try again.", detail)


def _friendly(r: httpx.Response) -> str:
    e = _failure(r)
    return str(e) + (f" OpenAI said: “{e.detail}”" if e.detail else "")
