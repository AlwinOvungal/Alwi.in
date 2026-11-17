"""Blender 'finish' pass on a print mesh: volume-preserving Laplacian smoothing (flat back and hanging
hole pinned via a vertex group), then collapse-decimation for a lighter file. Shade smooth.
usage: python3 blender_finish.py IN.stl OUT.stl HOLE_X HOLE_Y
"""
import sys, bpy, bmesh
from mathutils import Vector

src, dst = sys.argv[1], sys.argv[2]
hx, hy = float(sys.argv[3]), float(sys.argv[4])
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.wm.stl_import(filepath=src)
ob = bpy.context.selected_objects[0]
bpy.context.view_layer.objects.active = ob
me = ob.data
print('imported', len(me.polygons), 'faces')

# pin: bed layers (z < 0.45 mm, keeps the chamfer and a flat back) and the hole wall (r < 2.25 mm)
vg = ob.vertex_groups.new(name='free')
free = [v.index for v in me.vertices
        if v.co.z > 0.45 and ((v.co.x - hx) ** 2 + (v.co.y - hy) ** 2) ** 0.5 > 2.25]
vg.add(free, 1.0, 'REPLACE')

sm = ob.modifiers.new('LaplacianSmooth', 'LAPLACIANSMOOTH')
sm.iterations = 6
sm.lambda_factor = 0.35
sm.lambda_border = 0.0
sm.use_volume_preserve = True
sm.use_normalized = True
sm.vertex_group = 'free'

dec = ob.modifiers.new('Decimate', 'DECIMATE')
dec.decimate_type = 'COLLAPSE'
dec.ratio = 0.35
dec.use_symmetry = False

for m in list(ob.modifiers):
    bpy.ops.object.modifier_apply(modifier=m.name)
bpy.ops.object.shade_smooth()
print('finished', len(ob.data.polygons), 'faces')
bpy.ops.wm.stl_export(filepath=dst, export_selected_objects=True, ascii_format=False)
print('wrote', dst)
