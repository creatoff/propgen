"""Проверка окружения propgen и докачка моделей. Нужен запущенный ComfyUI: списки нод, моделей и папок моделей
берём у него через API — так неважно, куда и как он установлен (Desktop, portable, git, extra_model_paths).

python tools/doctor.py              # отчёт
python tools/doctor.py --json       # отчёт для скриптов
python tools/doctor.py download [--group core,multiview,pixal3d,hdri] [--dir D:/models]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

HF = "https://huggingface.co/{repo}/resolve/main/{path}"

# Модели: папка ComfyUI, файл, откуда, размер (ГБ), группа. core — основной путь «фото → чистый фронт → TRELLIS.2 → лоу-поли».
MODELS = [
    # генерация видов: Qwen-Image-Edit-2511 GGUF + Lightning 4 шага
    {"folder": "diffusion_models", "file": "qwen-image-edit-2511-Q4_K_M.gguf", "loader": ("UnetLoaderGGUF", "unet_name"),
     "repo": "unsloth/Qwen-Image-Edit-2511-GGUF", "path": "qwen-image-edit-2511-Q4_K_M.gguf", "gb": 13.24, "group": "core"},
    {"folder": "text_encoders", "file": "qwen_2.5_vl_7b_fp8_scaled.safetensors", "loader": ("CLIPLoader", "clip_name"),
     "repo": "Comfy-Org/HunyuanVideo_1.5_repackaged", "path": "split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors",
     "gb": 9.38, "group": "core"},
    {"folder": "vae", "file": "qwen_image_vae.safetensors", "loader": ("VAELoader", "vae_name"),
     "repo": "Comfy-Org/Qwen-Image_ComfyUI", "path": "split_files/vae/qwen_image_vae.safetensors", "gb": 0.25, "group": "core"},
    {"folder": "loras", "file": "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors", "loader": ("LoraLoaderModelOnly", "lora_name"),
     "repo": "lightx2v/Qwen-Image-Edit-2511-Lightning", "path": "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors",
     "gb": 0.85, "group": "core"},
    # 3D: TRELLIS.2 (int8) + общие части
    {"folder": "diffusion_models", "file": "trellis_2_int8_convrot.safetensors", "loader": ("UNETLoader", "unet_name"),
     "repo": "Comfy-Org/TRELLIS.2", "path": "diffusion_models/trellis_2_int8_convrot.safetensors", "gb": 5.25, "group": "core"},
    {"folder": "vae", "file": "trellis_2_shape_vae_bf16.safetensors", "loader": ("VAELoader", "vae_name"),
     "repo": "Comfy-Org/Pixal3D", "path": "vae/trellis_2_shape_vae_bf16.safetensors", "gb": 1.10, "group": "core"},
    {"folder": "vae", "file": "trellis_2_texture_vae_bf16.safetensors", "loader": ("VAELoader", "vae_name"),
     "repo": "Comfy-Org/Pixal3D", "path": "vae/trellis_2_texture_vae_bf16.safetensors", "gb": 0.95, "group": "core"},
    {"folder": "clip_vision", "file": "dino_v3_L_naf_fp32.safetensors", "loader": ("CLIPVisionLoader", "clip_name"),
     "repo": "Comfy-Org/Pixal3D", "path": "clip_vision/dino_v3_L_naf_fp32.safetensors", "gb": 1.22, "group": "core"},
    {"folder": "background_removal", "file": "birefnet.safetensors", "loader": ("LoadBackgroundRemovalModel", "bg_removal_name"),
     "repo": "Comfy-Org/BiRefNet", "path": "background_removal/birefnet.safetensors", "gb": 0.44, "group": "core"},
    # режим «4 вида»: развороты LoRA + Pixal3D multiview
    {"folder": "loras", "file": "qwen-image-edit-2511-multiple-angles-lora.safetensors", "loader": ("LoraLoaderModelOnly", "lora_name"),
     "repo": "fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA", "path": "qwen-image-edit-2511-multiple-angles-lora.safetensors",
     "gb": 0.30, "group": "multiview"},
    {"folder": "diffusion_models", "file": "pixal3d_multiview_int8_convrot.safetensors", "loader": ("UNETLoader", "unet_name"),
     "repo": "Comfy-Org/Pixal3D", "path": "diffusion_models/pixal3d_multiview_int8_convrot.safetensors", "gb": 5.58, "group": "multiview"},
    # быстрый движок видов: FLUX.2 Klein 4B (Apache 2.0) — целиком влезает в 8 ГБ, 4 шага
    {"folder": "diffusion_models", "file": "flux-2-klein-4b-fp8.safetensors", "loader": ("UNETLoader", "unet_name"),
     "repo": "black-forest-labs/FLUX.2-klein-4b-fp8", "path": "flux-2-klein-4b-fp8.safetensors", "gb": 4.07, "group": "klein"},
    {"folder": "text_encoders", "file": "qwen_3_4b.safetensors", "loader": ("CLIPLoader", "clip_name"),
     "repo": "Comfy-Org/z_image_turbo", "path": "split_files/text_encoders/qwen_3_4b.safetensors", "gb": 8.04, "group": "klein"},
    {"folder": "vae", "file": "flux2-vae.safetensors", "loader": ("VAELoader", "vae_name"),
     "repo": "Comfy-Org/flux2-dev", "path": "split_files/vae/flux2-vae.safetensors", "gb": 0.34, "group": "klein"},
    # модель Pixal3D для одного изображения
    {"folder": "diffusion_models", "file": "pixal3d_int8_convrot.safetensors", "loader": ("UNETLoader", "unet_name"),
     "repo": "Comfy-Org/Pixal3D", "path": "diffusion_models/pixal3d_int8_convrot.safetensors", "gb": 5.6, "group": "pixal3d"},
    {"folder": "geometry_estimation", "file": "moge_2_vitl_normal_fp16.safetensors", "loader": ("LoadMoGeModel", "model_name"),
     "repo": "Comfy-Org/MoGe", "path": "geometry_estimation/moge_2_vitl_normal_fp16.safetensors", "gb": 0.66, "group": "pixal3d"},
    # Krea-2 Turbo — необязательная модель режима «по тексту». Качаем лёгкий GGUF Q4_K_M (7 ГБ, архитектура qwen_image —
    # грузится нодой ComfyUI-GGUF, почти целиком влезает в 8 ГБ); если уже лежит любой другой вариант весов — берём его.
    # alts — в порядке предпочтения; also — варианты под другим загрузчиком (официальные safetensors Comfy-Org).
    {"folder": "diffusion_models", "file": "krea2_turbo-Q4_K_M.gguf", "loader": ("UnetLoaderGGUF", "unet_name"),
     "alts": ["krea2_turbo-Q4_K_M.gguf", "krea2_turbo-Q4_K_S.gguf", "krea2_turbo-Q4_0.gguf", "krea2_turbo-Q5_K_M.gguf",
              "krea2_turbo-Q5_K_S.gguf", "krea2_turbo-Q3_K_M.gguf", "krea2_turbo-Q6_K.gguf", "krea2_turbo-Q8_0.gguf"],
     "also": (("UNETLoader", "unet_name"), ["krea2_turbo_nvfp4.safetensors", "krea2_turbo_fp8_scaled.safetensors",
                                            "krea2_turbo_int8_convrot.safetensors", "krea2_turbo_mxfp8.safetensors",
                                            "krea2_turbo_bf16.safetensors"]),
     "repo": "vantagewithai/Krea-2-Turbo-GGUF", "path": "krea2_turbo-Q4_K_M.gguf", "gb": 6.97, "group": "krea"},
    {"folder": "text_encoders", "file": "qwen3vl_4b_fp8_scaled.safetensors", "loader": ("CLIPLoader", "clip_name"),
     "alts": ["qwen3vl_4b_fp8_scaled.safetensors", "qwen3vl_4b_bf16.safetensors"],
     "repo": "Comfy-Org/Krea-2", "path": "text_encoders/qwen3vl_4b_fp8_scaled.safetensors", "gb": 5.24, "group": "krea"},
    {"folder": "vae", "file": "qwen_image_vae.safetensors", "loader": ("VAELoader", "vae_name"),
     "repo": "Comfy-Org/Krea-2", "path": "vae/qwen_image_vae.safetensors", "gb": 0.25, "group": "krea"},
]
GROUPS = {"core": "основной путь: фото → чистый фронт → TRELLIS.2 → лоу-поли",
          "multiview": "режим «4 вида» (развороты + Pixal3D multiview)",
          "klein": "быстрый движок фронта FLUX.2 Klein 4B",
          "pixal3d": "модель Pixal3D для одного изображения",
          "krea": "Krea-2 Turbo (GGUF Q4) — необязательная модель режима «по тексту»; подхватит и уже скачанные варианты",
          "hdri": "панорамы 4k для фона просмотрщика (~80 МБ)"}

HDRI = ["studio_small_09", "abandoned_factory_canteen_01", "industrial_sunset_02_puresky", "kloofendal_48d_partly_cloudy_puresky"]
HDRI_DIR = os.path.join(config.ROOT, "web", "hdri")

# Ноды ComfyUI. Всё, кроме GGUF, — в ядре свежего ComfyUI (TRELLIS.2/Pixal3D появились в ядре в 2026 г.).
NODES = {
    "UnetLoaderGGUF": "custom node ComfyUI-GGUF (city96): ComfyUI Manager → «ComfyUI-GGUF» или git clone https://github.com/city96/ComfyUI-GGUF в custom_nodes",
    "TextEncodeQwenImageEditPlus": "ядро ComfyUI — обнови ComfyUI",
    "FluxKontextMultiReferenceLatentMethod": "ядро ComfyUI — обнови ComfyUI",
    "ConditioningZeroOut": "ядро ComfyUI — обнови ComfyUI",
    "Trellis2Conditioning": "ядро ComfyUI (TRELLIS.2) — обнови ComfyUI до свежей версии",
    "Trellis2ShapeStage": "ядро ComfyUI (TRELLIS.2) — обнови ComfyUI",
    "Pixal3DMultiViewConditioning": "ядро ComfyUI (Pixal3D) — обнови ComfyUI",
    "LoadBackgroundRemovalModel": "ядро ComfyUI — обнови ComfyUI",
    "RemoveBackground": "ядро ComfyUI — обнови ComfyUI",
    "ImageCropToMask": "ядро ComfyUI — обнови ComfyUI",
    "RemeshMesh": "ядро ComfyUI (3D-ноды) — обнови ComfyUI",
    "DecimateMesh": "ядро ComfyUI (3D-ноды) — обнови ComfyUI",
    "UnwrapMesh": "ядро ComfyUI (3D-ноды) — обнови ComfyUI",
    "BakeTextureFromVoxel": "ядро ComfyUI (3D-ноды) — обнови ComfyUI",
    "SaveGLB": "ядро ComfyUI — обнови ComfyUI",
}
PY_DEPS = ["fastapi", "uvicorn", "multipart", "PIL", "numpy", "scipy", "skimage", "trimesh", "pymeshlab"]
PIP_NAME = {"multipart": "python-multipart", "PIL": "pillow", "skimage": "scikit-image"}  # имя импорта ≠ пакет pip


def _get(path, timeout=10):
    with urllib.request.urlopen(config.get("comfy_url").rstrip("/") + path, timeout=timeout) as r:
        return json.loads(r.read())


def _options(spec):
    """Список вариантов комбобокса из object_info (старый формат — список, новый — {"options": [...]})."""
    if not spec:
        return []
    first = spec[0]
    if isinstance(first, list):
        return first
    if len(spec) > 1 and isinstance(spec[1], dict):
        return spec[1].get("options") or []
    return []


def check_comfy():
    out = {"ok": False, "url": config.get("comfy_url")}
    try:
        stats = _get("/system_stats", 5)
    except Exception as e:  # noqa: BLE001
        out["error"] = f"ComfyUI не отвечает по {out['url']}: {e}"
        # частые адреса: ComfyUI Desktop по умолчанию слушает 8000, portable и git-версия — 8188
        for alt in ("http://127.0.0.1:8188", "http://127.0.0.1:8000", "http://127.0.0.1:8189"):
            if alt == out["url"].rstrip("/"):
                continue
            try:
                with urllib.request.urlopen(alt + "/system_stats", timeout=2):
                    out["found_at"] = alt
                    out["error"] += f"\nНо ComfyUI отвечает на {alt} — укажи этот адрес в настройках."
                    break
            except Exception:  # noqa: BLE001
                pass
        return out
    dev = (stats.get("devices") or [{}])[0]
    out.update(ok=True, version=stats.get("system", {}).get("comfyui_version"), gpu=dev.get("name", ""),
               vram_gb=round((dev.get("vram_total") or 0) / 2**30, 1), ram_gb=round(stats.get("system", {}).get("ram_total", 0) / 2**30, 1))
    info = _get("/object_info", 60)
    out["nodes"] = [{"name": n, "ok": n in info, "fix": fix} for n, fix in NODES.items()]
    try:
        out["folders"] = _get("/internal/folder_paths", 10)
    except Exception:  # noqa: BLE001
        out["folders"] = {}
    models = []
    opts = lambda cls, inp: _options((info.get(cls, {}).get("input", {}).get("required", {}) or {}).get(inp))  # noqa: E731
    for m in MODELS:
        found = _found(m, opts)
        models.append({**{k: m[k] for k in ("folder", "file", "gb", "group")}, "ok": bool(found),
                       "found": found, "download": m.get("download", True)})
    out["models"] = models
    return out


def _found(m, opts):
    """Имя файла модели, которое реально видит ComfyUI (с учётом вариантов весов alts/also), или None.
    opts(cls, inp) — список вариантов комбобокса загрузчика."""
    have = opts(*m["loader"])
    f = next((f for f in m.get("alts") or [m["file"]] if f in have), None)
    if not f and m.get("also"):
        loader, names = m["also"]
        have = opts(*loader)
        f = next((n for n in names if n in have), None)
    return f


def krea_files():
    """Файлы Krea-2, которые уже есть в ComfyUI: {"unet", "clip", "vae"} или None, если чего-то нет.
    Лёгкий запрос — object_info только трёх загрузчиков, а не всего ComfyUI."""
    def opts(cls, inp):
        try:
            return _options(_get(f"/object_info/{cls}", 10)[cls]["input"]["required"].get(inp))
        except Exception:  # noqa: BLE001 — нет ноды (например, ComfyUI-GGUF) или ComfyUI не отвечает
            return []
    got = {}
    for m, key in zip([m for m in MODELS if m["group"] == "krea"], ("unet", "clip", "vae")):
        got[key] = _found(m, opts)
        if not got[key]:
            return None
    return got


def check_blender():
    p = config.find_blender()
    if not p:
        return {"ok": False, "error": "Blender не найден: установи Blender 4.2+ (blender.org) или укажи путь в настройках"}
    try:
        v = subprocess.run([p, "--version"], capture_output=True, text=True, timeout=60).stdout
        m = re.search(r"Blender (\d+)\.(\d+)", v)
        ver = (int(m[1]), int(m[2])) if m else (0, 0)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "path": p, "error": f"не запускается: {e}"}
    return {"ok": ver >= (4, 2), "path": p, "version": f"{ver[0]}.{ver[1]}",
            **({} if ver >= (4, 2) else {"error": "нужен Blender 4.2 или новее"})}


def check_llm():
    mode = config.get("llm_mode")
    res = {"mode": mode, "openai_key": bool(config.openai_key()), "lms": config.find_lms()}
    try:
        with urllib.request.urlopen(config.get("lmstudio_url").rstrip("/") + "/v1/models", timeout=3) as r:
            res["lmstudio_running"] = True
    except Exception:  # noqa: BLE001
        res["lmstudio_running"] = False
    if res["lms"]:
        try:
            ls = subprocess.run([res["lms"], "ls"], capture_output=True, text=True, timeout=60).stdout
            res["local_model_ok"] = config.get("local_model").split("/")[-1].lower() in ls.lower()
        except Exception:  # noqa: BLE001
            res["local_model_ok"] = False
    else:
        res["local_model_ok"] = False
    res["ok"] = mode == "off" or (mode in ("auto", "openai") and res["openai_key"]) or \
        (mode in ("auto", "local") and bool(res["lms"]) and res["local_model_ok"])
    return res


VCREDIST = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
_PROBE = ("import importlib, sys\n"
          "for d in sys.argv[1:]:\n"
          "    print('try', d, flush=True)\n"
          "    try:\n"
          "        importlib.import_module(d)\n"
          "        print('ok', d, flush=True)\n"
          "    except Exception as e:\n"
          "        print('missing', d, type(e).__name__, flush=True)\n")


def check_python():
    """Импорт зависимостей — в отдельном процессе. Нативные модули (pymeshlab с Qt, scipy, skimage) при несовместимой
    системной DLL падают «access violation» и роняют процесс целиком: раньше так падал сервер на /api/doctor и молча
    умирал setup.cmd. Упавший модуль = последний «try» без ответа; остальные проверяем следующим запуском."""
    missing, crashed, left = [], [], list(PY_DEPS)
    while left:
        try:
            p = subprocess.run([sys.executable, "-c", _PROBE, *left], capture_output=True, text=True, timeout=180,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            crashed.append({"name": left[0], "code": "timeout"})
            left = left[1:]
            continue
        done = set()
        for line in p.stdout.splitlines():
            kind, _, rest = line.partition(" ")
            name = rest.split(" ")[0]
            if kind in ("ok", "missing"):
                done.add(name)
                if kind == "missing":
                    missing.append(name)
        left = [d for d in left if d not in done]
        if p.returncode != 0 and left:
            code = p.returncode & 0xFFFFFFFF
            crashed.append({"name": left[0], "code": f"0x{code:08X}"})
            left = left[1:]
        elif p.returncode != 0:
            break
    res = {"ok": not missing and not crashed, "missing": missing + [c["name"] for c in crashed], "crashed": crashed,
           "python": sys.executable}
    if missing and not crashed and config.INSTALLED:
        # интерфейс без fix советует setup.cmd — у установленной версии его нет, зависимости ставит установщик
        res["fix"] = (f"нет: {', '.join(missing)} — установка повреждена, поставьте propgen заново: "
                      f"https://github.com/creatoff/propgen/releases/latest")
    if crashed:
        # 0xC0000005 при импорте нативного модуля на Windows — почти всегда устаревший Visual C++ Redistributable:
        # колёса собраны новой студией, а в процесс грузится старая msvcp140.dll из System32
        names = ", ".join(c["name"] for c in crashed)
        if any(c["code"] == "0xC0000005" for c in crashed):
            res["fix"] = (f"{names} падает при импорте (0xC0000005, нарушение доступа) — установите свежий Microsoft "
                          f"Visual C++ Redistributable x64: {VCREDIST}, затем перезапустите start.cmd")
        elif config.INSTALLED:  # у установленной версии свой python\ — проще поставить propgen заново поверх
            res["fix"] = (f"{names} падает при импорте ({crashed[0]['code']}) — переустановите propgen: скачайте "
                          f"propgen-setup-*.exe со страницы https://github.com/creatoff/propgen/releases/latest")
        else:
            res["fix"] = (f"{names} падает при импорте ({crashed[0]['code']}) — переустановите: "
                          f".venv\\Scripts\\python -m pip install --force-reinstall {' '.join(PIP_NAME.get(c['name'], c['name']) for c in crashed)}")
    return res


def check_hdri():
    have = [h for h in HDRI if os.path.exists(os.path.join(HDRI_DIR, f"{h}_4k.hdr"))]
    return {"ok": len(have) == len(HDRI), "have": len(have), "total": len(HDRI)}


# ---------- шаги конвейера: экран «Проверка системы» показывает цепочку, а не свалку проверок
# Шагу хватает одного варианта из options; optional — шаг можно пропустить. needs — требования варианта (нужны все):
# "node:<класс>", "group:<группа MODELS>", "file:<группа>/<файл>", "blender", "py:<модуль>", "llm:openai|local";
# "a|b" — хватит любого из двух. Минимальный набор, без которого конвейер не едет, — в _minimum().
_QWEN_FRONT = ["file:core/qwen-image-edit-2511-Q4_K_M.gguf", "file:core/qwen_2.5_vl_7b_fp8_scaled.safetensors",
               "file:core/qwen_image_vae.safetensors", "file:core/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"]
_SHARED_3D = ["file:core/trellis_2_shape_vae_bf16.safetensors", "file:core/trellis_2_texture_vae_bf16.safetensors",
              "file:core/dino_v3_L_naf_fp32.safetensors", "file:core/birefnet.safetensors"]
STEPS = [
    {"id": "prompt", "title": "Промпт", "what": "заметка → английский промпт для генератора", "optional": True, "options": [
        {"id": "openai", "title": "OpenAI", "needs": ["llm:openai"]},
        {"id": "local", "title": "LM Studio + Gemma-3-4B", "needs": ["llm:local"]},
        {"id": "off", "title": "без модели — заметка сразу по-английски", "needs": []}]},
    {"id": "front", "title": "Фронт", "what": "фото → чистый фронт без фона", "options": [
        {"id": "qwen", "title": "Qwen-Image-Edit-2511", "needs": ["node:UnetLoaderGGUF", *_QWEN_FRONT]},
        {"id": "klein", "title": "FLUX.2 Klein 4B — быстро", "needs": ["group:klein"]},
        {"id": "text", "title": "по тексту — Klein или Krea-2", "needs": ["group:klein|group:krea"]}]},
    {"id": "multiview", "title": "4 вида", "what": "развороты предмета и 3D по четырём видам", "optional": True, "options": [
        {"id": "multiview", "title": "LoRA Multiple-Angles + Pixal3D multiview", "needs": ["group:multiview"]}]},
    {"id": "mesh", "title": "3D", "what": "фронт → хай-поли с текстурой", "options": [
        {"id": "trellis2", "title": "TRELLIS.2", "needs": ["file:core/trellis_2_int8_convrot.safetensors", *_SHARED_3D]},
        {"id": "pixal3d", "title": "Pixal3D", "needs": ["group:pixal3d", *_SHARED_3D]}]},
    {"id": "lowpoly", "title": "Лоу-поли и запекание", "what": "упрощение сетки, развёртка, карты", "options": [
        {"id": "blender", "title": "Blender + pymeshlab", "needs": ["blender", "py:pymeshlab"]}]},
    {"id": "export", "title": "Экспорт", "what": "архив glb · gltf · fbx · obj", "options": [
        {"id": "blender", "title": "Blender", "needs": ["blender"]}]},
]
_MODELS_TO_COMFY = ("node", "group", "file")


def _need(r, need):
    """Одно требование → (выполнено, чего не хватает [строки], недостающие модели [(MODELS, статус)])."""
    if "|" in need:
        alts = [_need(r, n) for n in need.split("|")]
        ok = next((a for a in alts if a[0]), None)
        # не хватает всех вариантов: показываем первый — его обычно и стоит докачать
        return ok or (False, [" или ".join(dict.fromkeys(", ".join(a[1]) for a in alts))], alts[0][2])
    kind, _, arg = need.partition(":")
    c = r["comfy"]
    if kind in _MODELS_TO_COMFY and not c["ok"]:
        return False, ["ComfyUI не отвечает"], []
    if kind == "node":
        ok = any(n["name"] == arg and n["ok"] for n in c["nodes"])
        return ok, [] if ok else [f"нода {arg}"], []
    if kind in ("group", "file"):
        grp, _, fname = arg.partition("/")
        pairs = [(m, st) for m, st in zip(MODELS, c["models"]) if m["group"] == grp and (not fname or m["file"] == fname)]
        if not pairs:
            return False, [f"нет описания моделей «{arg}»"], []
        miss = [(m, st) for m, st in pairs if not st["ok"]]
        return not miss, [m["file"] for m, _ in miss], miss
    if kind == "blender":
        return r["blender"]["ok"], [] if r["blender"]["ok"] else ["Blender 4.2+"], []
    if kind == "py":
        ok = arg not in r["python"]["missing"]
        return ok, [] if ok else [f"Python-модуль {arg}"], []
    if kind == "llm":
        l = r["llm"]
        if arg == "openai":
            return l["openai_key"], [] if l["openai_key"] else ["нет ключа OpenAI"], []
        ok = bool(l["lms"]) and l["local_model_ok"]
        return ok, [] if ok else ["LM Studio не установлен" if not l["lms"] else "модель не скачана в LM Studio"], []
    return False, [f"неизвестное требование {need}"], []


def _used_files(r, needs):
    """Файлы моделей, которые вариант реально возьмёт (found — с учётом вариантов весов), для подсказки в интерфейсе."""
    c, out = r["comfy"], []
    if not c["ok"]:
        return out
    for alt in (a for n in needs for a in n.split("|")):
        kind, _, arg = alt.partition(":")
        if kind in ("group", "file"):
            grp, _, fname = arg.partition("/")
            out += [st["found"] for m, st in zip(MODELS, c["models"])
                    if m["group"] == grp and (not fname or m["file"] == fname) and st.get("found")]
    return list(dict.fromkeys(out))


def steps_status(r):
    """STEPS с итогом по каждому варианту: ok, чего не хватает, что можно докачать (группы и ГБ), что — только руками."""
    l = r["llm"]
    current = {"prompt": {"auto": "openai" if l["openai_key"] else "local"}.get(l["mode"], l["mode"])}
    out = []
    for s in STEPS:
        opts = []
        for o in s["options"]:
            res = [_need(r, n) for n in o["needs"]]
            miss_models = [p for x in res for p in x[2]]
            dl = [(m, st) for m, st in miss_models if m.get("download", True)]
            opts.append({"id": o["id"], "title": o["title"], "ok": all(x[0] for x in res),
                         "missing": list(dict.fromkeys(t for x in res for t in x[1])),  # без повторов «ComfyUI не отвечает»
                         "download": sorted({m["group"] for m, _ in dl}), "gb": round(sum(m["gb"] for m, _ in dl), 2),
                         "manual": [m["file"] for m, _ in miss_models if not m.get("download", True)],
                         "files": _used_files(r, o["needs"]), "current": current.get(s["id"]) == o["id"]})
        cur = next((o for o in opts if o["current"]), None)
        ok = any(o["ok"] for o in opts) and (cur is None or cur["ok"])
        out.append({"id": s["id"], "title": s["title"], "what": s["what"], "optional": bool(s.get("optional")),
                    "state": "ok" if ok else "opt" if s.get("optional") or any(o["ok"] for o in opts) else "bad",
                    "options": opts})
    return out


def _minimum(r):
    """Минимальный набор: ComfyUI с нодами + модели core + Blender + Python-зависимости.
    missing — в родительном падеже: читается и как «Не хватает: …», и в пометке окна настроек."""
    c, miss, dl = r["comfy"], [], []
    if not c["ok"]:
        miss.append("связи с ComfyUI")
    else:
        miss += [f"ноды {n['name']}" for n in c["nodes"] if not n["ok"]]
        core = [m for m in c["models"] if m["group"] == "core" and not m["ok"]]
        if core:
            miss.append(f"моделей основного пути ({len(core)} шт.)")  # объём — на кнопке «Скачать»
            dl = ["core"]
    if not r["blender"]["ok"]:
        miss.append("Blender 4.2+")
    if not r["python"]["ok"]:
        miss.append("Python-зависимостей")
    return {"ok": not miss, "missing": miss, "download": dl}


def check_all():
    comfy = check_comfy()
    r = {"comfy": comfy, "blender": check_blender(), "llm": check_llm(), "python": check_python(), "hdri": check_hdri(),
         "groups": GROUPS, "t": time.time()}
    r["steps"], r["minimum"] = steps_status(r), _minimum(r)
    return r


# ---------- докачка

# groups — что качается: интерфейс по ним находит строку проверки, где показать прогресс (окно могли открыть заново)
DL = {"active": False, "groups": [], "file": None, "done_bytes": 0, "total_bytes": 0, "files_done": 0, "files_total": 0, "error": None, "log": []}


def target_dir(folder, folders):
    base = config.get("models_dir")
    if base:
        return os.path.join(base, folder)
    paths = folders.get(folder) or []
    if not paths:
        raise RuntimeError(f"ComfyUI не сообщил папку для «{folder}» — укажи «Папка моделей» в настройках")
    return paths[0]  # первая папка этого типа в настройках ComfyUI — туда он смотрит в первую очередь


def _fetch(url, dest, threads=6, chunk=32 << 20):
    """Многопоточная докачка по Range с повторами; файл появляется под своим именем только целиком."""
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=60) as r:
        size = int(r.headers["Content-Length"])
    DL["total_bytes"] += size
    part = dest + ".part"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(part, "wb") as f:
        f.truncate(size)
    ranges = [(s, min(s + chunk, size) - 1) for s in range(0, size, chunk)]
    lock = threading.Lock()
    failed = []

    def worker():
        while True:
            with lock:
                if not ranges:
                    return
                a, b = ranges.pop(0)
            for attempt in range(8):
                try:
                    rq = urllib.request.Request(url, headers={"Range": f"bytes={a}-{b}"})
                    with urllib.request.urlopen(rq, timeout=120) as r:
                        data = r.read()
                    if len(data) != b - a + 1:
                        raise IOError("short read")
                    with lock:
                        with open(part, "r+b") as f:
                            f.seek(a)
                            f.write(data)
                        DL["done_bytes"] += len(data)
                    break
                except Exception:  # noqa: BLE001
                    time.sleep(2 + attempt * 2)
            else:
                failed.append((a, b))
    ts = [threading.Thread(target=worker) for _ in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    if failed:
        raise RuntimeError(f"не докачалось {len(failed)} кусков {os.path.basename(dest)}")
    os.replace(part, dest)


def download(groups=("core",), log=print):
    """Докачать недостающее из групп. Модели — в папки ComfyUI, панорамы — в web/hdri."""
    DL.update(active=True, groups=sorted(groups), error=None, done_bytes=0, total_bytes=0, files_done=0, files_total=0, log=[])
    try:
        jobs = []
        if any(g != "hdri" for g in groups):
            comfy = check_comfy()
            if not comfy["ok"]:
                raise RuntimeError(comfy["error"])
            for m, st in zip(MODELS, comfy["models"]):
                if m["group"] in groups and not st["ok"] and m.get("download", True):
                    jobs.append((HF.format(repo=m["repo"], path=m["path"]), os.path.join(target_dir(m["folder"], comfy["folders"]), m["file"])))
        if "hdri" in groups:
            for h in HDRI:
                dest = os.path.join(HDRI_DIR, f"{h}_4k.hdr")
                if not os.path.exists(dest):
                    jobs.append((f"https://dl.polyhaven.org/file/ph-assets/HDRIs/hdr/4k/{h}_4k.hdr", dest))
        DL["files_total"] = len(jobs)
        for url, dest in jobs:
            DL["file"] = os.path.basename(dest)
            msg = f"↓ {DL['file']} → {os.path.dirname(dest)}"
            DL["log"].append(msg)
            log(msg)
            if not os.path.exists(dest):
                _fetch(url, dest)
            DL["files_done"] += 1
        DL["log"].append("готово")
        log("готово")
    except Exception as e:  # noqa: BLE001
        DL["error"] = str(e)
        log("ошибка: " + str(e))
    finally:
        DL.update(active=False, file=None)
    return DL


def report(r):
    c = r["comfy"]
    ok = lambda b: "✓" if b else "✗"  # noqa: E731
    print(f"{ok(c['ok'])} ComfyUI {c.get('version', '')} {c.get('url')} {c.get('gpu', '')} {c.get('vram_gb', '')} ГБ VRAM"
          + (f" — {c['error']}" if not c["ok"] else ""))
    if c["ok"]:
        for n in c["nodes"]:
            if not n["ok"]:
                print(f"  ✗ нода {n['name']}: {n['fix']}")
        for g in GROUPS:
            ms = [m for m in c["models"] if m["group"] == g]
            if ms:
                miss = [m for m in ms if not m["ok"]]
                print(f"  {ok(not miss)} модели «{g}» ({GROUPS[g]}): " +
                      ("все на месте" if not miss else f"нет {len(miss)} из {len(ms)}, {sum(m['gb'] for m in miss):.1f} ГБ: " +
                       ", ".join(m["file"] for m in miss)))
    b = r["blender"]
    print(f"{ok(b['ok'])} Blender {b.get('version', '')} {b.get('path', '')}" + (f" — {b['error']}" if b.get("error") else ""))
    llm = r["llm"]
    print(f"{ok(llm['ok'])} текстовая модель: режим {llm['mode']}, ключ OpenAI {'есть' if llm['openai_key'] else 'нет'}, "
          f"LM Studio {'есть' if llm['lms'] else 'нет'}, локальная модель {'есть' if llm['local_model_ok'] else 'нет'}")
    p = r["python"]
    print(f"{ok(p['ok'])} Python-зависимости" + (f": {p['fix']}" if p.get("fix") else
                                                 f": нет {', '.join(p['missing'])} — pip install -r requirements.txt" if p["missing"] else ""))
    h = r["hdri"]
    print(f"{ok(h['ok'])} панорамы 4k: {h['have']}/{h['total']} (необязательно)")


if __name__ == "__main__":
    # вывод в трубу или файл на Windows — cp1252/cp866: кириллица и ✓ роняли отчёт целиком
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="check", choices=["check", "download"])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--group", default="core")
    ap.add_argument("--dir", default="", help="куда качать модели (иначе — папки самого ComfyUI)")
    a = ap.parse_args()
    if a.dir:
        config.save({"models_dir": a.dir})
    if a.cmd == "download":
        download(tuple(a.group.split(",")))
    r = check_all()
    print(json.dumps(r, ensure_ascii=False, indent=1) if a.json else "", end="")
    if not a.json:
        report(r)
