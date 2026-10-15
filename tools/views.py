"""Проекции пропа: фото → чистый фронт (Qwen-Image-Edit-2511) → развороты (LoRA Multiple-Angles) одной загрузкой модели.

python tools/views.py --img work/kettle_crop.png --out work/kettle/views --desc "yellow enamel kettle standing on a white electric hotplate"
    [--seed 1] [--size 768] [--views left,back,right] [--front path.png]  # --front: свой фронт, этап чистки пропускается

Результат: front.png, left.png, back.png, right.png (имена по Pixal3D: left — вид на левый бок самого предмета).
"""
import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(__file__))
import comfy  # noqa: E402

# Подписи LoRA Multiple-Angles. Какой из side view окажется «левым» по Pixal3D — проверяем глазами после генерации.
ANGLE_PROMPTS = {
    "left": "<sks> left side view eye-level shot medium shot",
    "back": "<sks> back view eye-level shot medium shot",
    "right": "<sks> right side view eye-level shot medium shot",
}

CLEAN_PROMPT = ("Turn this photo into a clean product shot of the {desc}. The whole object is fully visible and centered "
                "with some margin, straight front view at eye level, plain pure white background, soft even studio "
                "lighting, no cast shadows, no strong reflections, keep the exact shape, proportions, colors and decals.")

ap = argparse.ArgumentParser()
ap.add_argument("--img", action="append", default=[], help="фото предмета (можно несколько — до 3 референсов)")
ap.add_argument("--front", default="", help="готовый чистый фронт вместо этапа чистки")
ap.add_argument("--desc", default="object")
ap.add_argument("--out", required=True)
ap.add_argument("--seed", type=int, default=1)
ap.add_argument("--size", type=int, default=768, help="сторона вида; для 3D хватает 768 (TRELLIS смотрит на 518)")
ap.add_argument("--views", default="left,back,right")
ap.add_argument("--angles", type=float, default=0.9, help="сила LoRA Multiple-Angles")
ap.add_argument("--tag", default="propgen")
a = ap.parse_args()

g = comfy.Graph()
unet = g.node("UnetLoaderGGUF", unet_name="qwen-image-edit-2511-Q4_K_M.gguf")
clip = g.node("CLIPLoader", clip_name="qwen_2.5_vl_7b_fp8_scaled.safetensors", type="qwen_image", device="default")
vae = g.node("VAELoader", vae_name="qwen_image_vae.safetensors")
base = g.node("ModelSamplingAuraFlow", model=unet, shift=3.1)
base = g.node("CFGNorm", model=base, strength=1.0)
base = g.node("LoraLoaderModelOnly", model=base, lora_name="Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors",
              strength_model=1.0)
angled = g.node("LoraLoaderModelOnly", model=base, lora_name="qwen-image-edit-2511-multiple-angles-lora.safetensors",
                strength_model=a.angles)
latent = g.node("EmptySD3LatentImage", width=a.size, height=a.size, batch_size=1)


def edit(model, prompt, images, seed, name):
    kw = {f"image{i + 1}": im for i, im in enumerate(images)}
    pos = g.node("TextEncodeQwenImageEditPlus", clip=clip, vae=vae, prompt=prompt, **kw)
    pos = g.node("FluxKontextMultiReferenceLatentMethod", conditioning=pos, reference_latents_method="index_timestep_zero")
    # cfg = 1: негатив не участвует в выборке — не гоняем ради него 7B-энкодер по картинке ещё раз
    neg = g.node("ConditioningZeroOut", conditioning=pos)
    s = g.node("KSampler", model=model, positive=pos, negative=neg, latent_image=latent, seed=seed, steps=4, cfg=1.0,
               sampler_name="euler", scheduler="simple", denoise=1.0)
    dec = g.node("VAEDecode", samples=s, vae=vae)
    g.node("SaveImage", images=dec, filename_prefix=f"{a.tag}/{name}")
    return dec


def load(path):
    li = g.node("LoadImage", image=comfy.upload_image(path, f"{a.tag}_{os.path.basename(path)}".replace("/", "_")))
    return g.node("FluxKontextImageScale", image=li)


if a.front:
    front = load(a.front)
else:
    front = edit(base, CLEAN_PROMPT.format(desc=a.desc), [load(p) for p in a.img], a.seed, "front")
for i, v in enumerate(x for x in a.views.split(",") if x):
    edit(angled, ANGLE_PROMPTS[v], [front], a.seed + 10 * (i + 1), v)

entry = comfy.run(g, timeout=6 * 3600)
os.makedirs(a.out, exist_ok=True)
for f in comfy.collect(entry, a.out + "/_raw"):
    name = os.path.basename(f).split("_", 1)[1].rsplit("_", 2)[0]  # "<node>_<name>_00001_.png" → name
    dst = os.path.join(a.out, name + ".png")
    shutil.copy(f, dst)
    print(dst)
if a.front:
    shutil.copy(a.front, os.path.join(a.out, "front.png"))
