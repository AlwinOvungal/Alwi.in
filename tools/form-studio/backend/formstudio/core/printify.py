"""Step 5: AI mesh → print-ready parts in the house style.

This is the "geometry lock": whatever the AI produced, the same deterministic
steps give every object the same soft, chunky, printable character.

  1. stand it up (glTF y-up → printer z-up) and scale to the target height
  2. voxelize and fill (closes holes and makes it solid)
  3. remove parts thinner than `min_feature_mm` (morphological opening)
  4. blur the volume and re-surface it → soft clay edges, guaranteed watertight
  5. Taubin smoothing and decimation
  6. cut a flat bottom, add a parametric base, optional magnet pocket
"""
from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import manifold3d as mf
import numpy as np
import trimesh
from scipy import ndimage
from skimage import measure

from . import timing

Progress = Callable[[float, str], None]

SINK_MM = 0.4  # body sits this deep in the base so the two parts key together
MAX_VOXELS = 260  # cap on the longest grid side, keeps printify to a few seconds


@dataclass
class PrintResult:
    body: trimesh.Trimesh
    base: trimesh.Trimesh | None
    report: dict[str, Any]


# ---- conversions -------------------------------------------------------------

def to_manifold(m: trimesh.Trimesh) -> mf.Manifold:
    mesh = mf.Mesh(
        vert_properties=np.ascontiguousarray(m.vertices, dtype=np.float32),
        tri_verts=np.ascontiguousarray(m.faces, dtype=np.uint32),
    )
    out = mf.Manifold(mesh)
    if out.status() != mf.Error.NoError:
        raise ValueError(f"mesh is not manifold: {out.status()}")
    return out


def to_trimesh(m: mf.Manifold) -> trimesh.Trimesh:
    # keep manifold's topology as is: merging vertices that merely touch (as trimesh's
    # processing does) would turn a valid solid with a pinch point into a "broken" one
    mesh = m.to_mesh()
    return trimesh.Trimesh(
        vertices=np.asarray(mesh.vert_properties)[:, :3].astype(np.float64),
        faces=np.asarray(mesh.tri_verts).astype(np.int64),
        process=False,
    )


def load_mesh(path: Path) -> trimesh.Trimesh:
    loaded = trimesh.load(path, force="mesh")
    if not isinstance(loaded, trimesh.Trimesh) or len(loaded.faces) == 0:
        raise ValueError("the AI mesh is empty")
    return loaded


# ---- volume ------------------------------------------------------------------

def _ball(radius_vox: int) -> np.ndarray:
    r = max(1, radius_vox)
    g = np.mgrid[-r : r + 1, -r : r + 1, -r : r + 1]
    return (g**2).sum(0) <= r * r + 0.5


def _largest_component(vol: np.ndarray) -> np.ndarray:
    labels, n = ndimage.label(vol)
    if n <= 1:
        return vol
    sizes = ndimage.sum(vol, labels, range(1, n + 1))
    return labels == (int(np.argmax(sizes)) + 1)


def solid_volume(mesh: trimesh.Trimesh, pitch: float) -> tuple[np.ndarray, np.ndarray]:
    """Filled occupancy grid and the world position of voxel (0,0,0)."""
    grid = mesh.voxelized(pitch)
    surface = grid.matrix.astype(bool)
    # close pinholes in the AI surface before filling the inside
    surface = ndimage.binary_closing(np.pad(surface, 2), structure=_ball(1), iterations=1)
    filled = ndimage.binary_fill_holes(surface)
    origin = grid.transform[:3, 3] - 2 * pitch
    return filled, origin


def surface_from_volume(vol: np.ndarray, origin: np.ndarray, pitch: float, soften_vox: float) -> trimesh.Trimesh:
    pad = int(math.ceil(3 * soften_vox)) + 2
    v = np.pad(vol.astype(np.float32), pad)
    if soften_vox > 0:
        v = ndimage.gaussian_filter(v, sigma=soften_vox)
    verts, faces, _, _ = measure.marching_cubes(v, level=0.5)
    verts = verts * pitch + (origin - pad * pitch)
    m = trimesh.Trimesh(vertices=verts, faces=faces, process=True)
    if m.volume < 0:
        m.invert()
    return m


# ---- finishes --------------------------------------------------------------------

def voxel_surface(vol: np.ndarray, origin: np.ndarray, pitch: float, block_mm: float) -> trimesh.Trimesh:
    """Pixel-art look: resample to cubes of `block_mm`, then surface them crisply.

    Each block is split 4× before surfacing so marching cubes gives flat faces with
    only a small bevel on the edges: blocky, watertight and kind to the printer.
    """
    f = max(1, int(round(block_mm / pitch)))
    pad = [(0, (-n) % f) for n in vol.shape]
    v = np.pad(vol.astype(np.float32), pad)
    nx, ny, nz = (n // f for n in v.shape)
    blocks = v.reshape(nx, f, ny, f, nz, f).mean(axis=(1, 3, 5)) >= 0.5
    if not blocks.any():  # object smaller than one block: keep a single block
        blocks = np.zeros((1, 1, 1), bool)
        blocks[0, 0, 0] = True
    blocks = _largest_component(blocks)
    fine = np.repeat(np.repeat(np.repeat(blocks, 4, 0), 4, 1), 4, 2)
    return surface_from_volume(fine, origin, f * pitch / 4, 0.0)


def facet(mesh: trimesh.Trimesh, faces: int, warnings: list[str]) -> trimesh.Trimesh:
    """Low-poly look: a few hundred flat faces. Grows the count if a cut would break the solid."""
    target = max(60, faces)
    for _ in range(6):
        try:
            low = mesh.simplify_quadric_decimation(face_count=target)
            low.merge_vertices()
            # must stay a clean printable solid (no pinched edges or vertices)
            if low.is_watertight and low.is_winding_consistent and len(low.split(only_watertight=False)) == 1:
                to_manifold(low)
                return low
        except Exception:
            pass
        target *= 2
    warnings.append("Couldn't facet this shape cleanly; kept it smooth.")
    return mesh


# ---- base and magnet -------------------------------------------------------------

def make_base(radius: float, thickness: float, fillet: float, segments: int = 128) -> mf.Manifold:
    """A round plinth with a rounded top edge and a 0.4 mm chamfer at the bed (no elephant's foot)."""
    fillet = min(fillet, thickness * 0.6, radius * 0.3)
    chamfer = min(0.4, thickness * 0.2)
    pts = [(0.0, 0.0), (radius - chamfer, 0.0), (radius, chamfer)]
    if fillet > 0:
        cx, cy = radius - fillet, thickness - fillet
        for i in range(9):
            a = (math.pi / 2) * i / 8
            pts.append((cx + fillet * math.cos(a), cy + fillet * math.sin(a)))
    else:
        pts.append((radius, thickness))
    pts.append((0.0, thickness))
    return mf.Manifold.revolve(mf.CrossSection([pts]), circular_segments=segments)


def magnet_cutter(diameter: float, depth: float, clearance: float) -> mf.Manifold:
    d = diameter + 2 * clearance
    h = depth + clearance
    return mf.Manifold.cylinder(h + 0.01, d / 2, d / 2, 64).translate((0, 0, -0.01))


# ---- magnet and keychain formats ---------------------------------------------------

def _largest(m: mf.Manifold) -> mf.Manifold:
    parts = m.decompose()
    return max(parts, key=lambda p: p.volume()) if parts else m


def lay_flat(body: mf.Manifold, thickness: float) -> mf.Manifold:
    """Keep the front of a standing body and lay it on its new flat back.

    Standing, the object's front faces −y. We keep the front `thickness` mm of it
    (all of it if it's thinner), then rotate −90° about X: the cut face goes onto
    the bed (z = 0) and the object's top points to +y.
    """
    bb = body.bounding_box()
    ymin, ymax = bb[1], bb[4]
    depth = ymax - ymin
    kept = min(depth, max(thickness, 2.0))
    if kept < depth - 1e-6:
        body = _largest(body.trim_by_plane((0, -1, 0), -(ymin + kept)))
    body = body.rotate((-90, 0, 0))
    bb = body.bounding_box()
    lo, hi = np.array(bb[:3]), np.array(bb[3:])
    centre = (lo[:2] + hi[:2]) / 2
    return body.translate((-centre[0], -centre[1], -lo[2]))


def add_ring(body: mf.Manifold, ring: dict[str, Any], warnings: list[str]) -> mf.Manifold:
    """A flat key-ring loop on top of a body lying on its back (top = +y)."""
    outer = float(ring.get("outer_radius_mm", 4.5))
    hole_r = float(ring.get("hole_mm", 4.5)) / 2
    if outer - hole_r < 1.5:
        outer = hole_r + 1.5
        warnings.append("The key-ring loop was widened to keep its wall at least 1.5 mm.")
    bb = body.bounding_box()
    thickness = min(float(ring.get("thickness_mm", 3.5)), bb[5] - bb[2])
    # find the top of the shape where the loop will be (a slice through the loop's height),
    # so hollow objects like an open cup still get a solid attachment point
    pts = np.concatenate([np.asarray(p) for p in body.slice(thickness / 2).to_polygons()] or [np.zeros((0, 2))])
    if len(pts) == 0:
        warnings.append("Couldn't find a place for the key-ring loop.")
        return body
    ytop = float(pts[:, 1].max())
    near_top = pts[pts[:, 1] > ytop - 0.6]
    x_top = float(near_top[np.argmin(np.abs(near_top[:, 0] - (bb[0] + bb[3]) / 2)), 0])
    cy = ytop + hole_r + 0.6  # the hole sits just clear of the body
    stem_w = outer * 1.2
    stem_bottom = ytop - 3.0  # the stem reaches 3 mm into the body for a strong joint
    shape = mf.CrossSection.circle(outer, 64) + mf.CrossSection.square((stem_w, cy - stem_bottom)).translate(
        (-stem_w / 2, stem_bottom - cy)
    )
    shape = shape - mf.CrossSection.circle(hole_r, 48)
    loop = shape.extrude(thickness).translate((x_top, cy, 0))
    joined = body + loop
    if len(joined.decompose()) > 1:
        warnings.append("The key-ring loop doesn't touch the object; check it in the slicer.")
        return body
    return joined


# ---- main ----------------------------------------------------------------------

# Recent in-between results, so a slider that only changes the base, magnet, ring or format
# doesn't redo the expensive voxel work. Keyed by the AI mesh file and every setting that step uses.
CACHE: dict[str, Any] = {"enabled": True, "max": 12, "items": OrderedDict(), "hits": 0}


def _get(key):
    if not key:
        return None
    items = CACHE["items"]
    if key in items:
        items.move_to_end(key)
        CACHE["hits"] += 1
        return items[key]
    return None


def _put(key, value) -> None:
    if not key:
        return
    for v in value:  # shared between runs: nobody may change them in place
        if isinstance(v, np.ndarray):
            v.flags.writeable = False
    items = CACHE["items"]
    items[key] = value
    while len(items) > CACHE["max"]:
        items.popitem(last=False)


def clear_cache() -> None:
    CACHE["items"].clear()
    CACHE["hits"] = 0


def _soft(opened: np.ndarray, origin: np.ndarray, pitch: float, geo: dict[str, Any], w: list[str]) -> trimesh.Trimesh:
    soften = float(geo.get("soften_mm", 0.9)) / pitch
    body = surface_from_volume(opened, origin, pitch, soften)
    iters = int(geo.get("smooth_iters", 8))
    if iters > 0:
        trimesh.smoothing.filter_taubin(body, lamb=0.5, nu=-0.53, iterations=iters)
    target = int(geo.get("target_faces", 60000))
    if len(body.faces) > target:
        try:
            body = body.simplify_quadric_decimation(face_count=target)
        except Exception as e:  # fast-simplification missing → keep full resolution
            w.append(f"Decimation skipped ({e.__class__.__name__}).")
    return body


def _blender_clay(opened: np.ndarray, origin: np.ndarray, pitch: float, geo: dict[str, Any], w: list[str]) -> trimesh.Trimesh:
    """The Blender clay finish; Soft when Blender isn't there or its result can't be printed."""
    from . import blender

    base = surface_from_volume(opened, origin, pitch, float(geo.get("soften_mm", 0.9)) / pitch)
    try:
        body = blender.clay(base, {
            "voxel_mm": max(pitch, float(geo.get("voxel_mm", 0.5))),
            "smooth": int(geo.get("clay_smooth", 10)),
            "texture_mm": float(geo.get("clay_texture_mm", 0.25)),
            "grain_mm": float(geo.get("clay_grain_mm", 3.0)),
            "target_faces": int(geo.get("target_faces", 60000)),
        })
        to_manifold(body)  # must be a closed solid, like every other finish
        if body.volume < 0:
            body.invert()
        return body
    except (blender.BlenderError, ValueError) as e:
        w.append(f"Blender clay wasn't used ({e}); used Soft instead. Install Blender from blender.org.")
        return _soft(opened, origin, pitch, geo, w)


def printify(raw: trimesh.Trimesh, params: dict[str, Any], progress: Progress | None = None,
             cache_key: str | None = None) -> PrintResult:
    say = progress or (lambda p, m: None)
    geo = params["geometry"]
    base_p = params["base"]
    mag = params["magnet"]
    warnings: list[str] = []

    height = float(geo.get("height_mm", 50))
    flat = params.get("flat_back") or {}
    ring_p = params.get("ring") or {}
    use_base = bool(base_p.get("enabled", True)) and not flat.get("enabled")
    base_t = float(base_p.get("thickness_mm", 3)) if use_base else 0.0
    flat_cut = 0.0 if geo.get("finish") == "voxel" else float(geo.get("flat_cut", 0.03))
    body_h = height - base_t + (SINK_MM if use_base else 0.0)

    pitch_req = float(geo.get("voxel_mm", 0.5))
    min_feat = float(geo.get("min_feature_mm", 1.6))
    finish = str(geo.get("finish", "soft"))
    k1 = (cache_key, round(body_h, 4), round(flat_cut, 4), round(pitch_req, 4)) if cache_key and CACHE["enabled"] else None
    k2 = k1 and (*k1, round(min_feat, 4))
    k3 = k2 and (*k2, finish, *(geo.get(k) for k in ("voxel_size_mm", "facets", "soften_mm", "smooth_iters", "target_faces",
                                                       "clay_texture_mm", "clay_grain_mm", "clay_smooth")))

    hit3 = _get(k3)
    if hit3:
        body, pitch, thin_removed, w = hit3
        warnings += w
        say(0.6, "Reusing the shape (only the parts you changed are rebuilt)")
    else:
        hit2 = _get(k2)
        if hit2:
            opened, origin, pitch, thin_removed, w = hit2
            warnings += w
        else:
            hit1 = _get(k1)
            if hit1:
                vol, origin, pitch, solid_vox, w = hit1
                warnings += w
            else:
                w: list[str] = []
                say(0.05, "Standing the mesh up")
                mesh = raw.copy()
                mesh.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [1, 0, 0]))  # y-up → z-up
                ext = mesh.extents
                if ext[2] <= 1e-9:
                    raise ValueError("the AI mesh is flat")
                # scale so that after the flat cut the body is exactly body_h tall
                s = body_h / (ext[2] * (1 - flat_cut))
                mesh.apply_scale(s)
                mesh.apply_translation(-mesh.bounds[0])

                pitch = pitch_req
                longest = float(mesh.extents.max())
                if longest / pitch > MAX_VOXELS:
                    pitch = longest / MAX_VOXELS
                    w.append(f"Large object: remesh resolution relaxed to {pitch:.2f} mm.")

                say(0.2, "Making it solid")
                with timing.stage("printify.voxelize"):
                    vol, origin = solid_volume(mesh, pitch)
                solid_vox = int(vol.sum())
                if solid_vox == 0:
                    raise ValueError("the AI mesh has no volume")
                warnings += w
                _put(k1, (vol, origin, pitch, solid_vox, list(warnings)))

            w = []
            say(0.35, "Removing parts too thin to print")
            with timing.stage("printify.thin_parts"):
                r_vox = int(round(min_feat / 2 / pitch))
                opened = ndimage.binary_opening(np.pad(vol, r_vox + 1), structure=_ball(r_vox))
                opened = opened[r_vox + 1 : -(r_vox + 1), r_vox + 1 : -(r_vox + 1), r_vox + 1 : -(r_vox + 1)]
                opened = _largest_component(opened)
            if opened.sum() < 0.2 * solid_vox:
                w.append("Most of the shape was thinner than the minimum feature size; kept the original.")
                opened = _largest_component(vol)
            thin_removed = 1.0 - opened.sum() / solid_vox
            if thin_removed > 0.08:
                w.append(f"{thin_removed:.0%} of the volume was too thin to print and was removed.")
            warnings += w
            _put(k2, (opened, origin, pitch, thin_removed, list(warnings)))

        w = []
        with timing.stage("printify.finish", finish=finish):
            if finish == "voxel":
                say(0.5, "Rebuilding from blocks")
                body = voxel_surface(opened, origin, pitch, float(geo.get("voxel_size_mm", 2.5)))
            elif finish == "faceted":
                say(0.5, "Cutting facets")
                body = surface_from_volume(opened, origin, pitch, min(float(geo.get("soften_mm", 0.9)), 0.5) / pitch)
                body = facet(body, int(geo.get("facets", 400)), w)
            elif finish == "blender":
                say(0.5, "Finishing in Blender (clay)")
                body = _blender_clay(opened, origin, pitch, geo, w)
            else:
                say(0.5, "Softening into clay")
                body = _soft(opened, origin, pitch, geo, w)
        warnings += w
        _put(k3, (body, pitch, thin_removed, list(warnings)))

    say(0.7, "Cutting a flat bottom")
    body_m = to_manifold(body)
    zmin, zmax = body.bounds[0][2], body.bounds[1][2]
    body_m = body_m.trim_by_plane((0, 0, 1), zmin + flat_cut * (zmax - zmin))
    body_m = max(body_m.decompose(), key=lambda p: p.volume())  # the cut can leave crumbs
    bb = body_m.bounding_box()
    lo, hi = np.array(bb[:3]), np.array(bb[3:])
    k = body_h / (hi[2] - lo[2])  # blur and cut shift the height slightly; land it exactly
    body_m = body_m.scale((k, k, k))
    lo, hi = lo * k, hi * k
    centre = (lo[:2] + hi[:2]) / 2
    z0 = (base_t - SINK_MM) if use_base else 0.0
    body_m = body_m.translate((-centre[0], -centre[1], z0 - lo[2]))

    if flat.get("enabled"):
        say(0.76, "Making a flat back")
        body_m = lay_flat(body_m, float(flat.get("thickness_mm", 8)))
        if ring_p.get("enabled"):
            say(0.8, "Adding the key-ring loop")
            body_m = add_ring(body_m, ring_p, warnings)

    base_m = None
    verts = np.asarray(body_m.to_mesh().vert_properties)[:, :3]
    if use_base:
        say(0.82, "Adding the base")
        radius = float(np.sqrt((verts[:, :2] ** 2).sum(1)).max()) + float(base_p.get("margin_mm", 3))
        base_m = make_base(radius, base_t, float(base_p.get("fillet_mm", 1.2)))
        base_m = base_m - body_m  # pocket for the body: no overlap, parts key together

    if mag.get("enabled"):
        say(0.9, "Cutting the magnet pocket")
        cutter = magnet_cutter(float(mag["diameter_mm"]), float(mag["depth_mm"]), float(mag.get("clearance_mm", 0.15)))
        needed = float(mag["depth_mm"]) + 0.8
        if base_m is not None and base_t >= needed:
            base_m = base_m - cutter
        elif base_m is None:
            # the pocket plus 0.8 mm of roof must sit inside the body
            probe = magnet_cutter(float(mag["diameter_mm"]), needed, float(mag.get("clearance_mm", 0.15)))
            if (body_m ^ probe).volume() < 0.97 * probe.volume():
                warnings.append("The body is thin or narrow where the magnet goes; check the pocket in the slicer.")
            body_m = body_m - cutter
        else:
            body_m = body_m - cutter
            if base_m is not None:
                base_m = base_m - cutter
                warnings.append("Base is thinner than the magnet; the pocket goes into the body too.")

    say(0.95, "Checking printability")
    body_t = to_trimesh(body_m)
    base_tm = to_trimesh(base_m) if base_m is not None else None
    report = _report(body_t, base_tm, pitch, min_feat, thin_removed, warnings)
    report["format"] = params.get("format", "stand")
    say(1.0, "Ready to print")
    return PrintResult(body_t, base_tm, report)


def _overhang_pct(m: trimesh.Trimesh, floor_z: float) -> float:
    n = m.face_normals
    a = m.area_faces
    c = m.triangles_center
    down = (n[:, 2] < -math.cos(math.radians(45))) & (c[:, 2] > floor_z + 1.5)  # edges at the bed print fine
    return float(a[down].sum() / max(a.sum(), 1e-9) * 100)


def _report(body, base, pitch, min_feat, thin_removed, warnings) -> dict[str, Any]:
    parts = [body] + ([base] if base is not None else [])
    allm = trimesh.util.concatenate(parts)
    overhang = _overhang_pct(body, body.bounds[0][2])
    if overhang > 12:
        warnings.append(f"{overhang:.0f}% of the surface overhangs more than 45°; enable supports.")
    ok = all(p.is_watertight for p in parts)
    if not ok:
        warnings.append("A part is not watertight; the slicer may need to repair it.")
    return {
        "watertight": ok,
        "size_mm": [round(float(x), 1) for x in allm.extents],
        "body_volume_cm3": round(float(body.volume) / 1000, 2),
        "base_volume_cm3": round(float(base.volume) / 1000, 2) if base is not None else 0,
        "faces": int(sum(len(p.faces) for p in parts)),
        "remesh_mm": round(pitch, 2),
        "min_feature_mm": min_feat,
        "thin_removed_pct": round(float(thin_removed) * 100, 1),
        "overhang_pct": round(overhang, 1),
        "warnings": warnings,
    }


def run(raw_path: Path, out_dir: Path, params: dict[str, Any], progress: Progress | None = None) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    st = raw_path.stat()
    res = printify(load_mesh(raw_path), params, progress, cache_key=f"{raw_path.resolve()}:{st.st_mtime_ns}:{st.st_size}")
    for stale in ["parts.json", "plaque.stl", *(f.name for f in out_dir.glob("part_*.stl"))]:
        (out_dir / stale).unlink(missing_ok=True)  # an earlier logo build of this object
    res.body.export(out_dir / "body.stl")
    if res.base is not None:
        res.base.export(out_dir / "base.stl")
    else:
        (out_dir / "base.stl").unlink(missing_ok=True)
    return res.report
