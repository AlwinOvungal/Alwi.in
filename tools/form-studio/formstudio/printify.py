"""Stage 5: raw AI mesh -> print-ready body + base, forced into the house style.

Geometry lock: whatever the reconstructor produced, this stage remeshes,
smooths, scales, flattens and adds a standard base, so every object in a
series shares the same look and size.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pymeshlab
import trimesh
from scipy import ndimage

from .style import StyleSpec


class PrintabilityError(RuntimeError):
    """A hard failure: the part cannot be printed as designed."""


@dataclass
class PrintResult:
    body: trimesh.Trimesh
    base: trimesh.Trimesh
    report: dict[str, float | int | str | bool] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# ---------- basic mesh ops -------------------------------------------------

def load_mesh(path: Path) -> trimesh.Trimesh:
    mesh = trimesh.load(path, force="mesh")
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
        raise PrintabilityError(f"No triangles found in {path}")
    return mesh


def to_zup(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Reconstructors output y-up. Printers and slicers are z-up."""
    m = mesh.copy()
    m.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0]))
    return m


def keep_largest(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    parts = mesh.split(only_watertight=False)
    return max(parts, key=lambda p: len(p.faces))


def repair(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    m = mesh.copy()
    m.merge_vertices()
    m.update_faces(m.unique_faces())
    m.update_faces(m.nondegenerate_faces())
    trimesh.repair.fill_holes(m)
    m.fix_normals()
    return m


def scale_to_height(mesh: trimesh.Trimesh, height: float) -> trimesh.Trimesh:
    """Uniform scale so the mesh is `height` tall, bottom on z=0, centred in XY."""
    m = mesh.copy()
    m.apply_scale(height / m.extents[2])
    lo, hi = m.bounds
    m.apply_translation([-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]])
    return m


def clay_remesh(mesh: trimesh.Trimesh, pitch: float) -> trimesh.Trimesh:
    """Voxel remesh. Drops detail below `pitch` and always returns a watertight mesh."""
    grid = mesh.voxelized(pitch=pitch).fill()
    m = grid.marching_cubes
    m.fix_normals()
    return m


def taubin(mesh: trimesh.Trimesh, iterations: int) -> trimesh.Trimesh:
    if iterations <= 0:
        return mesh
    return trimesh.smoothing.filter_taubin(mesh.copy(), iterations=iterations)


def decimate(mesh: trimesh.Trimesh, target_faces: int) -> trimesh.Trimesh:
    if len(mesh.faces) <= target_faces:
        return mesh
    ms = pymeshlab.MeshSet()
    ms.add_mesh(pymeshlab.Mesh(vertex_matrix=mesh.vertices, face_matrix=mesh.faces))
    ms.meshing_decimation_quadric_edge_collapse(
        targetfacenum=target_faces,
        preserveboundary=True,
        preservenormal=True,
        preservetopology=True,
        autoclean=True,
    )
    out = ms.current_mesh()
    result = trimesh.Trimesh(out.vertex_matrix(), out.face_matrix(), process=True)
    result.fix_normals()
    return result


def cut_bottom(mesh: trimesh.Trimesh, fraction: float) -> trimesh.Trimesh:
    """Remove the bottom `fraction` of the height and cap it, giving a flat bottom."""
    if fraction <= 0:
        return mesh
    lo, _ = mesh.bounds
    cut_z = lo[2] + fraction * mesh.extents[2]
    sliced = trimesh.intersections.slice_mesh_plane(
        mesh, plane_normal=[0, 0, 1], plane_origin=[0, 0, cut_z], cap=True
    )
    sliced.apply_translation([0, 0, -sliced.bounds[0, 2]])
    return sliced


def footprint_radius(mesh: trimesh.Trimesh, band: float) -> float:
    """Largest distance from the Z axis of any vertex in the bottom `band` mm."""
    z0 = mesh.bounds[0, 2]
    v = mesh.vertices[mesh.vertices[:, 2] <= z0 + band]
    return float(np.max(np.hypot(v[:, 0], v[:, 1]))) if len(v) else 0.0


def rounded_plinth(radius: float, height: float, fillet: float, sections: int = 96) -> trimesh.Trimesh:
    """A cylinder with rounded top and bottom edges, sitting on z=0."""
    f = max(0.0, min(fillet, height / 2 - 1e-3, radius - 1e-3))
    pts: list[tuple[float, float]] = [(0.0, 0.0)]
    steps = 8
    if f > 0:
        # bottom-right fillet: centre (R-f, f), angle -90deg -> 0deg
        for k in range(steps + 1):
            t = -math.pi / 2 + (math.pi / 2) * k / steps
            pts.append((radius - f + f * math.cos(t), f + f * math.sin(t)))
        # top-right fillet: centre (R-f, H-f), angle 0deg -> 90deg
        for k in range(steps + 1):
            t = (math.pi / 2) * k / steps
            pts.append((radius - f + f * math.cos(t), height - f + f * math.sin(t)))
    else:
        pts += [(radius, 0.0), (radius, height)]
    pts.append((0.0, height))
    return trimesh.creation.revolve(np.array(pts, dtype=float), sections=sections)


def parse_magnet(spec: str | None) -> tuple[float, float] | None:
    """'6x2' -> (diameter 6 mm, depth 2 mm)."""
    if not spec:
        return None
    d, h = spec.lower().split("x")
    return float(d), float(h)


def add_magnet_pocket(base: trimesh.Trimesh, diameter: float, depth: float) -> trimesh.Trimesh:
    clearance = 0.2
    pocket = trimesh.creation.cylinder(
        radius=diameter / 2 + clearance / 2, height=depth * 2, sections=48
    )  # centred on z=0, so it cuts depth mm into the base's bottom face
    out = base.difference(pocket, engine="manifold")
    if not out.is_watertight:
        raise PrintabilityError("Magnet pocket made the base non-watertight")
    return out


# ---------- checks ---------------------------------------------------------

def wall_thickness_ok(mesh: trimesh.Trimesh, min_wall: float, max_thin_fraction: float = 0.01) -> float:
    """Morphological opening test. Returns the fraction of solid volume thinner
    than `min_wall`. Raises if above `max_thin_fraction`."""
    pitch = min(min_wall / 4, 0.5)
    filled = mesh.voxelized(pitch=pitch).fill().matrix
    k = max(1, math.ceil((min_wall / 2) / pitch))
    kernel = np.ones((2 * k + 1,) * 3, dtype=bool)
    opened = ndimage.binary_opening(filled, structure=kernel)
    thin = filled & ~opened
    fraction = float(thin.sum() / max(filled.sum(), 1))
    if fraction > max_thin_fraction:
        raise PrintabilityError(
            f"Walls thinner than {min_wall} mm in {fraction:.1%} of the solid volume. "
            "Thicken the thin parts or increase min_feature_mm."
        )
    return fraction


def overhang_fraction(mesh: trimesh.Trimesh, max_angle_deg: float = 45.0) -> float:
    """Share of surface area facing down beyond `max_angle_deg` from horizontal.
    The flat bed (bottom) is excluded."""
    z0 = mesh.bounds[0, 2]
    n = mesh.face_normals
    centres = mesh.triangles_center
    threshold = -math.sin(math.radians(max_angle_deg))
    mask = (n[:, 2] < threshold) & (centres[:, 2] > z0 + 0.5)
    total = mesh.area_faces.sum()
    return float(mesh.area_faces[mask].sum() / total) if total > 0 else 0.0


# ---------- pipeline -------------------------------------------------------

def printify(raw: trimesh.Trimesh, spec: StyleSpec) -> PrintResult:
    warnings: list[str] = []
    base_h = spec.base_height_mm
    body_h = spec.height_mm - base_h
    uncut_h = body_h / (1 - spec.cut_fraction)

    m = repair(keep_largest(to_zup(raw)))
    m = scale_to_height(m, uncut_h)
    m = clay_remesh(m, pitch=spec.min_feature_mm / 2)
    m = scale_to_height(m, uncut_h)           # remeshing grows bounds by ~1 voxel
    m = taubin(m, spec.smooth_iters)
    m = decimate(m, spec.target_faces)
    m = scale_to_height(m, uncut_h)           # smoothing shrinks slightly
    if not m.is_watertight:
        m = clay_remesh(m, pitch=spec.min_feature_mm / 2)
        m = scale_to_height(m, uncut_h)
        if not m.is_watertight:
            raise PrintabilityError("Could not make the body watertight")
        warnings.append("Decimation broke watertightness; re-remeshed the body.")

    m = cut_bottom(m, spec.cut_fraction)      # height is now exactly body_h
    body_r = footprint_radius(m, band=min(0.5, 0.02 * body_h))
    plinth_r = body_r + spec.base_margin_mm
    base = rounded_plinth(plinth_r, base_h, spec.base_fillet_mm)
    magnet = parse_magnet(spec.magnet)
    if magnet:
        base = add_magnet_pocket(base, *magnet)
    body = m.copy()
    body.apply_translation([0, 0, base_h])

    if not body.is_watertight or not base.is_watertight:
        raise PrintabilityError("Body or base is not watertight")
    wall_frac = wall_thickness_ok(body, spec.min_wall_mm)
    overhang = overhang_fraction(body)
    if overhang > 0.15:
        warnings.append(
            f"{overhang:.0%} of the surface is steep overhang (>45°); expect supports."
        )

    report: dict[str, float | int | str | bool] = {
        "watertight": True,
        "height_mm": round(float(body.bounds[1, 2]), 3),
        "body_height_mm": round(float(body.extents[2]), 3),
        "base_radius_mm": round(plinth_r, 3),
        "body_faces": int(len(body.faces)),
        "thin_wall_fraction": round(wall_frac, 5),
        "overhang_fraction": round(overhang, 4),
        "magnet": spec.magnet or "none",
    }
    return PrintResult(body=body, base=base, report=report, warnings=warnings)


def printify_to_files(raw_glb: Path, spec: StyleSpec, out_dir: Path) -> PrintResult:
    result = printify(load_mesh(raw_glb), spec)
    out_dir.mkdir(parents=True, exist_ok=True)
    result.body.export(out_dir / "body.stl")
    result.base.export(out_dir / "base.stl")
    return result
