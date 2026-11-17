"""Studio product renders of a print mesh with Blender Cycles (matte PLA, soft area lights, shadow catcher).
usage: python3 blender_render.py MODEL.stl OUTDIR [hex_colour]
Writes OUTDIR/render_front.png, _side, _back, _iso (RGBA with soft shadow).
"""
import sys, os, math, bpy

src, outdir = sys.argv[1], sys.argv[2]
hexcol = sys.argv[3] if len(sys.argv) > 3 else '3DA34A'
os.makedirs(outdir, exist_ok=True)


def srgb_to_lin(c):
    c /= 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene
sc.render.engine = 'CYCLES'
sc.cycles.device = 'CPU'
sc.cycles.samples = 96
sc.cycles.use_denoising = True
sc.render.film_transparent = True
sc.render.resolution_x, sc.render.resolution_y = 720, 960
sc.view_settings.view_transform = 'AgX'
sc.view_settings.look = 'AgX - Medium High Contrast'
sc.view_settings.exposure = 0.05

# model: mm -> scene units (x0.05), Y-up print coords -> Z-up, sitting on the floor
bpy.ops.wm.stl_import(filepath=src)
ob = bpy.context.selected_objects[0]
bpy.context.view_layer.objects.active = ob
bpy.ops.object.shade_smooth_by_angle(angle=math.radians(35))   # flat back stays crisp
ob.scale = (0.05, 0.05, 0.05)
ob.rotation_euler = (math.radians(90), 0, 0)
bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='BOUNDS')
zmin = min((ob.matrix_world @ v.co).z for v in ob.data.vertices)
ob.location = (0, 0, ob.location.z - zmin)
H = ob.dimensions.z

mat = bpy.data.materials.new('PLA')
mat.use_nodes = True
bsdf = mat.node_tree.nodes['Principled BSDF']
bsdf.inputs['Base Color'].default_value = (*[srgb_to_lin(int(hexcol[i:i + 2], 16)) for i in (0, 2, 4)], 1)
bsdf.inputs['Roughness'].default_value = 0.45
bsdf.inputs['Specular IOR Level'].default_value = 0.35
bsdf.inputs['Subsurface Weight'].default_value = 0.04
bsdf.inputs['Subsurface Radius'].default_value = (0.05, 0.08, 0.05)
ob.data.materials.append(mat)

# floor = shadow catcher
bpy.ops.mesh.primitive_plane_add(size=40, location=(0, 0, 0))
bpy.context.object.is_shadow_catcher = True

# world: soft neutral fill
world = bpy.data.worlds.new('W'); sc.world = world
world.use_nodes = True
world.node_tree.nodes['Background'].inputs['Color'].default_value = (0.9, 0.9, 0.9, 1)
world.node_tree.nodes['Background'].inputs['Strength'].default_value = 0.55


def area(name, loc, energy, size):
    bpy.ops.object.light_add(type='AREA', location=loc)
    L = bpy.context.object; L.name = name
    L.data.energy = energy; L.data.size = size
    tgt = (0, 0, H * 0.5)
    d = [tgt[i] - loc[i] for i in range(3)]
    L.rotation_euler = (math.atan2(math.hypot(d[0], d[1]), -d[2]), 0, math.atan2(d[1], d[0]) + math.pi / 2)
    return L


area('Key', (-1.6, -4.6, 5.2), 1500, 6.0)
area('Fill', (4.5, -3.0, 2.0), 300, 6.0)
area('Rim', (0.5, 4.5, 4.0), 450, 3.0)

bpy.ops.object.camera_add()
cam = bpy.context.object; sc.camera = cam
cam.data.lens = 125
dist = 8.6
elev = math.radians(8)
cam.location = (0, -dist * math.cos(elev), H * 0.5 + dist * math.sin(elev))
cam.rotation_euler = (math.radians(90) - elev, 0, 0)

for name, rz in [('front', 0), ('side', -90), ('back', 180), ('iso', -35)]:
    ob.rotation_euler = (0, 0, math.radians(rz))
    sc.render.filepath = os.path.join(outdir, f'render_{name}.png')
    bpy.ops.render.render(write_still=True)
    print('rendered', name)
