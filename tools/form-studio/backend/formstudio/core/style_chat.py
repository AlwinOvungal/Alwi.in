"""New style by conversation with ChatGPT: describe the look, answer a question or two, see a style
picture, refine, save.

ChatGPT keeps a running *proposal* (the style's words and 3D finish) and asks follow-up questions only
when they help. A style picture illustrates the proposal (OpenAI image generation). Saving creates an
ordinary style from the proposal with the approved picture as its reference. Styles never carry
colours: the proposal has no colour settings and its words describe shape, surface, texture and detail.
"""
from __future__ import annotations

import base64
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from .. import paths
from ..engines.base import EngineContext, EngineError

CHAT_API = "https://api.openai.com/v1/chat/completions"
IMAGE_API = "https://api.openai.com/v1/images/generations"
DEFAULT_CHAT_MODEL = "gpt-5-mini"
FALLBACK_CHAT_MODEL = "gpt-4.1-mini"
FINISHES = ("soft", "voxel", "faceted", "blender")

SYSTEM = """You help someone design a visual STYLE for small 3D-printed objects (figurines, fridge magnets, keychains, ornaments). Any object they make later is redrawn in this style, then turned into a 3D print.

A style defines only the visual treatment: shape language (chunky, rounded, angular, blocky…), surface finish (matte, glossy, satin), texture (smooth, clay grain, wood, knit…) and level of detail. A style NEVER defines colours: every object keeps its own colours, which are chosen later for the printer. Never put colour names or colour codes in any style field, and if the person asks for colours, explain kindly that colours are picked per object in the Print step.

Work conversationally: be brief (under 80 words), warm and concrete. Propose a first draft early, even from a short description, and ask at most two follow-up questions only when the answer would change the style (e.g. how much detail, glossy or matte, how rounded). Keep designs printable at a moderate, print-friendly level of detail: describe the look through main forms and recognisable features; fine or repetitive details (tiny lettering, bolts, dense textures) are simplified into broad shapes; chunky, no hair-thin or separate small parts.

Always answer with one JSON object:
{
  "reply": "your message to the person",
  "questions": ["optional short follow-up question", ...],
  "style": null or {
    "name": "2-3 words",
    "description": "one sentence about the look",
    "shape_language": "...", "surface": "...", "texture": "...", "detail": "...",
    "finish": "soft" | "voxel" | "faceted" | "blender",
    "soften_mm": number 0-2 (0 crisp, 2 very soft),
    "min_feature_mm": number 1.2-3,
    "prompt": "text-to-image prompt with {subject} exactly once, describing the style, ending with: centered, whole object in frame, plain white background, soft diffuse light",
    "edit_prompt": "instruction for an image-editing model: Redraw this {subject} as ... (keep {subject}); keep its silhouette, pose and its own colours; plain white background",
    "negative_prompt": "comma-separated things to avoid",
    "sculpt_guide": "2-3 sentences: how to build any object in this style from simple primitives",
    "image_prompt": "a showcase picture of three small sample objects (for example a cat, a mug and a little tree) made in this style, side by side on a plain white background, soft studio light, all in one neutral light warm-grey tone so the shapes, surface and texture read, no text"
  }
}
Return "style" whenever you have a draft (refine it on every turn); keep it null only if you truly know nothing yet."""

_COLOUR_WORDS = re.compile(
    r"(?:\b(?:in|with|of)\s+(?:an?\s+)?)?\b(?:#[0-9a-f]{3,6}|(?:pastel|bright|dark|light|muted|deep)\s+)?(?:red|orange|yellow|green|blue|purple|pink|"
    r"peach|olive|teal|turquoise|lavender|violet|magenta|brown|beige|cream|terracotta|navy|gold|silver)(?:\s+colou?r)?\b",
    re.I)


def _dir() -> Path:
    d = paths.home() / "style_chats"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(chat_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{12}", chat_id or ""):
        raise FileNotFoundError("No such conversation")
    return _dir() / chat_id


def load(chat_id: str) -> dict[str, Any]:
    f = _path(chat_id) / "chat.json"
    if not f.exists():
        raise FileNotFoundError("No such conversation")
    return json.loads(f.read_text())


def _save(state: dict[str, Any]) -> dict[str, Any]:
    d = _path(state["id"])
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "chat.json.tmp"
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(d / "chat.json")
    return state


def _key(ctx: EngineContext) -> str:
    key = ctx.secrets.get("openai")
    if not key:
        raise PermissionError("Creating a style with ChatGPT needs your OpenAI API key (Settings → Cloud accounts → OpenAI).")
    return key


def _http(ctx: EngineContext) -> httpx.Client:
    return httpx.Client(timeout=300, transport=ctx.settings.get("_openai_transport"))


def _fail(r: httpx.Response) -> EngineError:
    from ..engines.openai_image import _failure

    return _failure(r)


def _clean_style(style: dict[str, Any] | None) -> dict[str, Any] | None:
    """The proposal, tidied: known finish, sane numbers, {subject} in the prompts, no colour words."""
    if not isinstance(style, dict):
        return None
    out = {k: str(style.get(k) or "").strip() for k in (
        "name", "description", "shape_language", "surface", "texture", "detail", "prompt", "edit_prompt",
        "negative_prompt", "sculpt_guide", "image_prompt")}
    if not out["name"]:
        return None
    out["name"] = out["name"][:60]
    out["finish"] = style.get("finish") if style.get("finish") in FINISHES else "soft"
    try:
        out["soften_mm"] = float(min(max(float(style.get("soften_mm", 0.9)), 0.0), 2.0))
    except (TypeError, ValueError):
        out["soften_mm"] = 0.9
    try:
        out["min_feature_mm"] = float(min(max(float(style.get("min_feature_mm", 1.6)), 1.2), 3.0))
    except (TypeError, ValueError):
        out["min_feature_mm"] = 1.6
    for k in ("prompt", "edit_prompt", "description", "shape_language", "surface", "texture", "detail", "sculpt_guide"):
        out[k] = re.sub(r"\s{2,}", " ", _COLOUR_WORDS.sub("", out[k])).replace(" ,", ",").replace(" .", ".").strip(" ,")
    for k, lead in (("prompt", "a {subject}, "), ("edit_prompt", "Redraw this {subject}: ")):
        if "{subject}" not in out[k]:
            out[k] = lead + (out[k] or out["description"])
    if "own colours" not in out["edit_prompt"]:
        out["edit_prompt"] += " Keep the object's own colours."
    if not out["image_prompt"]:
        out["image_prompt"] = (f"Three small sample objects (a cat, a mug and a little tree) in this style: "
                               f"{out['description']} Plain white background, soft studio light, one neutral light "
                               "warm-grey tone, no text.")
    return out


def _ask(ctx: EngineContext, state: dict[str, Any]) -> dict[str, Any]:
    key = _key(ctx)
    msgs = [{"role": "system", "content": SYSTEM}]
    for m in state["messages"]:
        if m["role"] == "user":
            msgs.append({"role": "user", "content": m["text"]})
        elif m.get("raw"):
            msgs.append({"role": "assistant", "content": m["raw"]})
    if state.get("proposal"):
        msgs.append({"role": "system", "content": "The current draft style is: " + json.dumps(state["proposal"])})
    cfg = ctx.settings.get("openai") or {}
    models = [cfg.get("chat_model") or DEFAULT_CHAT_MODEL, FALLBACK_CHAT_MODEL]
    with _http(ctx) as http:
        for i, model in enumerate(models):
            r = http.post(CHAT_API, headers={"Authorization": f"Bearer {key}"},
                          json={"model": model, "messages": msgs, "response_format": {"type": "json_object"}})
            if r.status_code in (400, 404) and i == 0 and "model" in r.text.lower():
                continue  # that chat model isn't on this account: try the fallback
            break
    if r.status_code >= 400:
        raise _fail(r)
    content = r.json()["choices"][0]["message"].get("content") or "{}"
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = {"reply": content.strip(), "style": None}
    return {"raw": content, "reply": str(data.get("reply") or "").strip(),
            "questions": [str(q) for q in (data.get("questions") or []) if str(q).strip()][:2],
            "style": _clean_style(data.get("style"))}


def say(ctx: EngineContext, chat_id: str | None, message: str) -> dict[str, Any]:
    """Send the person's message; ChatGPT answers and updates the draft style."""
    message = " ".join((message or "").split())[:1500]
    if not message:
        raise ValueError("Say what kind of look you want.")
    _key(ctx)
    state = load(chat_id) if chat_id else {"id": uuid.uuid4().hex[:12], "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                            "messages": [], "proposal": None, "images": [], "chosen": None}
    state["messages"].append({"role": "user", "text": message})
    ans = _ask(ctx, state)
    if ans["style"]:
        state["proposal"] = ans["style"]
    state["messages"].append({"role": "assistant", "text": ans["reply"] or "Here's a first idea.",
                              "questions": ans["questions"], "raw": ans["raw"]})
    return _save(state)


def make_image(ctx: EngineContext, chat_id: str) -> dict[str, Any]:
    """A style picture for the current draft (three sample objects in the look, neutral tone)."""
    state = load(chat_id)
    prop = state.get("proposal")
    if not prop:
        raise ValueError("Describe the look first; ChatGPT needs a draft to draw.")
    key = _key(ctx)
    cfg = ctx.settings.get("openai") or {}
    from ..engines.openai_image import DEFAULT_MODEL

    prompt = (prop["image_prompt"] + f" Style: {prop['description']} Shapes: {prop['shape_language']}. "
              f"Surface: {prop['surface']}. Texture: {prop['texture']}. Detail: {prop['detail']}. "
              "Show only the treatment; no text, no labels.")
    with _http(ctx) as http:
        r = http.post(IMAGE_API, headers={"Authorization": f"Bearer {key}"},
                      json={"model": cfg.get("model") or DEFAULT_MODEL, "prompt": prompt[:3800], "n": 1,
                            "size": "1024x1024", "quality": cfg.get("quality") or "medium", "output_format": "png"})
    if r.status_code >= 400:
        raise _fail(r)
    data = r.json().get("data") or []
    if not data or not data[0].get("b64_json"):
        raise RuntimeError("OpenAI returned no picture. Try again.")
    n = len(state["images"]) + 1
    name = f"image_{n}.png"
    (_path(chat_id) / name).write_bytes(base64.b64decode(data[0]["b64_json"]))
    state["images"].append(name)
    state["chosen"] = n - 1
    state["messages"].append({"role": "assistant", "text": "Here's how the style looks.", "image": name})
    return _save(state)


def choose_image(chat_id: str, index: int) -> dict[str, Any]:
    state = load(chat_id)
    if not 0 <= index < len(state["images"]):
        raise IndexError("No such picture")
    state["chosen"] = index
    return _save(state)


def image_path(chat_id: str, name: str) -> Path:
    if not re.fullmatch(r"image_\d+\.png", name):
        raise FileNotFoundError(name)
    p = _path(chat_id) / name
    if not p.exists():
        raise FileNotFoundError(name)
    return p


def save(chat_id: str, name: str | None = None):
    """The approved draft becomes a style (words, finish, the chosen picture as its reference; no colours)."""
    from . import style as style_mod

    state = load(chat_id)
    prop = state.get("proposal")
    if not prop:
        raise ValueError("There's no style to save yet. Describe the look first.")
    words = "; ".join(f"{k.replace('_', ' ')}: {prop[k]}" for k in ("shape_language", "surface", "texture", "detail") if prop.get(k))
    edit = style_mod.StyleEdit(
        name=(name or prop["name"]).strip()[:60] or "My style",
        description=prop["description"] + (f" ({words})" if words else ""),
        prompt=prop["prompt"], edit_prompt=prop["edit_prompt"], negative_prompt=prop["negative_prompt"],
        sculpt_guide=prop["sculpt_guide"] or prop["description"],
        geometry=style_mod.StyleGeometry(height_mm=50, soften_mm=prop["soften_mm"], min_feature_mm=prop["min_feature_mm"],
                                         finish=prop["finish"]),
    )
    style = style_mod.create_style(edit)
    if state["images"]:
        chosen = state["images"][state["chosen"] if state.get("chosen") is not None else -1]
        style = style_mod.add_reference(style.id, (_path(chat_id) / chosen).read_bytes())
    state["saved_style"] = style.id
    _save(state)
    return style
