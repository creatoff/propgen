"""Описание пропа для промпта генератора видов: перевод заметки (обычно по-русски) на английский и дополнение по кадру.

Локальная мультимодальная модель в LM Studio (Gemma-3-4B). Вызывается только из очереди задач сервера, где видеокарта
ничья: просим ComfyUI выгрузить модели, грузим LLM на GPU (ответ за секунды), сразу выгружаем. На CPU рядом с идущим
Qwen-Edit она отнимала у Comfy оперативку — тот уходил в своп. Qwen3.5-4B не годится: в LM Studio она «думает»
и тратит весь лимит токенов на рассуждения, ответ пустой.

python tools/llm.py --img crop.png --notes "ржавая стела с надписью ..." [--cpu]
"""
import argparse
import base64
import io
import json
import os
import re
import subprocess
import time
import urllib.request

import config  # noqa: E402 — адреса и модель — из настроек propgen

IDENT = "propgen-llm"


def HOST():
    return config.get("lmstudio_url").rstrip("/")


def _lms():
    p = config.find_lms()
    if not p:
        raise RuntimeError("LM Studio не найден: установи его или укажи путь к lms в настройках")
    return p

TRANSLATE = """Translate the user's description of an object into English.
Keep EVERY detail and every explanation from the original - do not shorten, summarize or drop anything.
Write the whole text in English. Only the exact text of an inscription (what is written on the object) stays in its
original alphabet, in double quotes, followed by its English meaning in parentheses.
Example: "ржавая табличка с надписью "Выход"" -> "a rusty plate with the inscription "Выход" (Exit)".
Output only the translation, one paragraph, no preface."""

DETAILS = """You look at a photo containing the object described below and add visual details of THAT object only.
List appearance details NOT already stated in the description: shape, parts, materials, colors, wear, condition.
Ignore everything else in the photo - other signs, trees, cars, buildings, people, ground, sky.
Output one comma-separated phrase of 8-30 words, no full sentence, no preface. If there is nothing to add, output NONE.
Do not say "game prop", "photo", "image", "background", "lighting" or "style"."""


def _req(path, data=None, timeout=300):
    req = urllib.request.Request(HOST() + path, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def free_comfy():
    """Попросить ComfyUI выгрузить модели из видеопамяти (в оперативке они остаются — повторная загрузка быстрая)."""
    try:
        urllib.request.urlopen(urllib.request.Request(config.get("comfy_url").rstrip("/") + "/free", data=b'{"unload_models": true}',
                                                      headers={"Content-Type": "application/json"}), timeout=30)
        time.sleep(1.5)
    except Exception:  # noqa: BLE001 — Comfy не запущен: видеопамять и так свободна
        pass


def load(gpu):
    try:
        _req("/v1/models", timeout=5)
    except Exception:  # noqa: BLE001 — сервер LM Studio не запущен
        subprocess.run([_lms(), "server", "start"], capture_output=True, timeout=60)
    subprocess.run([_lms(), "unload", IDENT], capture_output=True, timeout=60)
    subprocess.run([_lms(), "load", config.get("local_model"), "--gpu", "max" if gpu else "off", "--ttl", "120", "--context-length", "4096",
                    "--identifier", IDENT, "-y"], capture_output=True, timeout=300, check=True)


def unload():
    subprocess.run([_lms(), "unload", IDENT], capture_output=True, timeout=60)


def _image_url(path, side=640):
    from PIL import Image
    im = Image.open(path).convert("RGB")
    im.thumbnail((side, side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _chat(system, content, max_tokens=300):
    r = _req("/v1/chat/completions", {
        "model": IDENT, "temperature": 0.2, "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
    })
    text = r["choices"][0]["message"].get("content") or ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    return " ".join(text.split()).strip()


def _ask(notes, img):
    """Промпт = полный перевод заметки (ничего не теряем) + добавочные детали с кадра."""
    notes = (notes or "").strip()
    if notes and has_cyrillic(notes):
        base = _chat(TRANSLATE, notes).strip().rstrip(".")
    else:
        base = notes.rstrip(".")
    extra = ""
    if img and os.path.exists(img):
        extra = _chat(DETAILS, [{"type": "image_url", "image_url": {"url": _image_url(img)}},
                                {"type": "text", "text": "Description: " + (base or "(none - describe the main object)")}],
                      max_tokens=120).strip().rstrip(".")
        if extra.upper().startswith("NONE"):
            extra = ""
    if not base and not extra:
        raise RuntimeError("модель вернула пустой ответ")
    text = base + ("; " + extra if base and extra else extra)
    # страховка: надписи из заметки (в кавычках) должны дойти до промпта дословно
    for q in re.findall(r'["«“]([^"»”]{2,})["»”]', notes):
        if q.lower() not in text.lower():
            text += f', with the inscription "{q}"'
    return text + "."


def describe(notes="", img=None, gpu=True):
    if gpu:
        free_comfy()
    load(gpu)
    try:
        return _ask(notes, img)
    finally:
        unload()


def has_cyrillic(s):
    return bool(re.search(r"[А-Яа-яЁё]", s or ""))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", default="")
    ap.add_argument("--notes", default="")
    ap.add_argument("--cpu", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    print(describe(a.notes, a.img or None, gpu=not a.cpu))
    print(f"[{time.time() - t0:.1f} s]")
