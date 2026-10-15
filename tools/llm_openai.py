"""Описание пропа через OpenAI API — экономно: один запрос (перевод заметки + детали с кадра одним JSON),
картинка 512 px в режиме detail=low, короткий ответ, кэш по заметке и кадру, учёт расхода и дневной потолок.
Ключ — в config.json или одной строкой в .openai_key в папке данных (config.DATA; в .gitignore; никуда не выводится).

python tools/llm_openai.py --img crop.png --notes "ржавая стела ..." [--model gpt-4.1-nano] [--kind t2i]
"""
import argparse
import base64
import hashlib
import io
import json
import os
import re
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402 — ключ, модель и лимит — из настроек propgen
import llm  # noqa: E402 — общая с локальной моделью инструкция «по тексту» и фраза о кадре

KEY_FILE = config.KEY_FILE  # старое место ключа; основное — config.json
# в папке данных, а не рядом с кодом: установленную версию обновление заменяет целиком (см. config.DATA)
USAGE = os.path.join(config.WORK, "llm_usage.jsonl")
CACHE = os.path.join(config.WORK, "llm_cache.json")
def MODEL():
    return config.get("openai_model") or "gpt-4.1-mini"  # лучшие описания из дешёвых, ~$0.0003 за проп


def DAILY_LIMIT():
    return float(config.get("openai_daily_usd") or 0)
# $ за 1M токенов (вход, выход) — для учёта; у неизвестной модели считаем по самой дорогой из известных
PRICE = {"gpt-4.1-nano": (0.10, 0.40), "gpt-5-nano": (0.05, 0.40), "gpt-4.1-mini": (0.40, 1.60), "gpt-5-mini": (0.25, 2.00)}

SYSTEM = """You prepare the subject text for an image-editing model that will redraw ONE object from a photo as a clean product shot.
Return JSON: {"translation": "...", "details": "..."}.
translation: the user's notes translated into English COMPLETELY - keep every detail and explanation, drop nothing.
  Text written on the object stays verbatim in its original alphabet in double quotes, followed by its English meaning in parentheses.
details: comma-separated appearance details of THAT object visible in the photo and not already in the notes
  (shape, parts, materials, colors, wear), 8-30 words. Ignore everything around it (other signs, trees, cars, buildings, ground, sky).
Never use the words game prop, photo, image, background, lighting, style."""

# Режим «по тексту»: инструкция та же, что у локальной модели (llm.T2I), но ответ — JSON, как и у основного запроса.
SYSTEM_T2I = llm.T2I.replace("60-140 words, one paragraph of plain English.",
                             'Return JSON: {"prompt": "..."} - 60-140 words, one paragraph of plain English.')


def key():
    return config.openai_key()


def available():
    return config.get("llm_mode") in ("auto", "openai") and bool(key()) and spent_today() < DAILY_LIMIT()


def spent_today():
    day, total = time.strftime("%Y-%m-%d"), 0.0
    if os.path.exists(USAGE):
        with open(USAGE, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                if r.get("day") == day:
                    total += r.get("usd", 0)
    return total


def _image(path, side=512):
    from PIL import Image
    im = Image.open(path).convert("RGB")
    im.thumbnail((side, side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def describe(notes, img=None, model=None, kind="edit"):
    """→ (текст промпта, сведения о вызове). Повтор с той же заметкой и кадром берётся из кэша — без запроса.
    kind: edit — подпись предмета для перерисовки кадра; t2i — исчерпывающий промпт для генерации с нуля."""
    model = model or MODEL()
    t2i = kind == "t2i"
    jpg = _image(img) if img and os.path.exists(img) else b""
    # kind — в ключе, но у edit ключ прежний: накопленный кэш описаний не обнуляется
    h = hashlib.sha1((model + "\0" + ("t2i\0" if t2i else "") + (notes or "") + "\0").encode() + jpg).hexdigest()
    cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}
    if h in cache:
        return cache[h], {"model": model, "cached": True, "usd": 0}
    content = [{"type": "text", "text": "Notes: " + ((notes or "").strip() or "(none - describe the main object)")}]
    if jpg:
        content.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpg).decode(),
                                                           "detail": "low"}})
    body = {"model": model, "messages": [{"role": "system", "content": SYSTEM_T2I if t2i else SYSTEM},
                                         {"role": "user", "content": content}],
            "response_format": {"type": "json_object"}}
    if model.startswith("gpt-5"):
        # у рассуждающих лимит включает рассуждения; 5.4 понимает только none/low…, 5.0 — minimal
        body.update(reasoning_effort="none" if model.startswith("gpt-5.4") else "minimal",
                    max_completion_tokens=900 if t2i else 600)
    else:
        # t2i-абзац до 140 слов ≈ 200 токенов + обёртка JSON — 350 с запасом, но без простора для болтовни
        body.update(max_tokens=350 if t2i else 220, temperature=0.2)
    t0 = time.time()
    req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + key(), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.loads(r.read())
    u = resp.get("usage", {})
    pin, pout = PRICE.get(model, max(PRICE.values()))
    usd = (u.get("prompt_tokens", 0) * pin + u.get("completion_tokens", 0) * pout) / 1e6
    info = {"day": time.strftime("%Y-%m-%d"), "t": time.time(), "model": model, "in": u.get("prompt_tokens"),
            "out": u.get("completion_tokens"), "usd": round(usd, 6), "sec": round(time.time() - t0, 1)}
    os.makedirs(os.path.dirname(USAGE), exist_ok=True)
    with open(USAGE, "a", encoding="utf-8") as f:
        f.write(json.dumps(info) + "\n")
    data = json.loads(resp["choices"][0]["message"]["content"] or "{}")
    if t2i:
        text = llm.t2i_finish(data.get("prompt"), notes)
        cache[h] = text
        json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return text, info
    tr = " ".join((data.get("translation") or "").split()).rstrip(".")
    det = " ".join((data.get("details") or "").split()).rstrip(".")
    if not tr and not det:
        raise RuntimeError("пустой ответ модели")
    text = tr + ("; " + det if tr and det else det)
    for q in re.findall(r'["«“]([^"»”]{2,})["»”]', notes or ""):  # надписи из заметки — дословно
        if q.lower() not in text.lower():
            text += f', with the inscription "{q}"'
    text += "."
    cache[h] = text
    json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return text, info


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", default="")
    ap.add_argument("--notes", default="")
    ap.add_argument("--model", default=None)
    ap.add_argument("--kind", default="edit", choices=["edit", "t2i"])
    a = ap.parse_args()
    text, info = describe(a.notes, a.img or None, a.model, kind=a.kind)
    print(text)
    print(info)
