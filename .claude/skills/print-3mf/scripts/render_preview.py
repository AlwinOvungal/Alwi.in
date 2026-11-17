"""Smooth-shaded preview of one or more STL/3MF parts, from several angles, as a PNG.

usage: python3 render_preview.py OUT.png PART.stl[:RRGGBB] [PART2.stl:RRGGBB ...] [--views front,side,back,iso]
       python3 render_preview.py OUT.png MODEL.3mf          (colours read from the 3MF if present)

Views assume the model's Z is "out of the front face" for flat pieces (magnets, ornaments printed
lying down) — use --upright for models printed standing (Z up), e.g. the traffic cone.
Point-splat z-buffer with interpolated vertex normals: no GPU or display needed.
"""
import sys, re, zipfile
import numpy as np, trimesh
from PIL import Image, ImageFilter


def load_parts(args):
    parts = []
    for a in args:
        if a.lower().endswith('.3mf'):
            xml = zipfile.ZipFile(a).read('3D/3dmodel.model').decode()
            cols = re.findall(r'displaycolor="#([0-9A-Fa-f]{6})', xml)
            scene = trimesh.load(a)
            for i, g in enumerate(scene.geometry.values()):
                parts.append((g, cols[i] if i < len(cols) else '4CAF50'))
        else:
            path, _, col = a.partition(':')
            parts.append((trimesh.load(path), col or '4CAF50'))
    return parts


def main():
    argv = sys.argv[1:]
    upright = '--upright' in argv
    argv = [a for a in argv if a != '--upright']
    views = ['front', 'side', 'back', 'iso']
    if '--views' in argv:
        i = argv.index('--views'); views = argv[i + 1].split(','); del argv[i:i + 2]
    out, inputs = argv[0], argv[1:]
    parts = load_parts(inputs)
    total = sum(m.area for m, _ in parts)
    P, N, C = [], [], []
    for m, col in parts:
        n_pts = int(6_000_000 * m.area / total)
        p, fi = trimesh.sample.sample_surface(m, max(n_pts, 10000))
        bary = trimesh.triangles.points_to_barycentric(m.triangles[fi], p)
        n = np.einsum('ij,ijk->ik', bary, m.vertex_normals[m.faces[fi]])
        n /= np.linalg.norm(n, axis=1, keepdims=True)
        P.append(p); N.append(n); C.append(np.tile([int(col[i:i + 2], 16) / 255 for i in (0, 2, 4)], (len(p), 1)))
    p, n, c = np.vstack(P), np.vstack(N), np.vstack(C)
    lo, hi = p.min(0), p.max(0); ctr = (lo + hi) / 2; span = np.linalg.norm(hi - lo) * 0.85

    if upright:   # Z up, front faces -Y
        dirs = {'front': ([0, -1, 0], [0, 0, 1]), 'side': ([1, 0, 0], [0, 0, 1]),
                'back': ([0, 1, 0], [0, 0, 1]), 'iso': ([0.7, -1, 0.6], [0, 0, 1])}
    else:         # flat piece: front faces +Z, Y up
        dirs = {'front': ([0, 0, 1], [0, 1, 0]), 'side': ([-1, 0, 0], [0, 1, 0]),
                'back': ([0, 0, -1], [0, 1, 0]), 'iso': ([0.65, -0.35, 1], [0, 1, 0])}

    def view(d, up, S=520):
        d = np.array(d, float); d /= np.linalg.norm(d); up = np.array(up, float)
        right = np.cross(up, d); right /= np.linalg.norm(right); up = np.cross(d, right)
        q = p - ctr; u, v, depth = q @ right, q @ up, q @ d
        s = S / span
        ix = np.clip((u * s + S / 2).astype(int), 0, S - 1); iy = np.clip((S / 2 - v * s).astype(int), 0, S - 1)
        ok = (n @ d) > -0.05
        idx = np.where(ok)[0][np.argsort(depth[ok])]
        key = d * 0.55 + up * 0.6 - right * 0.45; key /= np.linalg.norm(key)
        fill = d * 0.8 - up * 0.2 + right * 0.5; fill /= np.linalg.norm(fill)
        diff = np.clip(n @ key, 0, 1) * 0.75 + np.clip(n @ fill, 0, 1) * 0.25
        hv = key + d; hv /= np.linalg.norm(hv)
        spec = np.clip(n @ hv, 0, 1) ** 40 * 0.2
        col = np.clip(c * (0.3 + 0.78 * diff)[:, None] + spec[:, None], 0, 1)
        img = np.full((S, S, 3), 0.97); img[iy[idx], ix[idx]] = col[idx]
        return img

    im = np.hstack([view(*dirs[v]) for v in views])
    Image.fromarray((im * 255).astype(np.uint8)).filter(ImageFilter.MedianFilter(3)).save(out)
    print('wrote', out, 'views:', ', '.join(views))


if __name__ == '__main__':
    main()
