"""Экспорт лоу-поли в форматы для движков (запуск из Blender, текстуры уже лежат в out/textures):
blender -b --python tools/export.py -- lo.glb out_dir slug diff.png nor_gl.png [--height-cm 25]

Пишет <slug>.glb, <slug>.gltf+.bin (текстуры — ссылками на textures/), <slug>.fbx, <slug>.obj+.mtl.
Модель ставится на пол (низ в Y=0 у glTF), центрируется; с --height-cm масштабируется до реальной высоты (1 ед. = 1 м).
"""
import os
import sys

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
src, out, slug, diff, nor = (os.path.abspath(argv[0]), os.path.abspath(argv[1]), argv[2],
                             os.path.abspath(argv[3]), os.path.abspath(argv[4]))
height_cm = float(argv[argv.index("--height-cm") + 1]) if "--height-cm" in argv else 0.0

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
bpy.ops.object.select_all(action="DESELECT")
for o in meshes:
    o.select_set(True)
bpy.context.view_layer.objects.active = meshes[0]
if len(meshes) > 1:
    bpy.ops.object.join()
obj = bpy.context.view_layer.objects.active
for o in list(bpy.context.scene.objects):  # пустышки импорта glTF не нужны
    if o is not obj and o.type != "MESH":
        bpy.data.objects.remove(o)
obj.parent = None
obj.name = obj.data.name = slug
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

# на пол и в центр; реальный размер — если задан
pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
k = (height_cm / 100.0) / (hi.z - lo.z) if height_cm > 0 and hi.z > lo.z else 1.0
center = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
obj.data.transform(__import__("mathutils").Matrix.Translation(-center))
obj.data.transform(__import__("mathutils").Matrix.Scale(k, 4))
obj.data.update()

# материал заново — с текстурами из textures/ под их итоговыми именами (на них сошлются gltf, fbx и obj)
mat = bpy.data.materials.new(slug)
mat.use_nodes = True
nt = mat.node_tree
bsdf = nt.nodes["Principled BSDF"]
bsdf.inputs["Roughness"].default_value = 0.6
img_d = bpy.data.images.load(diff)
img_d.name = os.path.splitext(os.path.basename(diff))[0]
img_n = bpy.data.images.load(nor)
img_n.name = os.path.splitext(os.path.basename(nor))[0]
img_n.colorspace_settings.name = "Non-Color"
td = nt.nodes.new("ShaderNodeTexImage"); td.image = img_d
tn = nt.nodes.new("ShaderNodeTexImage"); tn.image = img_n
nm = nt.nodes.new("ShaderNodeNormalMap")
nt.links.new(td.outputs["Color"], bsdf.inputs["Base Color"])
nt.links.new(tn.outputs["Color"], nm.inputs["Color"])
nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
obj.data.materials.clear()
obj.data.materials.append(mat)

bpy.ops.object.select_all(action="DESELECT")
obj.select_set(True)
base = os.path.join(out, slug)
bpy.ops.export_scene.gltf(filepath=base + ".glb", export_format="GLB", use_selection=True)
bpy.ops.export_scene.gltf(filepath=base + ".gltf", export_format="GLTF_SEPARATE", use_selection=True,
                          export_texture_dir="textures", export_image_format="AUTO")
bpy.ops.export_scene.fbx(filepath=base + ".fbx", use_selection=True, path_mode="RELATIVE", embed_textures=False,
                         apply_scale_options="FBX_SCALE_ALL", mesh_smooth_type="FACE")
bpy.ops.wm.obj_export(filepath=base + ".obj", export_selected_objects=True, path_mode="RELATIVE",
                      export_materials=True, export_normals=True, export_uv=True, forward_axis="NEGATIVE_Z", up_axis="Y")
dims = obj.dimensions
print(f"EXPORT tris={sum(len(p.vertices) - 2 for p in obj.data.polygons)} size_m={dims.x:.3f}x{dims.y:.3f}x{dims.z:.3f}")
