"""Хай-поли GLB → лоу-поли в духе HL2: декимация до бюджета, жёсткие рёбра по углу, Smart UV, запекание (Cycles, GPU)
цвета, normal map и AO с хай-поли. Запуск:
blender -b --python tools/lowpoly.py -- hi.glb out_dir name [--tris 1500] [--tex 1024] [--crease 50] [--ao 1]
    [--res 256] [--slice-fill 1] [--nrm-src dense|hi] [--ref in_front.png,in_back.png] [--rot 90]  # эталоны цвета — вырезки входов из mesh.py
Результат: name_lo.glb, name_lo_base.png (цвет × AO), name_lo_normal.png, name_lo_ao.png
"""
import math
import os
import sys

import bpy
import bmesh
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
src, out_dir, name = os.path.abspath(argv[0]), os.path.abspath(argv[1]), argv[2]


def opt(key, default, cast=float):
    return cast(argv[argv.index(key) + 1]) if key in argv else default


TRIS = opt("--tris", 1500, int)
TEX = opt("--tex", 1024, int)
CREASE = opt("--crease", 50.0)
USE_AO = opt("--ao", 1, int)
os.makedirs(out_dir, exist_ok=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
parts = [o for o in bpy.context.scene.objects if o.type == "MESH"]
bpy.ops.object.select_all(action="DESELECT")
for o in parts:
    o.select_set(True)
bpy.context.view_layer.objects.active = parts[0]
if len(parts) > 1:
    bpy.ops.object.join()
hi = bpy.context.view_layer.objects.active
hi.name = "hi"
bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)


def tri_count(o):
    o.data.calc_loop_triangles()
    return len(o.data.loop_triangles)


hi_tris = tri_count(hi)
diag = Vector(hi.dimensions).length

# --- лоу-поли: сплошное тело + квадрик-декимация в venv проекта (tools/solidify.py), сюда — готовый PLY
import subprocess
import tempfile
tmp = tempfile.mkdtemp()
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# python проекта (pymeshlab, scipy…): сервер передаёт его в PROPGEN_PY; при ручном запуске — .venv рядом
PY = os.environ.get("PROPGEN_PY") or next((p for p in (os.path.join(root, ".venv", "Scripts", "python.exe"),
                                                       os.path.join(root, ".venv", "bin", "python")) if os.path.exists(p)), "python")
subprocess.run([PY, os.path.join(root, "tools", "solidify.py"),
                src, os.path.join(tmp, "lo.ply"), "--tris", str(TRIS), "--res", str(opt("--res", 256, int)),
                "--slice-fill", str(opt("--slice-fill", 1, int)), "--dense", os.path.join(tmp, "dense.ply"),
                "--dense-smooth", str(opt("--dense-smooth", 20, int))], check=True)
# Источник нормалей и AO: сглаженное сплошное тело (по умолчанию) или сама хай-поли генератора (--nrm-src hi —
# переносит в normal map полосы воксельной решётки и дрожь ремеша, на гладких гранях это «каша»).
NRM_SRC = argv[argv.index("--nrm-src") + 1] if "--nrm-src" in argv else "dense"
nsrc = hi
if NRM_SRC == "dense":
    bpy.ops.wm.ply_import(filepath=os.path.join(tmp, "dense.ply"), forward_axis="Y", up_axis="Z")
    nsrc = bpy.context.selected_objects[0]
    nsrc.name = "nrm_src"
    bpy.ops.object.shade_smooth()
bpy.ops.wm.ply_import(filepath=os.path.join(tmp, "lo.ply"), forward_axis="Y", up_axis="Z")
lo = bpy.context.selected_objects[0]
lo.name = "lo"
ROT = opt("--rot", 0.0)  # генератор сам выбирает, где у предмета перед — поворот вокруг вертикали, градусы
if ROT:
    for o in {hi, lo, nsrc}:
        o.rotation_euler[2] += math.radians(ROT)
    bpy.ops.object.select_all(action="DESELECT")
    for o in (hi, lo, nsrc):
        o.select_set(True)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
# на всякий случай — нормали наружу: развёрнутая грань — тогда луч запекания уходит наружу и пиксель пропадает
bpy.context.view_layer.objects.active = lo
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.mesh.normals_make_consistent(inside=False)
bpy.ops.object.mode_set(mode="OBJECT")
lo_tris = tri_count(lo)

bpy.ops.object.select_all(action="DESELECT")
lo.select_set(True)
bpy.context.view_layer.objects.active = lo
bpy.ops.object.shade_smooth_by_angle(angle=math.radians(CREASE))
# модификатор Smooth by Angle (Blender 4.1+) применяем, чтобы рёбра ушли в экспорт
for m in list(lo.modifiers):
    bpy.ops.object.modifier_apply(modifier=m.name)

bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project(angle_limit=math.radians(60), island_margin=0.01, area_weight=0.0, scale_to_bounds=False)
bpy.ops.uv.pack_islands(margin=4 / TEX, rotate=True)
bpy.ops.object.mode_set(mode="OBJECT")

# --- материал лоу-поли с целевыми картинками
def new_img(suffix, non_color=False, float_buffer=False):
    im = bpy.data.images.new(f"{name}_lo_{suffix}", TEX, TEX, alpha=False, float_buffer=float_buffer)
    if non_color:
        im.colorspace_settings.name = "Non-Color"
    return im


# нормали — во float: в 8 битах плавный наклон на крупной грани превращается в ступени, а скользящий свет — в кольца
img_base, img_nrm, img_ao = new_img("base"), new_img("normal", True, True), new_img("ao", True)
mat = bpy.data.materials.new(f"{name}_lo")
mat.use_nodes = True
nt = mat.node_tree
bsdf = nt.nodes["Principled BSDF"]
bsdf.inputs["Roughness"].default_value = 0.6
n_base = nt.nodes.new("ShaderNodeTexImage"); n_base.image = img_base
n_nrm = nt.nodes.new("ShaderNodeTexImage"); n_nrm.image = img_nrm
n_ao = nt.nodes.new("ShaderNodeTexImage"); n_ao.image = img_ao
lo.data.materials.append(mat)

scene = bpy.context.scene
scene.render.engine = "CYCLES"
prefs = bpy.context.preferences.addons["cycles"].preferences
for dev_type in ("OPTIX", "CUDA"):
    try:
        prefs.compute_device_type = dev_type
        prefs.get_devices()
        if any(d.type == dev_type for d in prefs.devices):
            for d in prefs.devices:
                d.use = d.type == dev_type
            scene.cycles.device = "GPU"
            break
    except TypeError:
        pass
scene.cycles.samples = 16
bake = scene.render.bake
bake.use_selected_to_active = True
bake.cage_extrusion = diag * 0.02
bake.max_ray_distance = diag * 0.06
bake.margin = 8

def do_bake(node, kind, source, **kw):
    bpy.ops.object.select_all(action="DESELECT")
    source.select_set(True)
    lo.select_set(True)
    bpy.context.view_layer.objects.active = lo
    nt.nodes.active = node
    bpy.ops.object.bake(type=kind, **kw)


do_bake(n_base, "DIFFUSE", hi, pass_filter={"COLOR"})  # цвет — только с хай-поли: у тела нет текстуры
do_bake(n_nrm, "NORMAL", nsrc, normal_space="TANGENT")
if USE_AO:
    scene.cycles.samples = 64
    # затенение в щелях (как в старых пропсах) — с гладкого тела, иначе в AO тоже попадает шум
    do_bake(n_ao, "AO", nsrc)

# цвет × AO (мягко) — в духе диффузных текстур 2004 года, где затенение запечено прямо в цвет
if USE_AO:
    b, a = list(img_base.pixels), list(img_ao.pixels)
    for i in range(0, len(b), 4):
        k = 0.35 + 0.65 * a[i]
        b[i] *= k; b[i + 1] *= k; b[i + 2] *= k
    img_base.pixels = b
REFS = argv[argv.index("--ref") + 1] if "--ref" in argv else ""
import numpy as np
px = np.empty(len(img_nrm.pixels), np.float32)
img_nrm.pixels.foreach_get(px)
rng = np.random.default_rng(0)
px[0::4] += (rng.random(px.size // 4) - 0.5) / 255  # дизеринг перед записью в 8-битный PNG: ступени → незаметный шум
px[1::4] += (rng.random(px.size // 4) - 0.5) / 255
px[2::4] += (rng.random(px.size // 4) - 0.5) / 255
img_nrm.pixels.foreach_set(np.clip(px, 0, 1))
for im in (img_base, img_nrm, img_ao):
    px = list(im.pixels)
    px[3::4] = [1.0] * (len(px) // 4)  # непрозрачно: иначе glTF-материал уходит в режим смешивания
    im.pixels = px
    im.filepath_raw = os.path.join(out_dir, im.name + ".png")
    im.file_format = "PNG"
    if im.is_float:
        im.use_half_precision = False
        bpy.context.scene.render.image_settings.color_depth = "8"
    im.save()
if REFS:  # цвет — к исходным видам (генератор уводит оттенок), затем перечитать для экспорта
    subprocess.run([PY, os.path.join(root, "tools", "colormatch.py"),
                    img_base.filepath_raw, REFS], check=True)
    img_base.reload()

# материал для экспорта: цвет + normal map
nt.links.new(n_base.outputs["Color"], bsdf.inputs["Base Color"])
nmap = nt.nodes.new("ShaderNodeNormalMap")
nt.links.new(n_nrm.outputs["Color"], nmap.inputs["Color"])
nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
nt.nodes.remove(n_ao)

bpy.ops.object.select_all(action="DESELECT")
lo.select_set(True)
out = os.path.join(out_dir, f"{name}_lo.glb")
bpy.ops.export_scene.gltf(filepath=out, use_selection=True, export_format="GLB", export_image_format="AUTO")
print(f"LOWPOLY hi={hi_tris} lo={lo_tris} tex={TEX} -> {out}")
