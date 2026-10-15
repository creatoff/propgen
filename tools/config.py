"""Настройки propgen: config.json в папке данных (в git-режиме — корень проекта, в .gitignore) поверх умолчаний.
Пустая строка = «найти автоматически». Читают сервер и все скрипты; правятся на экране настроек или руками.

Два режима:
- git (клон репозитория, setup.cmd): данные там же, где код — DATA = ROOT, как было всегда;
- установленный (инсталлятор кладёт ROOT/installed.json): код в %LOCALAPPDATA%\\Programs\\propgen, его целиком
  заменяет следующее обновление, поэтому данные отдельно — %LOCALAPPDATA%\\propgen (config.json, .openai_key, work\\).
PROPGEN_DATA переопределяет папку данных в любом режиме (проверки, вторая копия рядом с первой).
"""
import glob
import json
import os
import shlex
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIN = sys.platform == "win32"
INSTALLED_FILE = os.path.join(ROOT, "installed.json")
INSTALLED = os.path.exists(INSTALLED_FILE)


def _data_dir():
    env = os.environ.get("PROPGEN_DATA", "").strip().strip('"')
    if env:
        return os.path.abspath(os.path.expanduser(env))
    if not INSTALLED:
        return ROOT
    base = os.environ.get("LOCALAPPDATA") if WIN else os.environ.get("XDG_DATA_HOME")
    return os.path.join(base or os.path.expanduser(os.path.join("~", ".local", "share")), "propgen")


DATA = _data_dir()                        # config.json, .openai_key, work\
WORK = os.path.join(DATA, "work")         # пропы, логи, кэш и расход LLM, debug-*.zip, server.log
PATH = os.path.join(DATA, "config.json")
KEY_FILE = os.path.join(DATA, ".openai_key")
if DATA != ROOT:
    try:  # отдельная папка данных создаётся при первом запуске; ошибку покажет первая же запись в неё
        os.makedirs(WORK, exist_ok=True)
    except OSError:
        pass

DEFAULTS = {
    "port": 8795,
    "comfy_url": "http://127.0.0.1:8188",
    "comfy_launch": "",          # чем запускать ComfyUI: exe Desktop, run_nvidia_gpu.bat или main.py; пусто — автопоиск
    "models_dir": "",            # куда докачивать модели; пусто — первая папка нужного типа из настроек самого ComfyUI
    "blender": "",               # путь к blender(.exe); пусто — поиск в PATH и стандартных местах
    "llm_mode": "auto",          # auto: OpenAI, если есть ключ, иначе локально · openai · local · off
    "openai_key": "",            # ключ OpenAI (или файл .openai_key в папке данных)
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
    os.makedirs(DATA, exist_ok=True)
    with open(PATH + ".tmp", "w", encoding="utf-8") as f:
        json.dump(stored, f, ensure_ascii=False, indent=1)
    os.replace(PATH + ".tmp", PATH)
    return cfg


def get(key):
    return load()[key]


def openai_key():
    k = get("openai_key").strip()
    if not k and os.path.exists(KEY_FILE):
        with open(KEY_FILE, encoding="utf-8") as f:
            k = f.read().strip()
    return k


def app_python():
    """Python приложения: у установленного — встроенный python\\python.exe, в git-режиме — .venv (после setup.cmd)."""
    for p in (os.path.join(ROOT, "python", "python.exe" if WIN else os.path.join("bin", "python3")),
              os.path.join(ROOT, ".venv", "Scripts", "python.exe") if WIN else os.path.join(ROOT, ".venv", "bin", "python")):
        if os.path.exists(p):
            return p
    return ""


def version_info():
    """Какая версия запущена: у установленной — из installed.json (его пишет сборка), в git-режиме — короткий sha."""
    if INSTALLED:
        d = _read_json(INSTALLED_FILE) or {}
        return {"installed": True, "version": str(d.get("version") or ""), "tag": str(d.get("tag") or ""),
                "commit": str(d.get("commit") or ""), "built": str(d.get("built") or "")}
    head = ""
    if os.path.isdir(os.path.join(ROOT, ".git")) and shutil.which("git"):
        try:
            r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                               timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            head = r.stdout.strip() if r.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            pass
    return {"installed": False, "version": head, "tag": "", "commit": head, "built": ""}


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


# ---------- запуск ComfyUI из интерфейса (POST /api/comfy/start)

def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def desktop2_installs():
    """Локальные установки нового ComfyUI Desktop («Comfy Desktop», настройки в %APPDATA%\\Comfy Desktop), свежие первыми.

    Его exe при старте открывает только панель выбора установки и сервер сам не поднимает (если в нём не включён
    автозапуск), поэтому запускаем установку напрямую — её же python и с теми же папками моделей, входа и выхода,
    что передаёт Desktop: без --extra-model-paths-config ComfyUI не увидит модели из общих папок."""
    base = os.path.join(os.environ.get("APPDATA", ""), "Comfy Desktop")
    insts = _read_json(os.path.join(base, "installations.json")) if WIN and os.environ.get("APPDATA") else None
    if not isinstance(insts, list):
        return []
    sett = _read_json(os.path.join(base, "settings.json")) or {}
    out = []
    for i in insts:
        root = i.get("installPath") or ""
        main = os.path.join(root, "ComfyUI", "main.py")
        if not root or i.get("status", "installed") != "installed" or not os.path.exists(main):
            continue  # облачная «установка» (Comfy Cloud), недокачанная или удалённая папка
        args = [a.strip('"') for a in shlex.split(i.get("launchArgs") or "", posix=False)]
        paths = os.path.join(base, "instance-model-paths", f"{i.get('id')}.yaml")
        if os.path.exists(paths):
            args += ["--extra-model-paths-config", paths]
        for key, flag in (("inputDir", "--input-directory"), ("outputDir", "--output-directory")):
            if sett.get(key):
                args += [flag, sett[key]]
        out.append({"main": main, "args": args, "name": i.get("name") or "", "last": i.get("lastLaunchedAt") or 0})
    return sorted(out, key=lambda x: -x["last"])


def _comfy_candidates():
    """Типичные места установки — по шаблонам и переменным окружения, без обхода дисков."""
    if not WIN:
        return [os.path.expanduser("~/ComfyUI/main.py")]
    c = [d["main"] for d in desktop2_installs()]
    local, home = os.environ.get("LOCALAPPDATA"), os.environ.get("USERPROFILE")
    if local:  # Desktop 1.x: его exe сам поднимает сервер (по умолчанию на 8000)
        c.append(os.path.join(local, "Programs", "ComfyUI", "ComfyUI.exe"))
    roots = [f"{d}:\\" for d in "CDE"] + ([home] + [os.path.join(home, x) for x in ("Desktop", "Downloads", "Documents")] if home else [])
    for r in roots:  # portable: архив распаковывают в ComfyUI_windows_portable (иногда с суффиксом версии)
        for p in sorted(glob.glob(os.path.join(r, "ComfyUI_windows_portable*")), reverse=True):
            c += [os.path.join(p, "run_nvidia_gpu.bat"), os.path.join(p, "run_cpu.bat")]
    c += [os.path.join(r, "ComfyUI", "main.py") for r in roots]  # git-установка
    return c


def find_comfy_launch():
    """Чем запускать ComfyUI: путь из настроек (comfy_launch), иначе автопоиск. Пусто — не нашли."""
    p = (get("comfy_launch") or "").strip().strip('"')
    if p and os.path.exists(p):
        return p
    return next((c for c in _comfy_candidates() if os.path.exists(c)), "")


def _python_near(main):
    """Python установки ComfyUI рядом с main.py: venv/.venv (git, Desktop 2) или python_embeded (portable)."""
    d = os.path.dirname(main)
    rel = ("Scripts", "python.exe") if WIN else ("bin", "python")
    for v in ("venv", ".venv"):
        if os.path.exists(os.path.join(d, v, *rel)):
            return os.path.join(d, v, *rel)
    emb = os.path.join(os.path.dirname(d), "python_embeded", "python.exe")
    if os.path.exists(emb):
        return emb
    # системный python — последняя надежда (conda и т.п.); python проекта не годится: в нём нет torch
    return shutil.which("python") or shutil.which("python3") or ""


def comfy_launch_plan(path=None):
    """Как запустить ComfyUI: {"path", "kind", "argv", "cwd"} (kind: exe · bat · main · desktop2) или {"error"};
    None — запускать нечем. main.py получает --port из comfy_url: ComfyUI должен подняться там, где его ждёт propgen."""
    import urllib.parse
    path = (path or find_comfy_launch()).strip().strip('"')
    if not path or not os.path.exists(path):
        return None
    path = os.path.abspath(path)
    folder, low = os.path.dirname(path), path.lower()
    if os.path.basename(low) == "comfy desktop.exe":
        # лаунчер Desktop 2 сам сервер не поднимает (только панель установок) — указали его, значит хотят «запусти
        # ComfyUI»: запускаем его последнюю установку напрямую, как это делает он сам
        d2 = desktop2_installs()
        if d2:
            return comfy_launch_plan(d2[0]["main"])
    if low.endswith((".bat", ".cmd")):
        return {"path": path, "kind": "bat", "argv": ["cmd", "/c", path], "cwd": folder}
    if low.endswith(".py"):
        py = _python_near(path)
        if not py:
            return {"path": path, "error": "рядом с main.py нет venv/.venv/python_embeded, и python не найден в PATH"}
        d2 = next((d for d in desktop2_installs() if os.path.normcase(d["main"]) == os.path.normcase(path)), None)
        args = list(d2["args"]) if d2 else []
        argv = [py, "-s", path] + (["--windows-standalone-build"] if "python_embeded" in py else []) + args
        u = urllib.parse.urlparse(get("comfy_url"))
        if u.port and u.hostname in ("127.0.0.1", "localhost") and "--port" not in args:
            argv += ["--port", str(u.port)]
        return {"path": path, "kind": "desktop2" if d2 else "main", "argv": argv, "cwd": folder}
    return {"path": path, "kind": "exe", "argv": [path], "cwd": folder}


def venv_python():
    """Python проекта (с pymeshlab и т.п.) — его зовут скрипты, работающие внутри Blender."""
    return os.environ.get("PROPGEN_PY") or sys.executable


if __name__ == "__main__":
    # для .cmd: «python tools\config.py port» — без кавычек внутри for /f (cmd срезает внешние и ломает команду)
    # только по одному полю и никогда — ключ: весь конфиг в консоль не печатаем
    if len(sys.argv) != 2 or "key" in sys.argv[1]:
        sys.exit("python tools/config.py <поле>, например port")
    print(get(sys.argv[1]))
