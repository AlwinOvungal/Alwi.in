"""Minimal end-to-end demo of the sdf3mf library: a two-colour star fridge magnet.
White backing plate (outline grown + filleted with offset_outline), raised yellow star inlay,
8x2 mm magnet pocket on the bed side. Run: python3 star_magnet_lib_demo.py OUT.3mf
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
from sdf3mf import *

out = sys.argv[1] if len(sys.argv) > 1 else 'star_magnet.3mf'
T, INLAY = 5.0, 1.6
g = Grid((-25, 25), (-25, 25), (-1, 6), res=0.12)
X, Y, Z = g.X2, g.Y2, g.Z

star = star5(X, Y, 0, -1.5, 18 - 2.0, 0.5) - 2.0             # rounded 5-point star
outline = offset_outline(star, g, offset=2.5, fillet=2.0)      # border, concave corners filleted

plate = extrude(outline, Z, 0, T, top_round=1.0)
plate = subtract(plate, cylinder_hole_z(X, Y, Z, 0, -1.5, 8.3, -1, 2.1))     # magnet pocket
white = subtract(plate, extrude(star, Z, T - INLAY, T + 1))                 # pocket for the inlay
yellow = extrude(star, Z, T - INLAY, T, top_round=0.7)

parts = [('White plate', mesh_from_field(white, g), 'F4F2EE'),
         ('Yellow star', mesh_from_field(yellow, g), 'FFC93C')]
for n, m, _ in parts:
    report(n, m)
write_3mf(out, parts, title='Star Magnet')
verify_3mf(out)
print('wrote', out)
