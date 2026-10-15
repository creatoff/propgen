"""Лист-превью GLB: 4 ракурса с текстурой + 4 с сеткой, счётчик треугольников. Запуск через Blender:
blender -b --python tools/render.py -- model.glb out.png [--size 512]
"""
import math
import sys

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
import os
src, out = os.path.abspath(argv[0]), os.path.abspath(argv[1])
size = int(argv[argv.index("--size") + 1]) if "--size" in argv else 512

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
TWO_SIDED = "--two-sided" in argv
if "--clay" in argv:  # голая геометрия: серый двусторонний материал
    clay = bpy.data.materials.new("clay")
    clay.diffuse_color = (0.7, 0.7, 0.7, 1)
    for o in meshes:
        o.data.materials.clear()
        o.data.materials.append(clay)
for o in meshes:
    for m in o.data.materials:
        if m and TWO_SIDED:
            m.use_backface_culling = False
tris = 0
for o in meshes:
    o.data.calc_loop_triangles()
    tris += len(o.data.loop_triangles)

# рамка объекта
pts = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
center, radius = (lo + hi) / 2, (hi - lo).length / 2

scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in {e.identifier for e in
                                                                     bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items} else "BLENDER_EEVEE"
scene.render.resolution_x = scene.render.resolution_y = size
scene.render.film_transparent = False
scene.view_settings.view_transform = "Standard"
world = bpy.data.worlds.new("w")
scene.world = world
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.42, 0.42, 0.45, 1)
world.node_tree.nodes["Background"].inputs[1].default_value = 0.9
sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
sun.data.energy = 2.5
sun.rotation_euler = (math.radians(50), 0, math.radians(30))
scene.collection.objects.link(sun)

cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
cam.data.type = "ORTHO"
cam.data.ortho_scale = radius * 2.2
scene.collection.objects.link(cam)
scene.camera = cam

# glTF импортируется Y-up → Z-up: перед объекта смотрит в -Y
frames = []
import os, tempfile
tmp = tempfile.mkdtemp()
for wire in (False, True):
    if wire:
        for o in meshes:
            m = o.modifiers.new("wf", "WIREFRAME")
            m.thickness = radius * 0.004
            m.use_replace = False
            mat = bpy.data.materials.new("wire")
            mat.diffuse_color = (0.05, 0.05, 0.05, 1)
            mat.use_nodes = True
            mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.02, 0.02, 0.02, 1)
            o.data.materials.append(mat)
            m.material_offset = len(o.data.materials) - 1
    for i, az in enumerate((0, 90, 180, 270)):
        a = math.radians(az)
        d = Vector((math.sin(a), -math.cos(a), 0.35)).normalized() * radius * 4
        cam.location = center + d
        cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
        p = os.path.join(tmp, f"{int(wire)}_{i}.png")
        scene.render.filepath = p
        bpy.ops.render.render(write_still=True)
        frames.append(p)

# склейка 4×2
sheet = bpy.data.images.new("sheet", size * 4, size * 2)
px = [0.0] * (size * 4 * size * 2 * 4)
for k, p in enumerate(frames):
    im = bpy.data.images.load(p)
    src_px = list(im.pixels)
    col, row = k % 4, 1 - k // 4
    for y in range(size):
        a0 = ((row * size + y) * size * 4 + col * size) * 4
        b0 = y * size * 4
        px[a0:a0 + size * 4] = src_px[b0:b0 + size * 4]
sheet.pixels = px
sheet.filepath_raw = out
sheet.file_format = "PNG"
sheet.save()
print(f"TRIS {tris} -> {out}")
