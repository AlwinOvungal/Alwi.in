"""Close-up renders of regions of an STL (model mm) to inspect finishing details: joints, edges, creases.
usage: python3 render_closeups.py MODEL.stl OUT.png   (edit `regions` to the areas you want to inspect)
"""
import sys, numpy as np, trimesh
from PIL import Image, ImageFilter
stl, out = sys.argv[1], sys.argv[2]
m = trimesh.load(stl)
regions = [((-6, 26), (6, 41)), ((-13, 0), (-1, 14)), ((-5, -1), (5, 9))]   # (xmin,ymin),(xmax,ymax) in model mm
views = [([0, 0, 1], [0, 1, 0]), ([0.5, -0.3, 1], [0, 1, 0])]
col = np.array([.30, .66, .30]); imgs = []
for (x0, y0), (x1, y1) in regions:
    P, N = [], []
    for _ in range(12):
        p, fi = trimesh.sample.sample_surface(m, 2_000_000)
        k = (p[:, 0] > x0 - 2) & (p[:, 0] < x1 + 2) & (p[:, 1] > y0 - 2) & (p[:, 1] < y1 + 2)
        p, fi = p[k], fi[k]
        b = trimesh.triangles.points_to_barycentric(m.triangles[fi], p)
        n = np.einsum('ij,ijk->ik', b, m.vertex_normals[m.faces[fi]]); n /= np.linalg.norm(n, axis=1, keepdims=True)
        P.append(p); N.append(n)
    p, n = np.vstack(P), np.vstack(N)
    ctr = np.array([(x0 + x1) / 2, (y0 + y1) / 2, 5]); span = max(x1 - x0, y1 - y0) * 1.1
    for d, up in views:
        d = np.array(d, float); d /= np.linalg.norm(d); up = np.array(up, float)
        right = np.cross(up, d); right /= np.linalg.norm(right); up = np.cross(d, right)
        q = p - ctr; u, v, dep = q @ right, q @ up, q @ d
        S = 600; s = S / span
        ix = (u * s + S / 2).astype(int); iy = (S / 2 - v * s).astype(int)
        ok = ((n @ d) > -0.05) & (ix >= 0) & (ix < S) & (iy >= 0) & (iy < S)
        idx = np.where(ok)[0][np.argsort(dep[ok])]
        key = d * 0.5 + up * 0.6 - right * 0.5; key /= np.linalg.norm(key)
        diff = np.clip(n @ key, 0, 1); hv = key + d; hv /= np.linalg.norm(hv)
        c = np.clip(col * (0.3 + 0.8 * diff)[:, None] + (np.clip(n @ hv, 0, 1) ** 40 * 0.25)[:, None], 0, 1)
        img = np.full((S, S, 3), 0.97); img[iy[idx], ix[idx]] = c[idx]; imgs.append(img)
row1 = np.hstack(imgs[:3]); row2 = np.hstack(imgs[3:])
Image.fromarray((np.vstack([np.hstack([imgs[0], imgs[2], imgs[4]]), np.hstack([imgs[1], imgs[3], imgs[5]])]) * 255).astype(np.uint8)).filter(ImageFilter.MedianFilter(3)).save(out)
