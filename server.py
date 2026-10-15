"""propgen — веб-интерфейс генератора пропов: фото → проекции (Qwen-Image-Edit) → 3D (Pixal3D / TRELLIS.2) → лоу-поли HL2.

Запуск: start.cmd (или .venv/Scripts/python server.py) → http://127.0.0.1:8795 (порт — в настройках).
Нужны запущенный ComfyUI и Blender 4.2+; пути, адреса и ключи — config.json, экран «Настройки» или tools/doctor.py.
Задачи идут по одной: видеокарта одна.
"""
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps

ROOT = os.path.dirname(os.path.abspath(__file__))
PROPS = os.path.join(ROOT, "work", "props")
TOOLS = os.path.join(ROOT, "tools")
PY = sys.executable  # скрипты Comfy работают на stdlib, venv подходит
VIEWS = ("front", "left", "back", "right")
DEFAULTS = {"mode": "single", "single_src": "front", "model": "trellis2", "tris": 1500, "tex": 1024, "crease": 50, "seed": 1, "rot": 0,
            "height_cm": 0, "export_sources": False,
            "views_engine": "qwen", "wear": "keep", "t2i_model": "klein"}
os.makedirs(PROPS, exist_ok=True)
sys.path.insert(0, TOOLS)
import llm  # noqa: E402 — описание пропа локальной моделью (LM Studio, GPU через очередь)
import comfywatch  # noqa: E402 — живой прогресс ComfyUI (websocket) для строки состояния
import llm_openai  # noqa: E402 — описание через OpenAI API: быстро, без видеокарты, экономно
import config  # noqa: E402 — config.json: пути, адреса, ключ, режим текстовой модели
import doctor  # noqa: E402 — проверка окружения и докачка моделей
import pack  # noqa: E402 — архив пропа для движков (структура Poly Haven)

comfywatch.start()

app = FastAPI()
lock = threading.RLock()
tasks: "queue.Queue[dict]" = queue.Queue()
state = {"current": None, "queued": []}


# ---------- хранилище

def pdir(pid, *parts):
    if not re.fullmatch(r"[a-z0-9-]+", pid):
        raise HTTPException(400, "bad id")
    return os.path.join(PROPS, pid, *parts)


def load(pid):
    path = pdir(pid, "meta.json")
    if not os.path.exists(path):
        raise HTTPException(404, "нет такого пропа")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(meta):
    with lock:
        path = pdir(meta["id"], "meta.json")
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
        os.replace(path + ".tmp", path)


def update(pid, fn):
    with lock:
        m = load(pid)
        fn(m)
        save(m)
        return m


def files_of(m):
    """Что уже лежит на диске — для интерфейса (пути относительно /files/<id>/)."""
    pid = m["id"]
    out = {"photos": m.get("photos", []), "views": {}, "inputs": {}}
    if os.path.exists(pdir(pid, "crop.png")):
        out["crop"] = f"crop.png?t={int(os.path.getmtime(pdir(pid, 'crop.png')))}"
    for v in VIEWS:
        p = pdir(pid, "views", v + ".png")
        if os.path.exists(p):
            out["views"][v] = f"views/{v}.png?t={int(os.path.getmtime(p))}"
    for name, rel in (("hi", "mesh/prop_hi.glb"), ("raw", "mesh/prop_raw.glb"), ("lo", "lo/prop_lo.glb"),
                      ("base", "lo/prop_lo_base.png"), ("normal", "lo/prop_lo_normal.png"),
                      ("ao", "lo/prop_lo_ao.png"), ("sheet", "lo/sheet.png")):
        p = pdir(pid, rel)
        if os.path.exists(p):
            out[name] = f"{rel}?t={int(os.path.getmtime(p))}"
    return out


def public(m):
    m = dict(m)
    m["files"] = files_of(m)
    return m


# ---------- шаги конвейера

def run_logged(pid, step, cmd):
    log = pdir(pid, "logs", step + ".log")
    os.makedirs(os.path.dirname(log), exist_ok=True)
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"\n$ {' '.join(cmd)}\n")
        f.flush()
        proc = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=ROOT,
                                env=dict(os.environ, PROPGEN_PY=sys.executable, PYTHONIOENCODING="utf-8"),
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        with lock:
            state["proc"] = proc
        rc = proc.wait()
        with lock:
            state["proc"] = None
    if state.pop("cancelled", False):
        raise RuntimeError("остановлено")
    if rc:
        with open(log, encoding="utf-8", errors="replace") as f:
            tail = f.read()[-1500:]
        raise RuntimeError(tail)


def build_prompt(pid, notes, local_ok=True, kind="edit"):
    """Заметка → английский промпт. Сначала OpenAI (секунды, без видеокарты, копейки), иначе локальная Gemma —
    её можно звать только из очереди задач (она забирает видеокарту у Comfy).
    kind: edit — подпись предмета для перерисовки кадра (поле desc); t2i — исчерпывающий промпт «по тексту» (поле t2i)."""
    crop = pdir(pid, "crop.png")
    t0 = time.time()
    try:
        if not llm_openai.available():
            raise RuntimeError("нет ключа или исчерпан дневной лимит")
        en, info = llm_openai.describe(notes, crop, kind=kind)
        engine = f"{info['model']}" + (" · из кэша" if info.get("cached") else f" · ${info['usd']:.5f}")
    except Exception as e:  # noqa: BLE001
        if not local_ok or config.get("llm_mode") not in ("auto", "local"):
            raise
        en, engine = llm.describe(notes, crop, kind=kind), f"{config.get('local_model')} локально (OpenAI: {str(e)[:80]})"
    os.makedirs(pdir(pid, "logs"), exist_ok=True)
    with open(pdir(pid, "logs", "views.log"), "a", encoding="utf-8") as f:
        f.write(f"\n[промпт{' t2i' if kind == 't2i' else ''} {time.time() - t0:.0f} с, {engine}] {notes}\n  -> {en}\n")
    return en, engine


NO_LLM = ("Текстовой модели нет: добавьте ключ OpenAI или поставьте LM Studio с моделью (Настройки → Текстовая модель) — "
          "или напишите промпт по-английски сами")
_LOCAL = {"t": 0.0, "ok": False}


def local_llm_ok():
    """Есть ли LM Studio с нужной моделью. Без этой проверки кнопка «Из заметки» ставила перевод в очередь и висела
    на «перевожу…»: lms нет — ошибка не сразу, модели нет — `lms load` начинает её качать. `lms ls` — секунды, кэш на минуту."""
    if time.time() - _LOCAL["t"] > 60:
        lms, ok = config.find_lms(), False
        if lms:
            try:
                ls = subprocess.run([lms, "ls"], capture_output=True, text=True, timeout=30,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
                ok = config.get("local_model").split("/")[-1].lower() in ls.lower()
            except Exception:  # noqa: BLE001
                ok = False
        _LOCAL.update(t=time.time(), ok=ok)
    return _LOCAL["ok"]


def text_model():
    """Чем сейчас переводить заметки: "openai", "local" или None (выключено или нечем)."""
    mode = config.get("llm_mode")
    if mode in ("auto", "openai") and llm_openai.available():
        return "openai"
    if mode in ("auto", "local") and local_llm_ok():
        return "local"
    return None


def prompt_kind(m):
    """Какой промпт нужен пропу сейчас: в режиме «по тексту» — t2i, иначе подпись для перерисовки кадра."""
    p = m["params"]
    return "t2i" if p.get("mode") != "multiview" and p.get("single_src") == "text" else "edit"


def t2i_prompt(m):
    """Промпт для генерации с нуля — по-английски. Пусто или по-русски → собрать из заметки (мы в очереди — можно локально)."""
    t = m.get("t2i") or ""
    if t and not llm.has_cyrillic(t):
        return t
    notes = m.get("notes") or m.get("desc") or t
    if text_model() is None:
        if notes and not llm.has_cyrillic(notes):  # английская заметка — сама себе промпт, допишем только кадр
            return llm.t2i_finish(notes, notes)
        raise RuntimeError("режим «по тексту»: " + NO_LLM[0].lower() + NO_LLM[1:])
    en, engine = build_prompt(m["id"], notes, kind="t2i")

    def fn(mm):
        mm["notes"] = mm.get("notes") or notes
        mm["t2i"] = en
        mm["t2i_engine"] = engine
    update(m["id"], fn)
    return en


def english_desc(m):
    """Промпт генератора видов — по-английски. Пусто или по-русски → собрать из заметки."""
    desc = m.get("desc") or ""
    if desc and not llm.has_cyrillic(desc):
        return desc
    notes = m.get("notes") or desc
    if text_model() is None:  # выключена или нечем переводить — генератор получит заметку как есть
        with open(pdir(m["id"], "logs", "views.log"), "a", encoding="utf-8") as f:
            f.write(f"\n[промпт] текстовой модели нет — заметка как есть: {notes}\n")
        return notes
    en, engine = build_prompt(m["id"], notes)

    def fn(mm):
        mm["notes"] = mm.get("notes") or notes
        mm["desc"] = en
        mm["desc_engine"] = engine
    update(m["id"], fn)
    return en


def step_views(m, task):
    pid, p = m["id"], m["params"]
    os.makedirs(pdir(pid, "logs"), exist_ok=True)
    only = task.get("view")  # перегенерировать один вид
    text = prompt_kind(m) == "t2i" and only in (None, "front")
    # «по тексту» desc не нужен — не тратим на него запрос к модели
    desc = "" if text else english_desc(m)
    vdir = pdir(pid, "views")
    os.makedirs(vdir, exist_ok=True)
    seed = int(task.get("seed") or p["seed"])
    tmp = pdir(pid, "views_tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    if text:
        # Klein (или Krea-2, если она есть в ComfyUI) без опорной картинки рисует предмет с нуля; разворотов здесь нет
        engine = "krea2" if p.get("t2i_model") == "krea2" else "t2i"
        cmd = [PY, os.path.join(TOOLS, "views.py"), "--engine", engine, "--prompt", t2i_prompt(m), "--views", "",
               "--out", tmp, "--seed", str(seed), "--tag", f"propgen/{pid}"]
        run_logged(pid, "views", cmd)
        if os.path.exists(os.path.join(tmp, "front.png")):
            shutil.copy(os.path.join(tmp, "front.png"), os.path.join(vdir, "front.png"))
        shutil.rmtree(tmp, ignore_errors=True)
        return
    if only in (None, "front"):
        views = "" if only == "front" else "left,back,right"
        cmd = [PY, os.path.join(TOOLS, "views.py"), "--img", pdir(pid, "crop.png"), "--views", views]
    else:
        if not os.path.exists(os.path.join(vdir, "front.png")):
            raise RuntimeError("сначала нужен фронт")
        cmd = [PY, os.path.join(TOOLS, "views.py"), "--front", os.path.join(vdir, "front.png"), "--views", only]
    cmd += ["--engine", p.get("views_engine", "qwen"), "--wear", p.get("wear", "keep")]
    cmd += ["--out", tmp, "--desc", desc or "object", "--seed", str(seed), "--tag", f"propgen/{pid}"]
    run_logged(pid, "views", cmd)
    for v in VIEWS:
        src = os.path.join(tmp, v + ".png")
        if os.path.exists(src) and (only is None or v == only):
            shutil.copy(src, os.path.join(vdir, v + ".png"))
    shutil.rmtree(tmp, ignore_errors=True)


def step_mesh(m, task):
    pid, p = m["id"], m["params"]
    out = pdir(pid, "mesh.new")  # старый результат виден, пока не готов новый
    shutil.rmtree(out, ignore_errors=True)
    cmd = [PY, os.path.join(TOOLS, "mesh.py"), "--out", out, "--name", "prop", "--tag", f"propgen/{pid}",
           "--seed", str(42 + int(p["seed"]) - 1)]
    if p["mode"] == "multiview":
        use = [v for v in VIEWS if m.get("use_views", {}).get(v, True)
               and os.path.exists(pdir(pid, "views", v + ".png"))]
        if "front" not in use:
            raise RuntimeError("для multiview нужен хотя бы фронт")
        sel = pdir(pid, "views_sel")
        shutil.rmtree(sel, ignore_errors=True)
        os.makedirs(sel)
        for v in use:
            shutil.copy(pdir(pid, "views", v + ".png"), os.path.join(sel, v + ".png"))
        cmd += ["--views", sel]
    else:
        src = pdir(pid, "views", "front.png") if p.get("single_src") in ("front", "text") else pdir(pid, "crop.png")
        cmd += ["--img", src, "--model", p.get("model", "trellis2")]
    run_logged(pid, "mesh", cmd)
    swap(pid, "mesh")


def swap(pid, name):
    """<name>.new → <name>: подмена результата шага только после успеха."""
    shutil.rmtree(pdir(pid, name), ignore_errors=True)
    os.replace(pdir(pid, name + ".new"), pdir(pid, name))


def step_lowpoly(m, task):
    pid, p = m["id"], m["params"]
    hi = pdir(pid, "mesh", "prop_hi.glb")
    if not os.path.exists(hi):
        raise RuntimeError("сначала нужен 3D (хай-поли)")
    out = pdir(pid, "lo.new")
    shutil.rmtree(out, ignore_errors=True)
    refs = [os.path.join(pdir(pid, "mesh"), f) for f in sorted(os.listdir(pdir(pid, "mesh"))) if f.startswith("prop_in_")]
    blender = config.find_blender()
    if not blender:
        raise RuntimeError("Blender не найден — укажи путь в настройках")
    cmd = [blender, "-b", "--python", os.path.join(TOOLS, "lowpoly.py"), "--", hi, out, "prop", "--tris", str(p["tris"]),
           "--tex", str(p["tex"]), "--crease", str(p["crease"]), "--rot", str(p.get("rot", 0))]
    if refs:  # подгонка цвета текстуры к входным видам
        cmd += ["--ref", ",".join(refs)]
    run_logged(pid, "lowpoly", cmd)
    run_logged(pid, "lowpoly", [blender, "-b", "--python", os.path.join(TOOLS, "render.py"), "--",
                                os.path.join(out, "prop_lo.glb"), os.path.join(out, "sheet.png"), "--size", "320"])
    swap(pid, "lo")


def step_describe(m, task):
    notes = task.get("notes") or m.get("notes") or m.get("desc") or ""
    kind = task.get("kind") or prompt_kind(m)
    en, engine = build_prompt(m["id"], notes, kind=kind)
    field = "t2i" if kind == "t2i" else "desc"

    def fn(mm):
        mm["notes"] = notes  # заметка пользователя — как есть, не перезаписывается переводом
        mm[field] = en
        mm[field + "_engine"] = engine
    update(m["id"], fn)


STEPS = {"views": step_views, "mesh": step_mesh, "lowpoly": step_lowpoly, "describe": step_describe}
CHAIN = {"all": ["views", "mesh", "lowpoly"], "from_mesh": ["mesh", "lowpoly"]}


def worker():
    while True:
        task = tasks.get()
        if task.get("cancel"):
            tasks.task_done()
            continue
        with lock:
            state["queued"] = [t for t in state["queued"] if t["tid"] != task["tid"]]
            state["current"] = task
        pid, step = task["pid"], task["step"]
        try:
            def start(m):
                m["steps"][step] = {"status": "running", "started": time.time(), "view": task.get("view")}
            m = update(pid, start)
            STEPS[step](m, task)
            update(pid, lambda m: m["steps"][step].update(status="done", finished=time.time()))
            rest = task.get("then") or []
            if rest:  # цепочка: следующий шаг ставим только после успеха текущего
                nxt = rest[0]
                enqueue(pid, nxt["step"], view=nxt.get("view"), chain=nxt.get("chain"), then=rest[1:])
        except Exception as e:  # noqa: BLE001
            msg = str(e)[-1500:]
            update(pid, lambda m: m["steps"][step].update(status="error", finished=time.time(), error=msg))
        finally:
            with lock:
                state["current"] = None
            tasks.task_done()


def enqueue(pid, step, **kw):
    task = {"tid": uuid.uuid4().hex[:8], "pid": pid, "step": step, **kw}
    with lock:
        state["queued"].append(task)
    update(pid, lambda m: m["steps"].__setitem__(step, {"status": "queued", "view": kw.get("view")}))
    tasks.put(task)
    return task


threading.Thread(target=worker, daemon=True).start()


# ---------- API

@app.get("/api/props")
def list_props():
    out = []
    for pid in sorted(os.listdir(PROPS), reverse=True):
        if os.path.exists(os.path.join(PROPS, pid, "meta.json")):
            out.append(public(load(pid)))
    return out


@app.get("/api/props/{pid}")
def get_prop(pid: str):
    return public(load(pid))


@app.post("/api/props")
async def create_prop(name: str = Form(""), desc: str = Form(""), files: list[UploadFile] = File(...)):
    pid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    os.makedirs(pdir(pid, "photos"))
    photos = []
    for i, f in enumerate(files):
        im = ImageOps.exif_transpose(Image.open(f.file)).convert("RGB")
        im.thumbnail((2048, 2048))
        rel = f"photos/{i}.jpg"
        im.save(pdir(pid, rel), quality=92)
        photos.append(rel)
    shutil.copy(pdir(pid, photos[0]), pdir(pid, "crop.png"))
    meta = {"id": pid, "name": name or f"проп {pid[9:15]}", "notes": desc, "desc": "" if llm.has_cyrillic(desc) else desc, "created": time.time(), "photos": photos,
            "params": dict(DEFAULTS), "steps": {}, "use_views": {v: True for v in VIEWS}}
    save(meta)
    return public(meta)


@app.post("/api/props/{pid}/photos")
async def add_photos(pid: str, files: list[UploadFile] = File(...)):
    """Дослать фото к пропу (вставка из буфера, ещё ракурсы)."""
    def save_all(m):
        for f in files:
            im = ImageOps.exif_transpose(Image.open(f.file)).convert("RGB")
            im.thumbnail((2048, 2048))
            rel = f"photos/{len(m['photos'])}.jpg"
            im.save(pdir(pid, rel), quality=92)
            m["photos"].append(rel)
    return public(update(pid, save_all))


@app.patch("/api/props/{pid}")
def patch_prop(pid: str, body: dict):
    def fn(m):
        for k in ("name", "desc", "notes", "t2i"):
            if k in body:
                m[k] = body[k]
        if "params" in body:
            m["params"].update({k: v for k, v in body["params"].items() if k in DEFAULTS or k == "single_src"})
        if "use_views" in body:
            m["use_views"].update(body["use_views"])
    return public(update(pid, fn))


@app.delete("/api/props/{pid}")
def delete_prop(pid: str):
    with lock:
        busy = [t for t in state["queued"] if t["pid"] == pid] or (state["current"] or {}).get("pid") == pid
    if busy:
        raise HTTPException(409, "проп в очереди")
    shutil.rmtree(pdir(pid))
    return {"ok": True}


@app.post("/api/props/{pid}/crop")
def crop(pid: str, body: dict):
    """Рамка предмета на фото; full=true — осознанно целое фото. Без этого шаги, которым нужен кадр, не запускаются."""
    m = load(pid)
    im = Image.open(pdir(pid, m["photos"][int(body.get("photo", 0))]))
    n = int(body.get("photo", 0))
    rect = {"photo": n, "full": True}
    if not body.get("full"):
        x, y, w, h = (int(round(body[k])) for k in ("x", "y", "w", "h"))
        im = im.crop((x, y, x + w, y + h))
        rect = {"photo": n, "x": x, "y": y, "w": w, "h": h}
    im.save(pdir(pid, "crop.png"))

    def fn(mm):
        mm["cropped"] = True
        mm["crop_rect"] = rect  # чтобы рамку можно было показать и поправить, а не рисовать заново
    return public(update(pid, fn))


@app.post("/api/props/{pid}/view/{view}")
async def upload_view(pid: str, view: str, file: UploadFile = File(...)):
    if view not in VIEWS:
        raise HTTPException(400, "bad view")
    load(pid)
    os.makedirs(pdir(pid, "views"), exist_ok=True)
    ImageOps.exif_transpose(Image.open(file.file)).convert("RGB").save(pdir(pid, "views", view + ".png"))
    return public(load(pid))


@app.post("/api/props/{pid}/run")
def run(pid: str, body: dict):
    m = load(pid)
    step = body.get("step")
    chain = uuid.uuid4().hex[:6]
    # Кадр нужен генерации фронта и 3D «по кадру с фото». Забыть рамку легко — без неё модель видит весь снимок с фоном.
    p = m["params"]
    needs_crop = (step in ("all", "views") and body.get("view") in (None, "front")) or \
        (step in ("mesh", "from_mesh") and p["mode"] != "multiview" and p.get("single_src") == "crop")
    if needs_crop and not m.get("cropped") and not body.get("force"):
        raise HTTPException(409, "кадр не выделен")
    # Krea-2 не качаем: если её нет в ComfyUI, лучше сказать сразу, чем после промпта и ожидания в очереди
    if step in ("all", "views") and body.get("view") in (None, "front") and prompt_kind(m) == "t2i" \
            and p.get("t2i_model") == "krea2" and not doctor.krea_files():
        raise HTTPException(409, "Krea-2 не найдена в ComfyUI — выберите Klein или скачайте группу «krea» в настройках")
    if step in CHAIN:
        steps = [{"step": s} for s in CHAIN[step]]
        p = load(pid)["params"]
        if step == "all" and p["mode"] != "multiview":
            # одно фото: виды не нужны, а для «чистого фронта» и «по тексту» — только фронт
            steps = ([{"step": "views", "view": "front"}] if p.get("single_src") in ("front", "text") else []) + steps[1:]
        then = [dict(s, chain=chain) for s in steps[1:]]
        enqueue(pid, steps[0]["step"], view=steps[0].get("view"), chain=chain, then=then)
    elif step in STEPS:
        enqueue(pid, step, view=body.get("view"), seed=body.get("seed"))
    else:
        raise HTTPException(400, "bad step")
    return public(load(pid))


@app.post("/api/props/{pid}/describe")
def describe(pid: str, body: dict):
    """Перевести и дополнить описание по кадру. OpenAI — сразу, мимо очереди (видеокарта не нужна, виды не ждут);
    нет ключа, сети или лимита — локальная Gemma шагом очереди."""
    m = load(pid)
    notes = (body.get("notes") if body.get("notes") is not None else (m.get("notes") or m.get("desc"))) or ""
    # в режиме «по тексту» кнопка строит исчерпывающий t2i-промпт и пишет его в своё поле, desc не трогает
    kind = body.get("kind") if body.get("kind") in ("edit", "t2i") else prompt_kind(m)
    field = "t2i" if kind == "t2i" else "desc"
    if llm_openai.available():
        update(pid, lambda mm: mm["steps"].__setitem__("describe", {"status": "running", "started": time.time()}))
        try:
            en, engine = build_prompt(pid, notes, local_ok=False, kind=kind)

            def fn(mm):
                mm["notes"], mm[field], mm[field + "_engine"] = notes, en, engine
                mm["steps"]["describe"] = {"status": "done", "started": mm["steps"]["describe"].get("started"),
                                           "finished": time.time(), "engine": engine}
            return public(update(pid, fn))
        except Exception:  # noqa: BLE001 — OpenAI недоступен: уходим на локальную модель
            pass
    mode = config.get("llm_mode")
    if mode == "off":
        raise HTTPException(400, "текстовая модель выключена в настройках")
    if mode == "openai":
        raise HTTPException(503, "OpenAI недоступен: нет ключа, сети или исчерпан дневной лимит — см. настройки")
    if not local_llm_ok():  # не ставим в очередь то, что заведомо не выполнится
        raise HTTPException(409, NO_LLM)
    enqueue(pid, "describe", notes=notes, kind=kind)
    return public(load(pid))


@app.post("/api/cancel")
def cancel(body: dict):
    """Остановить текущий шаг (и снять очередь этого пропа): прервать ComfyUI и процесс скрипта."""
    import urllib.request
    pid = body.get("pid")
    with lock:
        cur = state["current"]
        dropped = [t for t in state["queued"] if t["pid"] == pid]
        state["queued"] = [t for t in state["queued"] if t["pid"] != pid]
        for t in dropped:
            t["cancel"] = True
        if cur and cur["pid"] == pid:
            state["cancelled"] = True
            if state.get("proc"):
                state["proc"].kill()
    try:
        urllib.request.urlopen(urllib.request.Request(config.get("comfy_url").rstrip("/") + "/interrupt", data=b"{}",
                                                      headers={"Content-Type": "application/json"}), timeout=5)
    except Exception:  # noqa: BLE001
        pass

    def fn(m):
        for t in dropped:
            m["steps"][t["step"]] = {"status": "error", "error": "снято с очереди"}
    if dropped:
        update(pid, fn)
    return {"ok": True}


@app.get("/api/queue")
def get_queue():
    with lock:
        cur = state["current"]
        return {"current": cur and {k: cur[k] for k in ("pid", "step") if k in cur} | {"view": cur.get("view")},
                "queued": [{"pid": t["pid"], "step": t["step"], "view": t.get("view")} for t in state["queued"]],
                "comfy": comfywatch.snapshot()}


@app.get("/api/health")
def health():
    """Для строки состояния: жив ли ComfyUI и сколько видеопамяти занято."""
    import urllib.request
    try:
        with urllib.request.urlopen(config.get("comfy_url").rstrip("/") + "/system_stats", timeout=1.5) as r:
            dev = (json.loads(r.read()).get("devices") or [{}])[0]
        out = {"comfy": True, "vram_total": dev.get("vram_total"), "vram_free": dev.get("vram_free"), "gpu": dev.get("name", "")}
    except Exception:  # noqa: BLE001
        out = {"comfy": False}
    out["openai"] = {"key": bool(config.openai_key()), "mode": config.get("llm_mode"), "today": round(llm_openai.spent_today(), 4),
                     "limit": llm_openai.DAILY_LIMIT(), "model": llm_openai.MODEL()}
    return out


@app.get("/api/props/{pid}/log/{step}")
def get_log(pid: str, step: str):
    p = pdir(pid, "logs", step + ".log")
    if not os.path.exists(p):
        return {"log": ""}
    with open(p, encoding="utf-8", errors="replace") as f:
        return {"log": f.read()[-6000:]}


@app.post("/api/props/{pid}/export")
def export_prop(pid: str, body: dict):
    """Архив для движков: glb, gltf, fbx, obj, текстуры по схеме Poly Haven, info.json; по желанию — исходники.
    Blender без видеокарты, ~10 с — прямо в запросе, мимо очереди."""
    m = load(pid)
    p = m["params"]
    height = float(body.get("height_cm", p.get("height_cm") or 0) or 0)
    sources = bool(body.get("sources", p.get("export_sources")))
    os.makedirs(pdir(pid, "logs"), exist_ok=True)

    def log(text):
        with open(pdir(pid, "logs", "export.log"), "a", encoding="utf-8") as f:
            f.write(f"\n=== {time.strftime('%H:%M:%S')}\n{text}\n")
    try:
        zpath, info = pack.build(pdir(pid), m, height_cm=height, sources=sources, log=log)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, str(e)[-800:])
    rel = os.path.relpath(zpath, pdir(pid)).replace("\\", "/")
    return {**info, "url": f"/files/{pid}/{rel}?t={int(os.path.getmtime(zpath))}"}


# ---------- версия и самообновление из git

STARTED = time.time()


def _git(*args, timeout=60):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.returncode, r.stdout.rstrip(), r.stderr.strip()  # слева не режем: у git status формат « M файл»


def _commits(rng):
    out = _git("log", "--format=%h\x1f%cI\x1f%s", rng)[1]
    return [dict(zip(("hash", "date", "subject"), l.split("\x1f"))) for l in out.splitlines() if l]


@app.get("/api/version")
def version():
    head = _git("rev-parse", "--short", "HEAD")[1] if os.path.isdir(os.path.join(ROOT, ".git")) else ""
    return {"head": head, "started": STARTED}


@app.get("/api/update")
def update_info(fetch: int = 0):
    """Есть ли новые коммиты в репозитории. fetch=1 — сначала спросить GitHub (несколько секунд)."""
    if not os.path.isdir(os.path.join(ROOT, ".git")) or not shutil.which("git"):
        return {"git": False}
    res = {"git": True}
    if fetch:
        rc, _, err = _git("fetch", "--quiet", timeout=90)
        if rc:
            res["fetch_error"] = err[-400:]
    res["branch"] = _git("rev-parse", "--abbrev-ref", "HEAD")[1]
    res["head"] = (_commits("-1") or [{}])[0]
    rc, up, _ = _git("rev-parse", "--abbrev-ref", "@{u}")
    res["upstream"] = up if rc == 0 else None
    if res["upstream"]:
        res["behind"] = _commits("HEAD..@{u}")
        res["ahead"] = len(_commits("@{u}..HEAD"))
    else:
        res["behind"], res["ahead"] = [], 0
    res["dirty"] = [l[3:] for l in _git("status", "--porcelain", "--untracked-files=no")[1].splitlines()]
    return res


def _restart():
    """start.cmd перезапускает сервер по коду 3; иначе — помощник, который дождётся порта и запустит нас заново."""
    if os.environ.get("PROPGEN_SUPERVISED") == "1":
        os._exit(3)
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
             | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    subprocess.Popen([sys.executable, os.path.join(TOOLS, "restart.py"), str(int(os.environ.get("PORT") or config.get("port"))),
                      sys.executable, os.path.join(ROOT, "server.py")], cwd=ROOT, creationflags=flags, close_fds=True)
    os._exit(0)


@app.post("/api/update")
def do_update():
    """git pull --ff-only → при изменении requirements.txt — pip install → перезапуск сервера."""
    info = update_info(fetch=1)
    if not info["git"]:
        raise HTTPException(400, "propgen установлен не через git — скачай новую версию с GitHub вручную")
    with lock:
        if state["current"] or state["queued"]:
            raise HTTPException(409, "идёт задача — обнови, когда очередь опустеет")
    if info["dirty"]:
        raise HTTPException(409, "в файлах есть локальные правки, обновление их затрёт: " + ", ".join(info["dirty"][:8]))
    if not info["behind"]:
        return {"updated": False, **info}
    req = os.path.join(ROOT, "requirements.txt")
    before = open(req, "rb").read() if os.path.exists(req) else b""
    rc, out, err = _git("pull", "--ff-only", timeout=180)
    if rc:
        raise HTTPException(500, "git pull не прошёл: " + (err or out)[-600:])
    pip = None
    if os.path.exists(req) and open(req, "rb").read() != before:  # новые зависимости — до перезапуска
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-r", req], cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=1800, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        pip = "ok" if r.returncode == 0 else (r.stdout + r.stderr)[-800:]
    threading.Timer(1.0, _restart).start()
    return {"updated": True, "pulled": len(info["behind"]), "pip": pip, "restarting": True}


# ---------- настройки и проверка окружения

def _mask(k):
    return (k[:3] + "…" + k[-4:]) if len(k) > 10 else ("•••" if k else "")


@app.get("/api/settings")
def get_settings():
    cfg = config.load()
    key = config.openai_key()
    cfg["openai_key"] = _mask(key)
    return {"config": cfg, "defaults": config.DEFAULTS, "has_key": bool(key),
            "detected": {"blender": config.find_blender(), "lms": config.find_lms(), "comfy_launch": config.find_comfy_launch()}}


@app.post("/api/settings")
def set_settings(body: dict):
    ch = {k: v for k, v in body.items() if k in config.DEFAULTS}
    if "openai_key" in ch and "…" in str(ch["openai_key"]):
        ch.pop("openai_key")  # прислали маску — ключ не меняли
    for k in ("blender", "lms", "comfy_launch", "models_dir"):
        if isinstance(ch.get(k), str):  # «Копировать как путь» в Проводнике добавляет кавычки
            ch[k] = ch[k].strip().strip('"').strip()
    for k in ("port", "openai_daily_usd"):
        if k in ch:
            ch[k] = (int if k == "port" else float)(ch[k] or 0)
    config.save(ch)
    _LOCAL["t"] = 0  # путь к lms или модель могли поменяться — перепроверить при следующем переводе
    return get_settings()


@app.post("/api/settings/test_openai")
def test_openai(body: dict):
    """Проверка ключа: список моделей — бесплатный запрос. Ключ из тела (ещё не сохранён) или из настроек."""
    import urllib.error
    import urllib.request
    key = (body.get("key") or "").strip()
    if not key or "…" in key:
        key = config.openai_key()
    if not key:
        return {"ok": False, "error": "ключа нет"}
    try:
        req = urllib.request.Request("https://api.openai.com/v1/models", headers={"Authorization": "Bearer " + key})
        ids = {m["id"] for m in json.loads(urllib.request.urlopen(req, timeout=20).read())["data"]}
        model = body.get("model") or config.get("openai_model")
        return {"ok": model in ids, "model": model,
                **({} if model in ids else {"error": f"ключ рабочий, но модели {model} в нём нет"})}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": "ключ не подходит" if e.code == 401 else f"OpenAI ответил {e.code}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"нет связи с OpenAI: {e}"}


@app.get("/api/doctor")
def get_doctor():
    return doctor.check_all()


@app.get("/api/debug")
def get_debug():
    """Отладочный архив (тот же, что debug.cmd): сводка, doctor, логи, последние пропы — без ключей.
    Импорт здесь, а не наверху: ошибка в tools/debug.py не должна мешать серверу подняться."""
    import debug  # noqa: E402 — tools/ уже в sys.path
    try:
        path = debug.collect()
    except Exception as e:  # noqa: BLE001 — в т.ч. отказ verify(): архив с ключом не отдаём
        raise HTTPException(500, f"отчёт не собрался: {e}")
    return FileResponse(path, media_type="application/zip", filename=os.path.basename(path))


@app.get("/api/engines")
def get_engines():
    """Какие необязательные движки есть в ComfyUI прямо сейчас (быстро — без полного /api/doctor)."""
    return {"krea2": doctor.krea_files()}


# ---------- запуск ComfyUI из интерфейса: кнопка «Запустить ComfyUI», когда он не отвечает
# Лаунчер — config.comfy_launch_plan(): exe Desktop, .bat portable или main.py (python рядом, у Desktop 2 — его аргументы)

COMFY_START = {"at": None, "path": "", "kind": "", "proc": None}
COMFY_ALT = ("http://127.0.0.1:8188", "http://127.0.0.1:8000", "http://127.0.0.1:8189")  # как doctor.check_comfy


def _comfy_alive(url, timeout=2.0):
    import urllib.request
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/system_stats", timeout=timeout):
            return True
    except Exception:  # noqa: BLE001
        return False


@app.get("/api/comfy/start")
def comfy_start_state():
    """Для опроса из интерфейса: запускали ли и когда, отвечает ли ComfyUI — по comfy_url или на соседнем порту."""
    from concurrent.futures import ThreadPoolExecutor
    url = config.get("comfy_url").rstrip("/")
    at = COMFY_START["at"]
    # все адреса разом: отказ в соединении с localhost на Windows приходит лишь через ~1–2 с, а опрос — раз в 2 с
    urls = [url] + [a for a in COMFY_ALT if a != url]
    with ThreadPoolExecutor(len(urls)) as ex:
        alive = dict(zip(urls, ex.map(_comfy_alive, urls)))
    out = {"ok": alive[url], "url": url, "launch": config.find_comfy_launch(),
           "started": at is not None, "started_at": at, "elapsed": round(time.time() - at) if at else None,
           "path": COMFY_START["path"], "kind": COMFY_START["kind"]}
    if not out["ok"]:
        # Desktop поднимает сервер на своём порту (у 1.x по умолчанию 8000) — интерфейс предложит поправить адрес
        out["found_at"] = next((a for a in urls[1:] if alive[a]), None)
        p = COMFY_START["proc"]
        if p is not None and COMFY_START["kind"] in ("main", "desktop2") and p.poll() is not None:
            out["exited"] = p.returncode  # python ComfyUI вышел, так и не ответив, — ждать дальше незачем
    return out


@app.post("/api/comfy/start")
def comfy_start():
    st = comfy_start_state()
    if st["ok"] or st["found_at"]:
        return {**st, "already": True}  # второй ComfyUI рядом — двойной расход видеопамяти, не запускаем
    p = COMFY_START["proc"]
    if COMFY_START["at"] and time.time() - COMFY_START["at"] < 180 and p is not None and p.poll() is None:
        return {**st, "already": False, "starting": True}  # повторное нажатие, пока он ещё поднимается
    plan = config.comfy_launch_plan()
    if not plan:
        raise HTTPException(409, "Не нашёл, чем запускать ComfyUI — укажите путь в Настройки → ComfyUI и Blender → «Запуск ComfyUI»")
    if plan.get("error"):
        raise HTTPException(409, f"{plan['path']}: {plan['error']}")
    kw = {"cwd": plan["cwd"], "close_fds": True}
    flags = 0
    if os.name != "nt":
        kw.update(start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif plan["kind"] == "exe":
        # у Desktop своё окно; отвязываем от консоли и Ctrl+C сервера, чтобы он пережил перезапуск propgen
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        kw.update(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        # .bat и main.py — в своём консольном окне (свёрнутом, без кражи фокуса): там лог ComfyUI, а закрыть окно =
        # остановить ComfyUI. Без своей консоли процесс был бы невидимым и его пришлось бы искать в диспетчере задач
        flags = subprocess.CREATE_NEW_CONSOLE
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 7  # SW_SHOWMINNOACTIVE
        kw["startupinfo"] = si
    try:
        try:  # из задачи-«job» (если сервер в ней) — наружу, иначе ComfyUI умрёт вместе с сервером
            proc = subprocess.Popen(plan["argv"], creationflags=flags | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0), **kw)
        except OSError:  # job не разрешает выход — запускаем как есть
            proc = subprocess.Popen(plan["argv"], creationflags=flags, **kw)
    except OSError as e:
        raise HTTPException(500, f"не запустился {plan['path']}: {e}")
    COMFY_START.update(at=time.time(), path=plan["path"], kind=plan["kind"], proc=proc)
    out = {**comfy_start_state(), "already": False}
    if plan["kind"] == "exe" and os.path.basename(plan["path"]).lower() == "comfy desktop.exe":
        out["note"] = "Comfy Desktop откроет панель установок — запустите в ней ComfyUI"
    return out


@app.post("/api/doctor/download")
def start_download(body: dict):
    if doctor.DL["active"]:
        raise HTTPException(409, "уже качаю")
    groups = tuple(body.get("groups") or ["core"])
    threading.Thread(target=doctor.download, args=(groups,), kwargs={"log": lambda m: None}, daemon=True).start()
    return doctor.DL


@app.get("/api/doctor/download")
def download_status():
    return doctor.DL


LMS_GET = {"active": False, "log": "", "ok": None}


@app.post("/api/doctor/local_model")
def get_local_model():
    """Скачать локальную текстовую модель через CLI LM Studio (lms get)."""
    lms = config.find_lms()
    if not lms:
        raise HTTPException(400, "LM Studio не найден — установи его с lmstudio.ai и запусти один раз")
    if LMS_GET["active"]:
        return LMS_GET

    def run():
        LMS_GET.update(active=True, log="", ok=None)
        p = subprocess.run([lms, "get", config.get("local_model"), "-y"], capture_output=True, text=True, timeout=3600)
        LMS_GET.update(active=False, log=(p.stdout + p.stderr)[-2000:], ok=p.returncode == 0)
    threading.Thread(target=run, daemon=True).start()
    return LMS_GET


@app.get("/api/doctor/local_model")
def local_model_status():
    return LMS_GET


app.mount("/files", StaticFiles(directory=PROPS), name="files")


@app.get("/")
def index():
    return FileResponse(os.path.join(ROOT, "web", "index.html"))


app.mount("/", StaticFiles(directory=os.path.join(ROOT, "web")), name="web")

if __name__ == "__main__":
    # незавершённые после перезапуска шаги — в ошибку, чтобы не висели «в работе»
    for pid in os.listdir(PROPS):
        if os.path.exists(os.path.join(PROPS, pid, "meta.json")):
            def fix(m):
                if "cropped" not in m:  # старые пропы: рамку ставили, если кадр меньше исходного фото
                    try:
                        m["cropped"] = Image.open(pdir(m["id"], "crop.png")).size != Image.open(pdir(m["id"], m["photos"][0])).size
                    except Exception:  # noqa: BLE001
                        m["cropped"] = False
                if llm.has_cyrillic(m.get("desc")) and not m.get("notes"):  # старые пропы: русское описание → заметка
                    m["notes"], m["desc"] = m["desc"], ""
                for s in m["steps"].values():
                    if s.get("status") in ("queued", "running"):
                        s.update(status="error", error="прервано перезапуском сервера")
            update(pid, fix)
    port = int(os.environ.get("PORT") or config.get("port"))
    if os.environ.get("PROPGEN_OPEN") == "1":
        # браузер — когда сервер уже отвечает: start.cmd открывал его заранее, и первым экраном была ошибка соединения
        def open_when_ready():
            import urllib.request
            import webbrowser
            for _ in range(300):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version", timeout=1).close()
                    break
                except Exception:  # noqa: BLE001
                    time.sleep(0.2)
            webbrowser.open(f"http://127.0.0.1:{port}/")
        threading.Thread(target=open_when_ready, daemon=True).start()
    print(f"propgen: http://127.0.0.1:{port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
