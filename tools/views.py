"""Проекции пропа: фото → чистый фронт → развороты (для режима «4 вида») одним графом ComfyUI.

Движок фронта: qwen — Qwen-Image-Edit-2511 Q4 GGUF + Lightning 4 шага (качество, держит надписи; 13 ГБ — на 8 ГБ
видеопамяти часть весов подкачивается, ~70 с); klein — FLUX.2 Klein 4B fp8 (4 ГБ — целиком в видеопамяти, 4 шага, быстро).
Развороты — всегда Qwen + LoRA Multiple-Angles (под Klein такой LoRA нет).
t2i — режим «по тексту»: тот же Klein, но без опорной картинки — рисует предмет с нуля по готовому промпту (--prompt),
для простых вещей (паллеты, ящики, бочки), которые edit-модель тащит с фото вместе с грязью, перспективой и соседями.
krea2 — тот же режим на Krea-2 Turbo (8 шагов, 768 px): GGUF Q4 (группа krea в doctor) или любые её веса из ComfyUI.

python tools/views.py --img crop.png --out views --desc "yellow enamel kettle on a white hotplate"
    [--engine qwen|klein] [--wear keep|clean] [--seed 1] [--size 768] [--views left,back,right] [--front path.png]
python tools/views.py --engine t2i --prompt "a stack of five wooden pallets ..." --views "" --out views

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
# Пропы живут износом: без этого «предметная съёмка» превращает дачный чайник и ржавую стелу в новенькие.
WEAR = {"keep": " Keep the object's condition exactly as it is in the photo: wear, rust, scratches and dirt that are visible "
                "stay as they are; do not add any new damage and do not make it look new.",
        "clean": " Show the object in clean, like-new condition."}

ap = argparse.ArgumentParser()
ap.add_argument("--img", action="append", default=[], help="фото предмета (можно несколько — до 3 референсов)")
ap.add_argument("--front", default="", help="готовый чистый фронт вместо этапа чистки")
ap.add_argument("--desc", default="object")
ap.add_argument("--out", required=True)
ap.add_argument("--engine", default="qwen", choices=["qwen", "klein", "t2i", "krea2"])
ap.add_argument("--prompt", default="", help="t2i/krea2: полный промпт как есть (без CLEAN_PROMPT и износа)")
ap.add_argument("--wear", default="keep", choices=["keep", "clean"])
ap.add_argument("--seed", type=int, default=1)
ap.add_argument("--size", type=int, default=768, help="сторона вида; для 3D хватает 768 (TRELLIS смотрит на 518)")
ap.add_argument("--views", default="left,back,right")
ap.add_argument("--angles", type=float, default=1.0, help="сила LoRA Multiple-Angles")
ap.add_argument("--no-level", action="store_true", help="не выравнивать фронт перед разворотами")
ap.add_argument("--tag", default="propgen")
a = ap.parse_args()
if a.engine in ("t2i", "krea2") and not a.front and not a.prompt.strip():
    ap.error(f"--engine {a.engine} требует --prompt")

views = [x for x in a.views.split(",") if x]
g = comfy.Graph()
_qwen = {}


def qwen():
    """Узлы Qwen-Image-Edit — только если нужны (фронт на Qwen или развороты): иначе граф не грузит 13 ГБ зря."""
    if not _qwen:
        unet = g.node("UnetLoaderGGUF", unet_name="qwen-image-edit-2511-Q4_K_M.gguf")
        clip = g.node("CLIPLoader", clip_name="qwen_2.5_vl_7b_fp8_scaled.safetensors", type="qwen_image", device="default")
        vae = g.node("VAELoader", vae_name="qwen_image_vae.safetensors")
        base = g.node("ModelSamplingAuraFlow", model=unet, shift=3.1)
        base = g.node("CFGNorm", model=base, strength=1.0)
        base = g.node("LoraLoaderModelOnly", model=base, lora_name="Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors",
                      strength_model=1.0)
        _qwen.update(clip=clip, vae=vae, base=base,
                     latent=g.node("EmptySD3LatentImage", width=a.size, height=a.size, batch_size=1))
    return _qwen


def qwen_edit(prompt, images, seed, name, angled=False):
    q = qwen()
    model = q["base"]
    if angled:
        if "angled" not in q:
            q["angled"] = g.node("LoraLoaderModelOnly", model=q["base"], strength_model=a.angles,
                                 lora_name="qwen-image-edit-2511-multiple-angles-lora.safetensors")
        model = q["angled"]
    kw = {f"image{i + 1}": g.node("FluxKontextImageScale", image=im) for i, im in enumerate(images)}
    pos = g.node("TextEncodeQwenImageEditPlus", clip=q["clip"], vae=q["vae"], prompt=prompt, **kw)
    pos = g.node("FluxKontextMultiReferenceLatentMethod", conditioning=pos, reference_latents_method="index_timestep_zero")
    # cfg = 1: негатив не участвует в выборке — не гоняем ради него 7B-энкодер по картинке ещё раз
    neg = g.node("ConditioningZeroOut", conditioning=pos)
    s = g.node("KSampler", model=model, positive=pos, negative=neg, latent_image=q["latent"], seed=seed, steps=4, cfg=1.0,
               sampler_name="euler", scheduler="simple", denoise=1.0)
    dec = g.node("VAEDecode", samples=s, vae=q["vae"])
    g.node("SaveImage", images=dec, filename_prefix=f"{a.tag}/{name}")
    return dec


def klein_edit(prompt, images, seed, name):
    """FLUX.2 Klein 4B distilled — как в шаблоне ComfyUI: опорные картинки через ReferenceLatent, 4 шага, cfg 1.
    Без картинок — ровно шаблон text-to-image (image_flux2_klein_text_to_image, Distilled): режим t2i."""
    unet = g.node("UNETLoader", unet_name="flux-2-klein-4b-fp8.safetensors", weight_dtype="default")
    clip = g.node("CLIPLoader", clip_name="qwen_3_4b.safetensors", type="flux2", device="default")
    vae = g.node("VAELoader", vae_name="flux2-vae.safetensors")
    pos = g.node("CLIPTextEncode", clip=clip, text=prompt)
    neg = g.node("ConditioningZeroOut", conditioning=pos)
    for im in images:
        scaled = g.node("ImageScaleToTotalPixels", image=im, upscale_method="lanczos", megapixels=a.size * a.size / 1e6,
                        resolution_steps=1)
        ref = g.node("VAEEncode", pixels=scaled, vae=vae)
        pos = g.node("ReferenceLatent", conditioning=pos, latent=ref)
        neg = g.node("ReferenceLatent", conditioning=neg, latent=ref)
    guider = g.node("CFGGuider", model=unet, positive=pos, negative=neg, cfg=1.0)
    s = g.node("SamplerCustomAdvanced", noise=g.node("RandomNoise", noise_seed=seed), guider=guider,
               sampler=g.node("KSamplerSelect", sampler_name="euler"),
               sigmas=g.node("Flux2Scheduler", steps=4, width=a.size, height=a.size),
               latent_image=g.node("EmptyFlux2LatentImage", width=a.size, height=a.size, batch_size=1))
    dec = g.node("VAEDecode", samples=s[0], vae=vae)
    g.node("SaveImage", images=dec, filename_prefix=f"{a.tag}/{name}")
    return dec


def krea2_t2i(prompt, seed, name):
    """Krea-2 Turbo — ядро шаблона ComfyUI «Text to Image (Krea-2 Turbo)»: 8 шагов, cfg 1, euler/simple.
    Встроенное «улучшение промпта» (TextGenerate) не берём — исчерпывающий промпт уже написала текстовая модель.
    Вариант весов (fp8, nvfp4, int8…) — какой нашёлся в ComfyUI."""
    import doctor  # noqa: PLC0415 — нужен только этому движку
    f = doctor.krea_files()
    if not f:
        sys.exit("Krea-2 не найдена в ComfyUI: скачай группу «krea» (Настройки → Проверка системы или "
                 "tools/doctor.py download --group krea) или положи свои веса krea2_turbo_* в diffusion_models")
    print("krea2:", f["unet"], f["clip"], flush=True)
    # 0,5 Мп (728 px) — как в рабочем воркфлоу пользователя (паллеты вышли чисто); 1 Мп на 8 ГБ заметно дольше
    size = a.size // 8 * 8
    unet = (g.node("UnetLoaderGGUF", unet_name=f["unet"]) if f["unet"].endswith(".gguf")
            else g.node("UNETLoader", unet_name=f["unet"], weight_dtype="default"))
    clip = g.node("CLIPLoader", clip_name=f["clip"], type="krea2", device="default")
    vae = g.node("VAELoader", vae_name=f["vae"])
    pos = g.node("CLIPTextEncode", clip=clip, text=prompt)
    neg = g.node("ConditioningZeroOut", conditioning=pos)
    s = g.node("KSampler", model=unet, positive=pos, negative=neg, seed=seed, steps=8, cfg=1.0, sampler_name="euler",
               scheduler="simple", denoise=1.0, latent_image=g.node("EmptyLatentImage", width=size, height=size, batch_size=1))
    dec = g.node("VAEDecode", samples=s, vae=vae)
    g.node("SaveImage", images=dec, filename_prefix=f"{a.tag}/{name}")
    return dec


def load(path):
    return g.node("LoadImage", image=comfy.upload_image(path, f"{a.tag}_{os.path.basename(path)}".replace("/", "_")))[0]


photos = [load(p) for p in a.img]
if a.front:
    front = load(a.front)
else:
    # для разворотов фронт ещё выравнивается — тогда «сырой» чистый вид сохраняется отдельно
    level = bool(views) and not a.no_level
    name = "front_clean" if level else "front"
    if a.engine == "t2i":
        # кадр и износ описаны в самом промпте; фото не подаём — иначе это снова edit с его перспективой и фоном
        front = klein_edit(a.prompt.strip(), [], a.seed, name)
    elif a.engine == "krea2":
        front = krea2_t2i(a.prompt.strip(), a.seed, name)
    else:
        prompt = CLEAN_PROMPT.format(desc=a.desc) + WEAR[a.wear]
        front = (klein_edit if a.engine == "klein" else qwen_edit)(prompt, photos, a.seed, name)
    if level:
        # Фото часто снято сверху и вбок, и «прямо спереди» в промпте модель не слушает. LoRA разворотов
        # обучена ставить камеру — ею приводим фронт к виду спереди на уровне глаз: от него Pixal3D ждёт ровно 90°.
        front = qwen_edit("<sks> front view eye-level shot medium shot", [front], a.seed + 5, "front", angled=True)
for i, v in enumerate(views):
    # опора — только выровненный фронт: исходное фото вторым референсом протаскивает в виды фон и чужие предметы
    qwen_edit(ANGLE_PROMPTS[v], [front], a.seed + 10 * (i + 1), v, angled=True)

entry = comfy.run(g, timeout=6 * 3600)
os.makedirs(a.out, exist_ok=True)
for f in comfy.collect(entry, a.out + "/_raw"):
    name = os.path.basename(f).split("_", 1)[1].rsplit("_", 2)[0]  # "<node>_<name>_00001_.png" → name
    dst = os.path.join(a.out, name + ".png")
    shutil.copy(f, dst)
    print(dst)
if a.front:
    shutil.copy(a.front, os.path.join(a.out, "front.png"))
