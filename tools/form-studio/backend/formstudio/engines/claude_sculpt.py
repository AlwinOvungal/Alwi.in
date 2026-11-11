"""Claude Sculpt: Claude looks at the chosen image and writes a shape recipe of
soft primitives; Form Studio builds the mesh from it. Same recipe, same object."""
from __future__ import annotations

import json
from pathlib import Path

from .. import claude
from ..core import primitives
from ..core.style import StyleSpec
from .base import EngineContext, Progress, ReconstructInput

SYSTEM = """You are the sculptor for a series of collectible 3D-printed miniatures.
You rebuild the object in the image as a recipe of soft, chunky primitives that will be 3D printed.

Coordinates: units are millimetres for an object about 100 tall. y is up and the object stands on y = 0.
x is to the object's right as seen in the image; z points toward the viewer. `position` is a part's centre.
Primitive shapes, before rotation:
- rounded_box: size = [width, height, depth]
- capsule: a pill standing along y
- sphere: an ellipsoid of the given size
- cylinder: stands along y; taper = top radius / bottom radius (1 straight)
- cone: like cylinder with a small top (taper near 0)
- torus: a ring lying flat (hole along y); size = [outer width, tube thickness, outer depth].
  For a mug handle on the right side, use rotation_deg [90, 0, 0] so the ring faces the viewer.
rotation_deg is applied after sizing, in degrees around x, then y, then z.

Rules:
- Capture the overall silhouette and proportions first; use 3 to 25 parts. Fewer, bolder parts are better.
- Keep every solid part at least 6 thick; merge small details into larger forms. No text, no thin wires.
- Use op "subtract" for real openings and hollows (e.g. the inside of a cup, the hole of a handle when not using a torus).
- Every added part must overlap another so the result is one connected piece.
- Roundness: 15-35% of a part's smallest size for soft clay forms (0 for spheres and tori).
- Use symmetry "mirror_x" only if the object is left-right symmetric and you place parts on one side (x >= 0)."""


def recipe_path(raw_mesh: Path) -> Path:
    return raw_mesh.with_name("04_recipe.json")


class ClaudeSculpt:
    id = "claude-sculpt"
    label = "Claude Sculpt (Anthropic)"
    kind = "cloud"
    note = "Paid, about $0.02-0.06 per object. Claude rebuilds the object from soft building blocks; very consistent, toy-like."

    def available(self, ctx: EngineContext) -> tuple[bool, str]:
        ok = bool(ctx.secrets.get("anthropic"))
        return ok, "" if ok else "Add an Anthropic API key in Settings."

    def reconstruct(self, inp: ReconstructInput, ctx: EngineContext, progress: Progress) -> dict:
        style: StyleSpec | None = getattr(inp, "style", None)
        progress(None, "Claude is studying the object")
        images = [("The object to sculpt:", inp.image)]
        if style:
            images += [(f"The series' style reference {i + 1}:", p) for i, p in enumerate(style.reference_paths[:2])]
        guide = f"\n\nStyle of this series: {style.sculpt_guide}" if style and style.sculpt_guide else ""
        recipe = claude.ask(
            key=ctx.secrets.get("anthropic"),
            model=(ctx.settings.get("claude") or {}).get("model"),
            system=SYSTEM + guide,
            images=images,
            prompt=f"Sculpt this {getattr(inp, 'subject', '') or 'object'} as a shape recipe.",
            schema=primitives.ShapeRecipe,
            effort=(ctx.settings.get("claude") or {}).get("effort", "medium"),
        )
        progress(0.8, "Building the shape from Claude's recipe")
        recipe_path(inp.out).write_text(json.dumps(recipe.model_dump(), indent=2))
        info = primitives.build_to_glb(recipe, inp.out)
        progress(1.0, "Done")
        return {"recipe": "04_recipe.json", **info}


def rebuild_from_recipe(raw_mesh: Path) -> dict:
    """Free and deterministic: rebuild 04_raw.glb from the saved 04_recipe.json."""
    rp = recipe_path(raw_mesh)
    if not rp.exists():
        raise FileNotFoundError("This object has no Claude recipe yet.")
    recipe = primitives.ShapeRecipe.model_validate_json(rp.read_text())
    return primitives.build_to_glb(recipe, raw_mesh)
