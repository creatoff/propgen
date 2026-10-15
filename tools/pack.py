"""Архив пропа для движков: структура и имена как у Poly Haven (её понимают Blender, Unity, Unreal, Godot).

<slug>/
  <slug>.glb · <slug>.gltf + .bin · <slug>.fbx · <slug>.obj + .mtl
  textures/<slug>_diff_1k.png · _nor_gl_1k.png · _nor_dx_1k.png · _ao_1k.png
  preview.png · info.json · README.txt
  source/  — по желанию: хай-поли, фото, кадр, чистый фронт
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
              ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u", "f",
               "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"]))
TEX_LABEL = {256: "256", 512: "512", 1024: "1k", 2048: "2k", 4096: "4k"}

README = """{name}
{line}

Сделано в propgen: фото → чистый фронт (Qwen-Image-Edit) → 3D (TRELLIS.2) → лоу-поли (Blender).
Треугольников: {tris}. Размер (м): {size}. Текстуры {res}×{res}.

Файлы
  {slug}.glb            glTF одним файлом — Blender, Godot, three.js, Unity (glTFast), Unreal (Interchange)
  {slug}.gltf + .bin    glTF с текстурами из textures/
  {slug}.fbx            Unity, Unreal, 3ds Max, Maya (текстуры — из textures/)
  {slug}.obj + .mtl     универсальный (текстуры — из textures/)

Текстуры (textures/)
  *_diff_*    цвет; затенение в щелях уже запечено в цвет — как у пропов 2000-х
  *_nor_gl_*  нормали OpenGL (Y+): Blender, Unity, Godot, glTF
  *_nor_dx_*  нормали DirectX (Y−): Unreal, Source, 3ds Max
  *_ao_*      ambient occlusion отдельно — если нужен чистый цвет без затенения, бери его вместо запечённого
Шероховатость постоянная 0.6, металличность 0.

1 единица = 1 метр, низ модели — на полу (Y = 0 в glTF, Z = 0 в Blender), по центру.
"""


def slugify(name):
    s = "".join(TR.get(c, c) for c in (name or "").lower())
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s[:48] or "prop"


def build(pdir, meta, height_cm=0.0, sources=False, log=None):
    """pdir — папка пропа. Возвращает путь к zip и сводку."""
    p = meta["params"]
    slug = slugify(meta.get("name"))
    lab = TEX_LABEL.get(int(p.get("tex", 1024)), str(p.get("tex", 1024)))
    lo = os.path.join(pdir, "lo")
    if not os.path.exists(os.path.join(lo, "prop_lo.glb")):
        raise RuntimeError("сначала нужен лоу-поли")
    work = os.path.join(pdir, "export")
    shutil.rmtree(work, ignore_errors=True)
    out = os.path.join(work, slug)
    tex = os.path.join(out, "textures")
    os.makedirs(tex)

    # текстуры под итоговыми именами; нормаль DirectX — переворот зелёного канала
    names = {k: os.path.join(tex, f"{slug}_{k}_{lab}.png") for k in ("diff", "nor_gl", "nor_dx", "ao")}
    shutil.copy(os.path.join(lo, "prop_lo_base.png"), names["diff"])
    shutil.copy(os.path.join(lo, "prop_lo_normal.png"), names["nor_gl"])
    r, g, b = Image.open(names["nor_gl"]).convert("RGB").split()
    Image.merge("RGB", (r, g.point(lambda v: 255 - v), b)).save(names["nor_dx"])
    if os.path.exists(os.path.join(lo, "prop_lo_ao.png")):
        shutil.copy(os.path.join(lo, "prop_lo_ao.png"), names["ao"])
    else:
        names.pop("ao")

    blender = config.find_blender()
    if not blender:
        raise RuntimeError("Blender не найден — укажи путь в настройках")
    cmd = [blender, "-b", "--python", os.path.join(config.ROOT, "tools", "export.py"), "--",
           os.path.join(lo, "prop_lo.glb"), out, slug, names["diff"], names["nor_gl"]]
    if height_cm:
        cmd += ["--height-cm", str(height_cm)]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if log:
        log(res.stdout[-4000:] + res.stderr[-2000:])
    m = re.search(r"EXPORT tris=(\d+) size_m=([\d.]+)x([\d.]+)x([\d.]+)", res.stdout)
    if res.returncode or not m:
        raise RuntimeError("экспорт в Blender не удался: " + (res.stderr or res.stdout)[-600:])
    tris = int(m[1])
    size = [round(float(m[i]), 3) for i in (2, 3, 4)]  # x, y (глубина), z (высота) — оси Blender

    if os.path.exists(os.path.join(lo, "sheet.png")):
        shutil.copy(os.path.join(lo, "sheet.png"), os.path.join(out, "preview.png"))
    info = {"name": meta.get("name"), "slug": slug, "notes": meta.get("notes"), "prompt": meta.get("desc"),
            "triangles": tris, "size_m": {"width": size[0], "depth": size[1], "height": size[2]},
            "texture_resolution": int(p.get("tex", 1024)), "normal_map": {"opengl": f"textures/{slug}_nor_gl_{lab}.png",
                                                                          "directx": f"textures/{slug}_nor_dx_{lab}.png"},
            "units": "meters, Y-up (glTF), origin at bottom center", "params": p,
            "exported": time.strftime("%Y-%m-%d %H:%M"), "tool": "propgen"}
    with open(os.path.join(out, "info.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    with open(os.path.join(out, "README.txt"), "w", encoding="utf-8") as f:
        name = meta.get("name") or slug
        f.write(README.format(name=name, line="=" * len(name), tris=tris, slug=slug, res=int(p.get("tex", 1024)),
                              size=f"{size[0]} × {size[1]} × {size[2]} (ш × г × в)"))

    if sources:
        src = os.path.join(out, "source")
        os.makedirs(src)
        for rel, dst in (("mesh/prop_hi.glb", f"{slug}_highpoly.glb"), ("crop.png", "crop.png"),
                         ("views/front.png", "front.png")):
            if os.path.exists(os.path.join(pdir, rel)):
                shutil.copy(os.path.join(pdir, rel), os.path.join(src, dst))
        for i, ph in enumerate(meta.get("photos", [])):
            shutil.copy(os.path.join(pdir, ph), os.path.join(src, f"photo_{i + 1}.jpg"))

    zpath = os.path.join(work, f"{slug}.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(out):
            for fn in sorted(files):
                full = os.path.join(root, fn)
                z.write(full, os.path.relpath(full, work))
    shutil.rmtree(out)
    return zpath, {"slug": slug, "triangles": tris, "size_m": info["size_m"], "bytes": os.path.getsize(zpath)}
