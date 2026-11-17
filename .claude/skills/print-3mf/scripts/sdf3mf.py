"""sdf3mf: build 3D-printable models from signed distance fields and write multi-part 3MF files.

Import from a model script:

    import sys; sys.path.insert(0, '<skill dir>/scripts')
    from sdf3mf import *

Conventions: millimetres; Z is up on the print bed; an SDF is negative inside, positive outside.
Typical flow:  Grid -> 2D/3D SDF fields -> booleans -> mesh_from_field() per part -> write_3mf().
"""
import zipfile
import numpy as np
from scipy.ndimage import distance_transform_edt, gaussian_filter
from skimage.measure import marching_cubes
import trimesh

# ----------------------------------------------------------------------------- grid


class Grid:
    """Regular sampling grid. The tiny offsets keep sample points off exact planes such as z=0,
    which otherwise produce zero-valued samples, degenerate triangles and non-watertight meshes."""

    def __init__(self, x_range, y_range, z_range, res=0.1):
        self.res = res
        self.xs = (np.arange(x_range[0], x_range[1] + res, res) + 0.0137).astype(np.float32)
        self.ys = (np.arange(y_range[0], y_range[1] + res, res) + 0.0231).astype(np.float32)
        self.zs = (np.arange(z_range[0], z_range[1] + res, res) + 0.0173).astype(np.float32)
        self.X2, self.Y2 = np.meshgrid(self.xs, self.ys, indexing='ij')   # 2D (x, y)
        self.Z = self.zs[None, None, :]                                    # broadcast with 2D[..., None]

    def xyz(self):
        """Full 3D coordinate arrays (memory heavy; prefer 2D fields + self.Z broadcasting)."""
        X, Y, Z = np.meshgrid(self.xs, self.ys, self.zs, indexing='ij')
        return X, Y, Z

    @property
    def origin(self):
        return np.array([self.xs[0], self.ys[0], self.zs[0]])


# ----------------------------------------------------------------------------- booleans


def union(*f):
    return np.minimum.reduce(f)


def intersect(*f):
    return np.maximum.reduce(f)


def subtract(a, b):
    return np.maximum(a, -b)


def smin(a, b, k):
    """Smooth union (fillet radius ~k). Note: it also grows the shape slightly where a and b are
    both near the surface, so do NOT use it to build outlines that must hit exact dimensions."""
    h = np.clip(0.5 + 0.5 * (b - a) / k, 0, 1)
    return b + (a - b) * h - k * h * (1 - h)


def smax(a, b, k):
    """Smooth intersection (rounds the new edge)."""
    h = np.clip(0.5 - 0.5 * (b - a) / k, 0, 1)
    return b + (a - b) * h + k * h * (1 - h)


def round_intersect(a, b, r):
    """Intersection whose new edge is a true radius-r round. Great for 'extrude 2D shape with
    rounded top edge':  round_intersect(d2[..., None], Z - top, r)."""
    return (np.sqrt(np.maximum(a + r, 0) ** 2 + np.maximum(b + r, 0) ** 2)
            + np.minimum(np.maximum(a, b) + r, 0) - r)


# ----------------------------------------------------------------------------- 2D primitives (pass X, Y)


def circle(X, Y, cx, cy, r):
    return np.hypot(X - cx, Y - cy) - r


def box2(X, Y, x0, y0, x1, y1):
    qx = np.maximum(x0 - X, X - x1); qy = np.maximum(y0 - Y, Y - y1)
    return np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0)


def round_rect(X, Y, cx, cy, w, h, r):
    qx = np.abs(X - cx) - (w / 2 - r); qy = np.abs(Y - cy) - (h / 2 - r)
    return np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - r


def polygon(X, Y, pts):
    """Exact SDF of a simple polygon given as [(x, y), ...]."""
    pts = np.asarray(pts, float)
    d = (X - pts[0, 0]) ** 2 + (Y - pts[0, 1]) ** 2
    s = np.ones_like(X)
    for i in range(len(pts)):
        vi, vj = pts[i], pts[i - 1]
        ex, ey = vj - vi
        wx, wy = X - vi[0], Y - vi[1]
        t = np.clip((wx * ex + wy * ey) / (ex * ex + ey * ey), 0, 1)
        d = np.minimum(d, (wx - ex * t) ** 2 + (wy - ey * t) ** 2)
        c1 = Y >= vi[1]; c2 = Y < vj[1]; c3 = ex * wy > ey * wx
        s = np.where((c1 & c2 & c3) | (~c1 & ~c2 & ~c3), -s, s)
    return s * np.sqrt(d)


def star5(X, Y, cx, cy, r, rf=0.5):
    """5-point star, point up. rf = inner/outer ratio. For a chunky rounded star use
    star5(..., r - k, rf) - k  (shrink then inflate by k)."""
    k1 = np.array([0.809016994375, -0.587785252292]); k2 = np.array([-k1[0], k1[1]])
    px, py = np.abs(X - cx), Y - cy
    for k in (k1, k2):
        dd = np.maximum(k[0] * px + k[1] * py, 0) * 2
        px, py = px - dd * k[0], py - dd * k[1]
    px = np.abs(px); py = py - r
    bax, bay = rf * -k1[1], rf * k1[0] - 1
    h = np.clip((px * bax + py * bay) / (bax * bax + bay * bay), 0, r)
    return np.hypot(px - bax * h, py - bay * h) * np.sign(py * bax - px * bay)


def ring2(X, Y, cx, cy, inner_r, wall):
    return np.abs(np.hypot(X - cx, Y - cy) - (inner_r + wall / 2)) - wall / 2


# ----------------------------------------------------------------------------- 3D primitives


def round_cone(P, a, b, r1, r2):
    """Sphere-swept cone between sphere (a, r1) and sphere (b, r2). P: (..., 3) array."""
    a = np.asarray(a, float); b = np.asarray(b, float)
    ba = b - a; l2 = ba @ ba; rr = r1 - r2; a2 = l2 - rr * rr; il2 = 1.0 / l2
    pa = P - a; y = pa @ ba; z = y - l2
    q = pa * l2 - y[..., None] * ba
    x2 = np.einsum('...i,...i', q, q); y2 = y * y * l2; z2 = z * z * l2
    k = np.sign(rr) * rr * rr * x2
    d3 = (np.sqrt(x2 * a2 * il2) + y * rr) * il2 - r1
    d2 = np.sqrt(x2 + y2) * il2 - r1
    d1 = np.sqrt(x2 + z2) * il2 - r2
    return np.where(np.sign(z) * a2 * z2 > k, d1, np.where(np.sign(y) * a2 * y2 < k, d2, d3))


def round_box(P, center, half, r):
    q = np.abs(P - np.asarray(center)) - (np.asarray(half) - r)
    return np.linalg.norm(np.maximum(q, 0), axis=-1) + np.minimum(q.max(-1), 0) - r


def extrude(d2, Z, z0, z1, top_round=0.0, bottom_round=0.0):
    """Extrude a 2D field between z0 and z1 with optional rounded top/bottom edges."""
    f = d2[..., None]
    top = round_intersect(f, Z - z1, top_round) if top_round > 0 else np.maximum(f, Z - z1)
    bot = round_intersect(f, z0 - Z, bottom_round) if bottom_round > 0 else np.maximum(f, z0 - Z)
    return np.maximum(top, bot)


def cylinder_hole_z(X, Y, Z, cx, cy, d, z0, z1):
    """Vertical blind hole (e.g. magnet pocket opening on the bed side: z0=-1, z1=depth)."""
    return np.maximum(np.hypot(X - cx, Y - cy)[..., None] - d / 2, np.maximum(z0 - Z, Z - z1))


# ----------------------------------------------------------------------------- outlines & reliefs


def offset_outline(d2, grid, offset, fillet=0.0, hires=0.025, margin=None):
    """Grow a 2D shape by `offset` and round its concave corners by `fillet` (morphological
    closing) WITHOUT changing the outer dimensions — the right way to make borders/backing plates.
    Computed with an exact distance transform on a fine grid, then resampled onto `grid`."""
    from scipy.ndimage import map_coordinates
    margin = margin if margin is not None else offset + fillet + 1
    # rebuild on a fine grid by bilinear upsampling of the source field (exact enough for outlines)
    xs, ys = grid.xs, grid.ys
    hx = np.arange(xs[0] - margin, xs[-1] + margin, hires)
    hy = np.arange(ys[0] - margin, ys[-1] + margin, hires)
    ix = (hx - xs[0]) / grid.res; iy = (hy - ys[0]) / grid.res
    IX, IY = np.meshgrid(ix, iy, indexing='ij')
    src = map_coordinates(d2, [IX, IY], order=1, mode='nearest')
    dil = src <= offset + fillet
    sd = (distance_transform_edt(~dil) - distance_transform_edt(dil)) * hires
    out_hi = sd + fillet
    jx = (grid.X2 - hx[0]) / hires; jy = (grid.Y2 - hy[0]) / hires
    return map_coordinates(out_hi, [jx, jy], order=1, mode='nearest').astype(np.float32)


def pillow_height(mask, res, depth, radius, edge_wall=0.7, smooth=1.1):
    """Height of a puffy 'pillow' over a 2D mask: rises from `edge_wall` at the outline to `depth`
    over `radius` mm using a quarter-circle profile. `depth` may be an array (e.g. thicker at the
    bottom). `smooth` (mm) blurs the distance field to hide medial-axis ridge lines."""
    dist = distance_transform_edt(mask) * res
    if smooth > 0:
        dist = gaussian_filter(dist, smooth / res)
    t = np.clip(dist / radius, 0, 1)
    return np.where(mask, edge_wall + (depth - edge_wall) * np.sqrt(1 - (1 - t) ** 2), 0.0)


def relief_body(H, sil, Z, antialias=0.12, res=0.1, edge_wall=0.7):
    """Solid between the flat back z=0 and front height-field H, clipped to 2D silhouette `sil`.
    Combine several pillows with np.maximum (hard max) to keep crisp creases between layers."""
    if antialias > 0:
        H = gaussian_filter(H, antialias / res)
    H = np.maximum(H, edge_wall)
    return np.maximum(np.maximum(Z - H[..., None], -Z), sil[..., None])


# ----------------------------------------------------------------------------- meshing & export


def mesh_from_field(field, grid, level=0.0):
    """Marching cubes + cleanup -> watertight trimesh (prints a warning if not)."""
    v, f, _, _ = marching_cubes(field, level, spacing=(grid.res,) * 3)
    v = np.round(v + grid.origin, 4)
    m = trimesh.Trimesh(v, f[:, ::-1], process=True)
    m.merge_vertices()
    for _ in range(5):
        m.update_faces(m.nondegenerate_faces(height=1e-6))
        m.update_faces(m.unique_faces())
        m.remove_unreferenced_vertices()
        m.merge_vertices()
    trimesh.repair.fill_holes(m)
    trimesh.repair.fix_normals(m)
    if not m.is_watertight:
        print('WARNING: mesh not watertight — check the grid covers the whole model (margin >= 1 mm)')
    return m


def report(name, m):
    size = (m.bounds[1] - m.bounds[0]).round(2).tolist()
    print(f'{name:16s} watertight={m.is_watertight} volume={m.volume / 1000:.2f} cm3 '
          f'(~{m.volume / 1000 * 1.24:.1f} g PLA solid) tris={len(m.faces)} size={size}')


def write_3mf(path, parts, title='Model', center=True, plate_xy=(128, 128)):
    """Write one printable object made of named, coloured parts.

    parts: list of (name, trimesh, 'RRGGBB'). Slicers (Bambu Studio, OrcaSlicer, PrusaSlicer) load it
    as a single object with multiple parts so each part can get its own filament. Vertices are
    shifted so the model is centred in XY and grounded at z=0 when center=True.
    """
    allb = np.vstack([m.bounds for _, m, _ in parts])
    lo, hi = allb.min(0), allb.max(0)
    shift = np.array([-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]]) if center else np.zeros(3)
    # Keep the shift on the same 1e-4 mm lattice as the (rounded) vertices: an arbitrary shift can
    # round two neighbouring vertices to the same printed value -> degenerate faces on reload.
    shift = np.round(shift, 4)
    mats = ''.join(f'<base name="{n}" displaycolor="#{c}FF"/>' for n, _, c in parts)
    objs, comps = [], []
    for i, (n, m, c) in enumerate(parts):
        oid = i + 2
        vs = '\n'.join(f'<vertex x="{a:.4f}" y="{b:.4f}" z="{d:.4f}"/>' for a, b, d in np.round(m.vertices + shift, 4))
        ts = '\n'.join(f'<triangle v1="{a}" v2="{b}" v3="{d}"/>' for a, b, d in m.faces)
        objs.append(f'<object id="{oid}" name="{n}" type="model" pid="1" pindex="{i}"><mesh><vertices>\n'
                    f'{vs}\n</vertices><triangles>\n{ts}\n</triangles></mesh></object>')
        comps.append(f'<component objectid="{oid}"/>')
    top = len(parts) + 2
    model = f'''<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
<metadata name="Title">{title}</metadata>
<resources>
<basematerials id="1">{mats}</basematerials>
{''.join(objs)}
<object id="{top}" name="{title}" type="model"><components>{''.join(comps)}</components></object>
</resources>
<build><item objectid="{top}" transform="1 0 0 0 1 0 0 0 1 {plate_xy[0]} {plate_xy[1]} 0"/></build>
</model>'''
    ct = ('<?xml version="1.0" encoding="UTF-8"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', ct)
        z.writestr('_rels/.rels', rels)
        z.writestr('3D/3dmodel.model', model)
    return path


def verify_3mf(path):
    """Re-open a written 3MF and confirm every part is watertight with positive volume."""
    s = trimesh.load(path)
    ok = True
    for k, g in s.geometry.items():
        m = trimesh.Trimesh(g.vertices, g.faces)
        good = m.is_watertight and m.volume > 0
        ok &= good
        print(f'  3MF part {k!r}: watertight={m.is_watertight} volume={m.volume:.0f} mm3')
    print('  overall size', (s.bounds[1] - s.bounds[0]).round(2).tolist(), 'OK' if ok else 'PROBLEM')
    return ok


# ----------------------------------------------------------------------------- surface textures


def band_noise(shape, sigma_vox, seed=0):
    """Smooth random field with unit standard deviation and feature size ~2*sigma voxels."""
    rng = np.random.default_rng(seed)
    n = gaussian_filter(rng.standard_normal(shape).astype(np.float32), sigma_vox)
    return n / (n.std() + 1e-9)


def clay_texture(field, grid, amp_lumps=0.10, size_lumps=1.8, amp_grain=0.03, size_grain=0.6,
                 amp_dents=0.08, size_dents=2.6, keep_bottom=0.6, keep_out=None, weight2d=None, seed=7):
    """Hand-modelled clay look: soft lumps + fine grain + shallow thumb dents, as a displacement of
    the SDF (positive values push the surface inward). Sizes are feature sizes in mm, amplitudes in mm.

    For FDM, amplitudes ~0.1–0.25 mm (≈ one layer) and features ≥ ~0.8 mm read best; finer grain
    mostly shows on sloped surfaces. `keep_bottom` mm above the bed stay untextured (flat first
    layers, chamfer intact). `keep_out`: 2D bool mask (e.g. inside/near a hanging hole) left smooth.
    """
    shape = field.shape
    r = grid.res
    lumps = band_noise(shape, size_lumps / 2 / r, seed)
    grain = band_noise(shape, size_grain / 2 / r, seed + 1)
    d = band_noise(shape, size_dents / 2 / r, seed + 2)
    dents = np.clip(d - 1.0, 0, None)               # only the strongest lows -> sparse shallow dents
    dents /= (dents.max() + 1e-9)
    disp = amp_lumps * lumps + amp_grain * grain + amp_dents * 2.5 * dents
    w = np.clip((grid.zs[None, None, :] - keep_bottom) / 0.4, 0, 1)
    if keep_out is not None:
        w = w * (~keep_out)[..., None]
    if weight2d is not None:
        w = w * weight2d[..., None]
    return field + disp * w
