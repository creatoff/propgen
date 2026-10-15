"""Проекции → 3D (Pixal3D / TRELLIS.2, ComfyUI) → хай-поли с текстурой. Лоу-поли — tools/lowpoly.py (Blender).

Multiview (Pixal3D):  python tools/mesh.py --views work/kettle/views --out work/kettle/mesh
Одно фото:            python tools/mesh.py --img work/kettle_crop.png --model pixal3d|trellis2 --out ...

Результат: <name>_hi.glb (150k граней, текстура 2048), <name>_raw.glb (сырой меш генератора), <name>_in_*.png (входы).
Своя лоу-поли-ветка в Comfy (DecimateMesh qem → 1500) не годится: сетка схлопывается до десятков треугольников.
"""
import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(__file__))
import comfy  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--views", default="", help="папка с front/left/back/right.png (какие есть)")
ap.add_argument("--img", default="", help="одно фото вместо видов")
ap.add_argument("--model", default="pixal3d", help="для --img: pixal3d или trellis2")
ap.add_argument("--out", required=True)
ap.add_argument("--name", default="prop")
ap.add_argument("--fov", type=float, default=20.0, help="FOV видов для multiview")
ap.add_argument("--upsample", type=int, default=1024)
ap.add_argument("--hi-faces", type=int, default=150000)
ap.add_argument("--seed", type=int, default=42)
ap.add_argument("--tag", default="propgen")
a = ap.parse_args()

g = comfy.Graph()
bgm = g.node("LoadBackgroundRemovalModel", bg_removal_name="birefnet.safetensors")
cv = g.node("CLIPVisionLoader", clip_name="dino_v3_L_naf_fp32.safetensors")


def prep(path):
    """Фото/вид → вырезка фона → квадрат 1024 с запасом 1.1 на чёрном (как кадрирует риг Pixal3D)."""
    img = g.node("LoadImage", image=comfy.upload_image(path, f"{a.tag}_{a.name}_{os.path.basename(path)}".replace("/", "_")))
    mask = g.node("RemoveBackground", bg_removal_model=bgm, image=img[0])
    crop = g.node("ImageCropToMask", images=img[0], masks=mask, width=1024, height=1024, pad_factor=1.1, grow_mask=0,
                  background="#000000")
    g.node("SaveImage", images=crop, filename_prefix=f"{a.tag}/{a.name}_in_{os.path.splitext(os.path.basename(path))[0]}")
    return crop


if a.views:
    views = {v: prep(os.path.join(a.views, v + ".png")) for v in ("front", "left", "back", "right")
             if os.path.exists(os.path.join(a.views, v + ".png"))}
    print("views:", ", ".join(views), flush=True)
    cond = g.node("Pixal3DMultiViewConditioning", clip_vision_model=cv, fov=a.fov, **views)
    unet = g.node("UNETLoader", unet_name="pixal3d_multiview_int8_convrot.safetensors", weight_dtype="default")
elif a.model == "pixal3d":
    crop = prep(a.img)
    moge = g.node("LoadMoGeModel", model_name="moge_2_vitl_normal_fp16.safetensors")
    geo = g.node("MoGeInference", moge_model=moge, image=crop, resolution_level=9, fov_x_degrees=0.0, batch_size=4,
                 force_projection=True, apply_mask=True, refine_steps=0)
    fov = g.node("MoGeGeometryToFOV", moge_geometry=geo, axis="horizontal", unit="degrees")
    cond = g.node("Pixal3DConditioning", clip_vision_model=cv, image=crop, camera_angle_x=fov[0])
    unet = g.node("UNETLoader", unet_name="pixal3d_int8_convrot.safetensors", weight_dtype="default")
else:
    cond = g.node("Trellis2Conditioning", clip_vision_model=cv, image=prep(a.img))
    unet = g.node("UNETLoader", unet_name="trellis_2_int8_convrot.safetensors", weight_dtype="default")

vae_s = g.node("VAELoader", vae_name="trellis_2_shape_vae_bf16.safetensors")
vae_t = g.node("VAELoader", vae_name="trellis_2_texture_vae_bf16.safetensors")
# Генерация — параметры официального шаблона (CFG override + rescale CFG как в исходном пайплайне).
m_struct = g.node("ModelSamplingSD3", model=g.node("RescaleCFG", model=g.node(
    "CFGOverride", model=unet, cfg=1, start_percent=0.667, end_percent=1), multiplier=0.7), shift=5)
s1 = g.node("KSampler", model=m_struct, positive=cond[0], negative=cond[1],
            latent_image=g.node("EmptyTrellis2LatentStructure", batch_size=1), seed=a.seed + 14, steps=12, cfg=7.5,
            sampler_name="euler", scheduler="normal", denoise=1)
vox = g.node("VaeDecodeStructureTrellis2", samples=s1, vae=vae_s, resolution="32")
m_shape = g.node("RescaleCFG", model=g.node("CFGOverride", model=unet, cfg=1, start_percent=0.769, end_percent=1),
                 multiplier=0.5)
ss = g.node("Trellis2ShapeStage", positive=cond[0], negative=cond[1], voxel=vox)
s2 = g.node("KSampler", model=m_shape, positive=ss[0], negative=ss[1], latent_image=ss[2], seed=a.seed, steps=20,
            cfg=7.5, sampler_name="euler", scheduler="normal", denoise=1)
up = g.node("Trellis2UpsampleStage", positive=ss[0], negative=ss[1], shape_latent=s2, vae=vae_s,
            target_resolution=str(a.upsample))
s3 = g.node("KSampler", model=m_shape, positive=up[0], negative=up[1], latent_image=up[2], seed=a.seed, steps=12,
            cfg=7.5, sampler_name="euler", scheduler="simple", denoise=1)
shape = g.node("VaeDecodeShapeTrellis", samples=s3, vae=vae_s)
ts = g.node("Trellis2TextureStage", positive=up[0], negative=up[1], shape_latent=s3)
s4 = g.node("KSampler", model=unet, positive=ts[0], negative=ts[1], latent_image=ts[2], seed=a.seed + 1, steps=12,
            cfg=1, sampler_name="euler", scheduler="normal", denoise=1)
vcol = g.node("VaeDecodeTextureTrellis", samples=s4, vae=vae_t, shape_subdivides=shape[1])
g.node("SaveGLB", mesh=shape[0], filename_prefix=f"{a.tag}/{a.name}_raw")

# Хай-поли: чистая сетка, текстура 2048.
rm = g.node("RemeshMesh", mesh=shape[0], resolution=512, sign_mode="udf", sign_mode__qef=False,
            sign_mode__drop_inverted_components=True, sign_mode__drop_enclosed_components=True, band=1, project_back=0,
            fix_poles=True, smooth_iters=3, drop_small_components=0.01, precluster_max_verts=20000000)
hi = g.node("DecimateMesh", mesh=rm, target_face_count=a.hi_faces, placement_mode="midpoint")
hi_uv = g.node("UnwrapMesh", mesh=g.node("MeshSmoothNormals", mesh=hi, crease_angle=180), segmenter="pec",
               resolution=2048, padding=2, weld_distance=0.0002)
hi_bake = g.node("BakeTextureFromVoxel", mesh=hi_uv, voxel_colors=vcol, texture_size=2048, reference_mesh=shape[0])
g.node("SaveGLB", mesh=g.node("ApplyTextureToMesh", mesh=hi_uv, base_color=hi_bake[0]), filename_prefix=f"{a.tag}/{a.name}_hi")

entry = comfy.run(g, timeout=3 * 3600)
os.makedirs(a.out, exist_ok=True)
for f in comfy.collect(entry, os.path.join(a.out, "_raw")):
    base = os.path.basename(f).split("_", 1)[1]                      # "<node>_<file>"
    stem, ext = os.path.splitext(base)
    stem = stem.rstrip("_").rsplit("_", 1)[0] if stem.rstrip("_")[-5:].isdigit() else stem
    dst = os.path.join(a.out, stem + ext)
    shutil.copy(f, dst)
    print(dst)
