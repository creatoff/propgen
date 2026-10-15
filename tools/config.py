"""Настройки propgen: config.json в корне проекта (в .gitignore) поверх значений по умолчанию.
Пустая строка = «найти автоматически». Читают сервер и все скрипты; правятся на экране настроек или руками.
"""
import glob
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "config.json")
WIN = sys.platform == "win32"

DEFAULTS = {
    "port": 8795,
    "comfy_url": "http://127.0.0.1:8188",
    "models_dir": "",            # куда докачивать модели; пусто — первая папка нужного типа из настроек самого ComfyUI
    "blender": "",               # путь к blender(.exe); пусто — поиск в PATH и стандартных местах
    "llm_mode": "auto",          # auto: OpenAI, если есть ключ, иначе локально · openai · local · off
    "openai_key": "",            # ключ OpenAI (или файл .openai_key в корне)
    "openai_model": "gpt-4.1-mini",
    "openai_daily_usd": 0.20,
    "lmstudio_url": "http://127.0.0.1:1234",
    "lms": "",                   # путь к lms(.exe) — CLI LM Studio; пусто — ~/.lmstudio/bin
    "local_model": "google/gemma-3-4b",
}


def load():
    cfg = dict(DEFAULTS)
    if os.path.exists(PATH):
        try:
            with open(PATH, encoding="utf-8") as f:
                cfg.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
        except (OSError, ValueError):
            pass
    return cfg


def save(changes):
    cfg = load()
    cfg.update({k: v for k, v in changes.items() if k in DEFAULTS})
    stored = {k: v for k, v in cfg.items() if v != DEFAULTS[k]}  # в файле только отличия от умолчаний
    with open(PATH + ".tmp", "w", encoding="utf-8") as f:
        json.dump(stored, f, ensure_ascii=False, indent=1)
    os.replace(PATH + ".tmp", PATH)
    return cfg


def get(key):
    return load()[key]


def openai_key():
    k = get("openai_key").strip()
    if not k and os.path.exists(os.path.join(ROOT, ".openai_key")):
        with open(os.path.join(ROOT, ".openai_key"), encoding="utf-8") as f:
            k = f.read().strip()
    return k


def find_blender():
    """Явный путь из настроек, иначе PATH и стандартные места установки (самая новая версия)."""
    p = get("blender")
    if p and os.path.exists(p):
        return p
    p = shutil.which("blender")
    if p:
        return p
    cands = []
    if WIN:
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", "")):
            cands += glob.glob(os.path.join(base, "Blender Foundation", "Blender*", "blender.exe"))
        cands += glob.glob(os.path.expanduser(r"~\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Steam\steamapps\common\Blender\blender.exe"))
        cands += glob.glob(r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe")
    elif sys.platform == "darwin":
        cands += glob.glob("/Applications/Blender*.app/Contents/MacOS/Blender")
    else:
        cands += glob.glob("/opt/blender*/blender") + glob.glob(os.path.expanduser("~/blender*/blender"))

    def ver(path):
        import re
        m = re.search(r"Blender[ _-]?(\d+)\.(\d+)", path)
        return (int(m[1]), int(m[2])) if m else (0, 0)
    cands = [c for c in cands if os.path.exists(c)]
    return max(cands, key=ver) if cands else ""


def find_lms():
    p = get("lms")
    if p and os.path.exists(p):
        return p
    p = os.path.expanduser(os.path.join("~", ".lmstudio", "bin", "lms.exe" if WIN else "lms"))
    return p if os.path.exists(p) else (shutil.which("lms") or "")


def venv_python():
    """Python проекта (с pymeshlab и т.п.) — его зовут скрипты, работающие внутри Blender."""
    return os.environ.get("PROPGEN_PY") or sys.executable
