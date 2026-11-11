"""Build a mesh from a shape recipe: a short list of soft, chunky primitives.

Claude Sculpt writes the recipe; this module turns it into geometry the same
way every time, so a saved recipe always rebuilds the identical object.

Recipe coordinates (glTF convention, like every engine's output):
  units are millimetres for an object about 100 tall,
  y is up (the object stands on y = 0), x is to the object's right as seen
  in the image, z points toward the viewer.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

import manifold3d as mf
import numpy as np
import trimesh
from pydantic import BaseModel, Field

Kind = Literal["rounded_box", "capsule", "sphere", "cylinder", "cone", "torus"]

MAX_PARTS = 40
MIN_SIZE = 1.0
MAX_SIZE = 250.0


class Part(BaseModel):
    label: str = Field(description="What this part is, e.g. 'body', 'handle', 'opening'")
    kind: Kind
    op: Literal["add", "subtract"] = Field(description="'subtract' carves the part away, e.g. the opening of a cup")
    size: list[float] = Field(description="[width (x), height (y), depth (z)] before rotation")
    position: list[float] = Field(description="[x, y, z] of the part's centre")
    rotation_deg: list[float] = Field(description="[x, y, z] rotation in degrees, applied after sizing")
    roundness: float = Field(description="Edge radius in units; soft clay forms use 15-35% of the smallest size")
    taper: float = Field(description="Cylinder/cone only: top radius / bottom radius (1 = straight, 0 = point)")


class ShapeRecipe(BaseModel):
    subject: str
    notes: str = Field(description="One or two sentences on how the parts make up the object")
    symmetry: Literal["none", "mirror_x"] = Field(description="'mirror_x' mirrors every part across x = 0")
    parts: list[Part]


def _vec(v: list[float], n: int = 3, default: float = 0.0) -> np.ndarray:
    out = np.full(n, default, dtype=float)
    vals = np.asarray(list(v)[:n], dtype=float)
    out[: len(vals)] = vals
    return np.nan_to_num(out, nan=default)


def _sphere_points(r: float) -> np.ndarray:
    return np.asarray(mf.Manifold.sphere(r, 24).to_mesh().vert_properties)[:, :3]


def _rounded_hull(centres: np.ndarray, r: float) -> mf.Manifold:
    """Convex hull of spheres of radius r at the given centres: a rounded solid."""
    pts = (centres[:, None, :] + _sphere_points(r)[None, :, :]).reshape(-1, 3)
    return mf.Manifold.hull_points(pts)


def _ring(radius: float, y: float, n: int = 32) -> np.ndarray:
    if radius <= 1e-6:
        return np.array([[0.0, y, 0.0]])
    a = np.linspace(0, 2 * math.pi, n, endpoint=False)
    return np.stack([radius * np.cos(a), np.full(n, y), radius * np.sin(a)], axis=1)


def build_part(p: Part) -> mf.Manifold:
    w, h, d = np.clip(_vec(p.size, default=10.0), MIN_SIZE, MAX_SIZE)
    small = min(w, h, d)
    r = float(np.clip(p.roundness, 0.0, small * 0.49))

    if p.kind == "sphere":
        m = mf.Manifold.sphere(0.5, 48).scale((w, h, d))
    elif p.kind == "rounded_box":
        if r < 0.05:
            m = mf.Manifold.cube((w, h, d), center=True)
        else:
            hx, hy, hz = w / 2 - r, h / 2 - r, d / 2 - r
            corners = np.array([[sx * hx, sy * hy, sz * hz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)])
            m = _rounded_hull(corners, r)
    elif p.kind == "capsule":
        rad = min(w, d) / 2
        half = max(h / 2 - rad, 0.0)
        m = _rounded_hull(np.array([[0, -half, 0], [0, half, 0]]), rad).scale((w / (2 * rad), 1, d / (2 * rad)))
    elif p.kind in ("cylinder", "cone"):
        taper = float(np.clip(p.taper if p.kind == "cylinder" else min(p.taper, 0.9), 0.0, 3.0))
        if p.kind == "cone" and p.taper >= 0.95:
            taper = 0.05
        r_bot = w / 2
        r_top = r_bot * taper
        rr = min(r, h / 2 * 0.98, max(r_bot, r_top) * 0.98)
        if rr < 0.05:
            m = mf.Manifold.cylinder(h, r_bot, max(r_top, 1e-3), 64, center=True)
            m = m.rotate((-90, 0, 0))  # manifold cylinders run along z; ours run along y
        else:
            centres = np.concatenate([_ring(max(r_bot - rr, 0), -h / 2 + rr), _ring(max(r_top - rr, 0), h / 2 - rr)])
            m = _rounded_hull(centres, rr)
        m = m.scale((1, 1, d / w))
    elif p.kind == "torus":
        # outer diameter w (x) by d (z), tube thickness h (y): a flat ring, hole along y
        tube = max(min(h, w / 2.2) / 2, 0.5)
        major = max(w / 2 - tube, tube * 0.6)
        m = mf.Manifold.revolve(mf.CrossSection.circle(tube, 32).translate((major, 0)), 64)  # axis z
        m = m.rotate((90, 0, 0)).scale((1, 1, d / w))  # axis y, stretched to depth d
    else:  # pragma: no cover - the schema only allows the kinds above
        raise ValueError(f"unknown part kind {p.kind}")

    rx, ry, rz = _vec(p.rotation_deg)
    if rx or ry or rz:
        m = m.rotate((float(rx), float(ry), float(rz)))
    x, y, z = _vec(p.position)
    return m.translate((float(x), float(y), float(z)))


def build(recipe: ShapeRecipe) -> mf.Manifold:
    parts = recipe.parts[:MAX_PARTS]
    adds = [build_part(p) for p in parts if p.op == "add"]
    subs = [build_part(p) for p in parts if p.op == "subtract"]
    if not adds:
        raise ValueError("The recipe has no solid parts.")
    solid = mf.Manifold.batch_boolean(adds, mf.OpType.Add) if len(adds) > 1 else adds[0]
    if subs:
        cut = mf.Manifold.batch_boolean(subs, mf.OpType.Add) if len(subs) > 1 else subs[0]
        solid = solid - cut
    if recipe.symmetry == "mirror_x":
        solid = solid + solid.mirror((1, 0, 0))
    if solid.is_empty() or solid.volume() <= 0:
        raise ValueError("The recipe produced an empty shape.")
    pieces = solid.decompose()
    if len(pieces) > 1:  # keep the main piece; stray bits would fall off a print
        solid = max(pieces, key=lambda m: m.volume())
    return solid


def to_glb(solid: mf.Manifold, out: Path) -> trimesh.Trimesh:
    mesh = solid.to_mesh()
    tm = trimesh.Trimesh(
        vertices=np.asarray(mesh.vert_properties)[:, :3].astype(np.float64),
        faces=np.asarray(mesh.tri_verts).astype(np.int64),
        process=True,
    )
    tm.export(out)
    return tm


def build_to_glb(recipe: ShapeRecipe, out: Path) -> dict:
    tm = to_glb(build(recipe), out)
    return {"parts": len(recipe.parts), "faces": int(len(tm.faces)), "size": [round(float(v), 1) for v in tm.extents]}
