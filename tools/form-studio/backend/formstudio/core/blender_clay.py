"""Runs INSIDE Blender (not Form Studio's Python): the "Blender clay" 3D finish.

    blender -b --factory-startup --python-exit-code 1 -P blender_clay.py -- in.stl out.stl params.json
    python blender_clay.py -- in.stl out.stl params.json     (with Blender's Python package, bpy)

1. voxel remesh: even, closed topology
2. smooth: soften what the AI left lumpy
3. displace with a clouds texture: a fine hand-made clay surface (same noise every run)
4. voxel remesh again: keeps the textured surface closed and printable
5. decimate to the target face count
Works with Blender 3.6 to 5.x (the STL operators were renamed in 4.2).
"""
import json
import sys

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
src, dst, cfg_path = argv[:3]
with open(cfg_path) as f:
    p = json.load(f)

voxel = float(p.get("voxel_mm", 0.5))
smooth = int(p.get("smooth", 10))
strength = float(p.get("texture_mm", 0.25))
grain = float(p.get("grain_mm", 3.0))
target = int(p.get("target_faces", 60000))

# start empty: factory settings (no add-ons or preferences), then remove the default cube, camera and light
bpy.ops.wm.read_factory_settings(use_empty=True)
for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob, do_unlink=True)

if hasattr(bpy.ops.wm, "stl_import"):
    bpy.ops.wm.stl_import(filepath=src)
else:  # Blender before 4.2
    bpy.ops.import_mesh.stl(filepath=src)
obj = [o for o in bpy.context.scene.objects if o.type == "MESH"][0]
bpy.context.view_layer.objects.active = obj


def apply_all() -> None:
    """Bake the modifiers into the mesh, without needing an interactive context."""
    dg = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(dg))
    old = obj.data
    obj.modifiers.clear()
    obj.data = mesh
    bpy.data.meshes.remove(old)


def remesh(name: str) -> None:
    m = obj.modifiers.new(name, "REMESH")
    m.mode = "VOXEL"
    m.voxel_size = voxel
    m.use_smooth_shade = True


remesh("remesh")
if smooth > 0:
    s = obj.modifiers.new("smooth", "SMOOTH")
    s.factor = 0.5
    s.iterations = smooth
apply_all()

if strength > 0:
    tex = bpy.data.textures.new("clay", "CLOUDS")
    tex.noise_scale = max(0.1, grain)
    tex.noise_depth = 2
    tex.noise_basis = "BLENDER_ORIGINAL"  # fixed noise: the same surface every run
    d = obj.modifiers.new("clay", "DISPLACE")
    d.texture = tex
    d.texture_coords = "LOCAL"
    d.strength = strength
    d.mid_level = 0.5
    remesh("closed")  # the displaced surface, closed again
    apply_all()

faces = len(obj.data.polygons)
if faces > target > 0:
    dec = obj.modifiers.new("decimate", "DECIMATE")
    dec.decimate_type = "COLLAPSE"
    dec.ratio = target / faces
    apply_all()

for ob in bpy.context.scene.objects:
    ob.select_set(ob == obj)
if hasattr(bpy.ops.wm, "stl_export"):
    bpy.ops.wm.stl_export(filepath=dst, export_selected_objects=True, ascii_format=False)
else:
    bpy.ops.export_mesh.stl(filepath=dst, use_selection=True)
print(f"formstudio: clay finish done, {len(obj.data.polygons)} faces")
