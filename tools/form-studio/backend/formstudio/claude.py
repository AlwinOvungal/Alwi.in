"""Claude in Form Studio: sculpt recipes, style drafts and object names.

Claude can't draw images or meshes, but it describes shapes precisely. Every
call returns structured JSON (validated against a Pydantic model), so the app
always gets something it can build from.
"""
from __future__ import annotations

import base64
import io
import logging
from pathlib import Path
from typing import Any, TypeVar

from PIL import Image
from pydantic import BaseModel

log = logging.getLogger("formstudio.claude")

DEFAULT_MODEL = "claude-opus-5-5"
# approximate $ per million tokens (input, output) for the cost line in the log
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50)}

T = TypeVar("T", bound=BaseModel)


class ClaudeError(RuntimeError):
    """A message that's fine to show in the app."""


def _image_block(path: Path, max_side: int = 1024) -> dict[str, Any]:
    img = Image.open(path).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": base64.standard_b64encode(buf.getvalue()).decode()},
    }


def _client(key: str):
    import anthropic

    return anthropic.Anthropic(api_key=key, max_retries=2, timeout=180)


def ask(
    *,
    key: str | None,
    model: str | None,
    system: str,
    images: list[tuple[str, Path]],
    prompt: str,
    schema: type[T],
    effort: str = "medium",
    max_tokens: int = 16000,
) -> T:
    """One structured call. `images` are (caption, path) pairs shown before the prompt."""
    if not key:
        raise ClaudeError("Add your Anthropic API key in Settings to use Claude.")
    import anthropic

    content: list[dict[str, Any]] = []
    for caption, path in images:
        content.append({"type": "text", "text": caption})
        content.append(_image_block(path))
    content.append({"type": "text", "text": prompt})
    model = model or DEFAULT_MODEL
    try:
        response = _client(key).beta.messages.parse(
            model=model,
            max_tokens=max_tokens,
            system=system,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            # if Claude declines, the API retries on a suitable fallback model
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": content}],
            output_format=schema,
        )
    except anthropic.AuthenticationError:
        raise ClaudeError("Anthropic rejected the API key. Check it in Settings.")
    except anthropic.PermissionDeniedError:
        raise ClaudeError("This Anthropic API key can't use that model.")
    except anthropic.RateLimitError:
        raise ClaudeError("Anthropic's rate limit was hit. Wait a minute and try again.")
    except anthropic.APIStatusError as e:
        raise ClaudeError(f"Anthropic API error ({e.status_code}): {e.message}")
    except anthropic.APIConnectionError:
        raise ClaudeError("Couldn't reach Anthropic. Check your internet connection.")

    if response.stop_reason == "refusal":
        raise ClaudeError("Claude declined this request. Try a different photo.")
    if response.stop_reason == "max_tokens":
        raise ClaudeError("Claude's answer was cut off. Try again.")
    parsed = response.parsed_output
    if parsed is None:
        raise ClaudeError("Claude didn't return a usable answer. Try again.")
    usage = getattr(response, "usage", None)
    if usage is not None:
        pin, pout = PRICES.get(model, (0, 0))
        cost = (usage.input_tokens * pin + usage.output_tokens * pout) / 1e6
        log.info("claude %s (%s): %d in / %d out tokens, ≈$%.3f", schema.__name__, response.model, usage.input_tokens, usage.output_tokens, cost)
    return parsed


# ---- naming --------------------------------------------------------------------

class ObjectName(BaseModel):
    name: str


def name_object(key: str | None, model: str | None, photo: Path) -> str:
    out = ask(
        key=key, model=model, effort="low", max_tokens=4000,
        system="You name objects in photos for a product catalogue.",
        images=[("Photo:", photo)],
        prompt="What is the main object? Answer with a short lowercase noun phrase of 1-4 words, e.g. 'coffee mug', 'desk lamp'. No brand names.",
        schema=ObjectName,
    )
    return out.name.strip().strip(".").lower()[:60] or "object"


# ---- style drafts ----------------------------------------------------------------

class Colour(BaseModel):
    name: str
    hex: str


class StyleDraft(BaseModel):
    name: str
    description: str
    prompt: str
    edit_prompt: str
    negative_prompt: str
    sculpt_guide: str
    body: Colour
    base: Colour
    soften_mm: float
    min_feature_mm: float


STYLE_SYSTEM = """You are an art director defining a house style for a series of 3D-printed miniatures.
From the reference pictures, describe the style so that any everyday object can be redrawn and rebuilt in it consistently.
Be concrete about form (how chunky, how rounded, how much detail), surface (matte, glossy), colour and mood.
The objects are printed in two filament colours: one for the object (body) and one for its base."""

STYLE_PROMPT = """Write the style definition.
- name: 2-3 words.
- description: one sentence.
- prompt: a text-to-image prompt for the restyled object. It must contain the placeholder {{subject}} exactly once, describe the style, and end with "centered, whole object in frame, plain white background, soft diffuse light".
- edit_prompt: an instruction for an image-editing model that receives a photo of the object: "Turn this {{subject}} into ..." (keep {{subject}}), keeping the pose and silhouette, on a plain white background.
- negative_prompt: comma-separated things to avoid.
- sculpt_guide: 2-4 sentences telling a sculptor how to build any object in this style from simple soft primitives (how chunky, roundness, what detail to drop).
- body / base: filament colours as names and #RRGGBB hex taken from the references.
- soften_mm: 0.3 (crisp) to 2.0 (very soft, clay-like).
- min_feature_mm: 1.2 to 3.0 (thinnest part kept).
{notes}"""


def draft_style(key: str | None, model: str | None, references: list[Path], notes: str) -> StyleDraft:
    if not references:
        raise ClaudeError("Add at least one reference image first.")
    draft = ask(
        key=key, model=model, effort="medium",
        system=STYLE_SYSTEM,
        images=[(f"Reference {i + 1}:", p) for i, p in enumerate(references[:4])],
        prompt=STYLE_PROMPT.format(notes=f"Notes from the designer: {notes}" if notes.strip() else ""),
        schema=StyleDraft,
    )
    for field in ("prompt", "edit_prompt"):
        if "{subject}" not in getattr(draft, field):
            setattr(draft, field, "a {subject}, " + getattr(draft, field))
    for c in (draft.body, draft.base):
        h = c.hex.strip().lstrip("#")
        c.hex = "#" + (h.upper() if len(h) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in h) else "808080")
    draft.soften_mm = float(min(max(draft.soften_mm, 0.0), 3.0))
    draft.min_feature_mm = float(min(max(draft.min_feature_mm, 0.8), 5.0))
    return draft
