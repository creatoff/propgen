"""Отладочный архив propgen: одна команда собирает всё, что нужно, чтобы понять чужую поломку по переписке.

    python tools/debug.py            → work/debug-<дата-время>.zip в папке данных (путь печатается последней строкой)

Только stdlib: должен работать, когда не стоит .venv, не установлены зависимости и не запущен сервер —
именно в этих случаях он нужнее всего. Тот же сборщик зовёт сервер (GET /api/debug → collect()).
Каждый пункт независим: упавший пишется в errors.txt, остальные собираются дальше.
Ключи и токены в архив не попадают: config.json маскируется, файл .openai_key не читается в архив,
все тексты проходят через scrub(), а в конце verify() перечитывает архив и ищет в нём ключ.
"""
import datetime
import glob
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
import zipfile

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
KEEP = 5  # столько последних debug-*.zip оставляем в work — кнопку могут нажать много раз

sys.path.insert(0, TOOLS)
try:
    import config  # tools/config.py — тоже только stdlib
except Exception:  # noqa: BLE001 — сломанный config.py не должен лишать нас остального отчёта
    config = None

# режим и папка данных — как в config.py; без него (сломан) угадываем сами, чтобы архив всё равно собрался
INSTALLED = config.INSTALLED if config else os.path.exists(os.path.join(ROOT, "installed.json"))
DATA = config.DATA if config else (os.path.join(os.environ.get("LOCALAPPDATA", ""), "propgen") if INSTALLED else ROOT)
WORK = os.path.join(DATA, "work")
PROPS = os.path.join(WORK, "props")
# Python приложения: встроенный python\ установленной версии, иначе .venv git-клона (после setup.cmd)
if sys.platform == "win32":
    VENV_PY = next((p for p in (os.path.join(ROOT, "python", "python.exe"), os.path.join(ROOT, ".venv", "Scripts", "python.exe"))
                    if os.path.exists(p)), os.path.join(ROOT, ".venv", "Scripts", "python.exe"))
else:
    VENV_PY = os.path.join(ROOT, ".venv", "bin", "python")
PY_NAME = "python\\" if os.path.basename(os.path.dirname(VENV_PY)) == "python" else ".venv"

SECRET_FIELD = re.compile(r"key|token|secret|passw|auth", re.I)
# узнаваемые ключи в логах и чужих выводах: OpenAI (sk-, sk-proj-), Hugging Face (hf_), GitHub (ghp_ и т.п.)
SECRET_RX = re.compile(r"\b(sk-[A-Za-z0-9_\-]{16,}|hf_[A-Za-z0-9]{16,}|gh[pousr]_[A-Za-z0-9]{20,})")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def mask(s):
    """Маска, по которой можно сверить «тот ли ключ», но не восстановить его: sk-…abcd."""
    s = str(s)
    if not s:
        return ""
    return f"{s[:3]}…{s[-4:]}" if len(s) >= 16 else "***"


def _cfg():
    return config.load() if config else {}


def _secrets():
    """Строки, которых не должно быть в архиве: ключ OpenAI (из config.json или .openai_key) и прочие секретные поля."""
    out = set()
    try:
        if config:
            out.add(config.openai_key())
    except Exception:  # noqa: BLE001
        pass
    try:
        with open(os.path.join(DATA, "config.json"), encoding="utf-8") as f:
            raw = json.load(f)
        out |= {str(v) for k, v in raw.items() if SECRET_FIELD.search(k) and isinstance(v, str)}
    except Exception:  # noqa: BLE001
        pass
    # совсем короткие строки не ищем: «abc» встретится где угодно, а настоящих ключей такой длины не бывает
    return [s.strip() for s in out if s and len(s.strip()) >= 8]


def scrub(text, secrets=None):
    secrets = _secrets() if secrets is None else secrets
    for s in secrets:
        text = text.replace(s, mask(s))
    return SECRET_RX.sub(lambda m: mask(m[1]), text)


def tail(path, lines=300, max_bytes=4 << 20):
    """Последние строки большого лога без чтения всего файла."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        f.seek(max(0, size - max_bytes))
        data = f.read()
    text = data.decode("utf-8", errors="replace")
    rows = text.splitlines()
    head = f"[{path} — {size} байт, последние {min(lines, len(rows))} строк]\n" if len(rows) > lines else ""
    return head + "\n".join(rows[-lines:]) + "\n"


def run(cmd, timeout=30, cwd=ROOT):
    # PYTHONIOENCODING: дочерний Python в трубу пишет в cp1251 и падает на кириллице (doctor.py --json)
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=cwd,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    # слева не обрезаем: у `git status --short` первый пробел — часть статуса (" M файл")
    return p.returncode, (p.stdout or "").rstrip(), (p.stderr or "").strip()


def http_json(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def gb(b):
    return f"{(b or 0) / 2**30:.1f} ГБ"


class Bundle:
    """Файлы архива в памяти + строки сводки + ошибки сбора."""

    def __init__(self):
        self.files = {}
        self.summary = []
        self.errors = []
        self.secrets = _secrets()

    def add(self, name, data):
        if isinstance(data, (dict, list)):
            data = json.dumps(data, ensure_ascii=False, indent=1, default=str)
        self.files[name] = scrub(str(data), self.secrets)

    def line(self, text=""):
        self.summary.append(text)

    def step(self, name, fn):
        t = time.time()
        try:
            fn()
        except Exception:  # noqa: BLE001 — пункт отчёта не должен ронять остальные
            self.errors.append(f"== {name} ({time.time() - t:.1f} с)\n{traceback.format_exc()}")
            self.line(f"  ! {name}: не собралось — см. errors.txt")


# ---------- пункты отчёта


def sec_system(b):
    b.line(f"Собран:      {datetime.datetime.now().astimezone():%Y-%m-%d %H:%M:%S %z}")
    b.line(f"ОС:          {platform.platform()} · {platform.machine()} · ядер {os.cpu_count()}")
    b.line(f"Проект:      {ROOT}")
    if INSTALLED:
        try:
            with open(os.path.join(ROOT, "installed.json"), encoding="utf-8") as f:
                inst = json.load(f)
        except Exception as e:  # noqa: BLE001
            inst = {"error": str(e)}
        b.add("installed.json", inst)
        b.line(f"Режим:       установлен — версия {inst.get('version') or '?'} ({inst.get('tag') or '—'}, "
               f"коммит {str(inst.get('commit') or '—')[:10]}, собран {inst.get('built') or '?'})")
    else:
        b.line("Режим:       git-клон (setup.cmd), версия — коммит в строке git")
    same = os.path.normcase(os.path.abspath(DATA)) == os.path.normcase(ROOT)
    b.line(f"Данные:      {DATA}" + (" (= папка проекта)" if same else ""))
    b.line(f"Python:      {sys.version.split()[0]} — {sys.executable}")
    if os.path.exists(VENV_PY):
        try:
            v = run([VENV_PY, "--version"], 20)[1]
        except Exception as e:  # noqa: BLE001
            v = f"не запускается: {e}"
        b.line(f"{PY_NAME + ':':<13}есть — {v}")
    elif INSTALLED:
        b.line("python\\:     НЕТ — установка повреждена, поставьте propgen заново")
    else:
        b.line(".venv:       НЕТ — нужно запустить setup.cmd")
    env = {k: v for k, v in os.environ.items() if k.startswith(("PROPGEN", "CUDA", "PYTORCH", "HF_", "COMFY"))
           and not SECRET_FIELD.search(k)}
    if env:
        b.line("Переменные:  " + ", ".join(f"{k}={v}" for k, v in sorted(env.items())))


def sec_git(b):
    if INSTALLED:
        return  # установленная версия обновляется из GitHub Releases, git ей не нужен; версия — в строке «Режим»
    if not shutil.which("git"):
        b.line("git:         не установлен (обновление из интерфейса не заработает)")
        return
    if not os.path.isdir(os.path.join(ROOT, ".git")):
        b.line("git:         папка не из git clone (скачан zip?) — обновление из интерфейса не заработает")
        return
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])[1]
    head = run(["git", "log", "-1", "--format=%h %cI «%s»"])[1]
    b.line(f"git:         ветка {branch}, коммит {head}")
    # fetch — чтобы «отстаёт на N» было про GitHub, а не про последний pull; без сети просто пишем, что не вышло
    try:
        rc, _, err = run(["git", "fetch", "--quiet"], 25)
        fetched = "" if rc == 0 else f" (fetch не удался: {err.splitlines()[-1] if err else rc})"
    except subprocess.TimeoutExpired:
        fetched = " (fetch: нет ответа за 25 с)"
    rc, up, _ = run(["git", "rev-parse", "--abbrev-ref", "@{u}"])
    if rc == 0:
        behind = run(["git", "rev-list", "--count", "HEAD..@{u}"])[1]
        ahead = run(["git", "rev-list", "--count", "@{u}..HEAD"])[1]
        b.line(f"             {up}: отстаёт на {behind}, впереди на {ahead}{fetched}")
    else:
        b.line(f"             нет upstream-ветки{fetched}")
    st = run(["git", "status", "--short"])[1]
    b.add("git_status.txt", st or "(чисто)")
    n = len(st.splitlines()) if st else 0
    b.line(f"             локальных правок: {n}" + (" — список в конце сводки и в git_status.txt" if n else ""))
    b.git_status = st


def sec_comfy(b):
    url = (_cfg().get("comfy_url") or "http://127.0.0.1:8188").rstrip("/")
    find = getattr(config, "find_comfy_launch", None)  # чем кнопка «Запустить ComfyUI» стала бы его запускать
    b.line(f"Запуск ComfyUI: {(find and find()) or 'не найден — поле comfy_launch в настройках'}")
    try:
        st = http_json(url + "/system_stats", 5)
    except Exception as e:  # noqa: BLE001
        b.line(f"ComfyUI:     НЕ ОТВЕЧАЕТ по {url} ({e})")
        b.comfy_url = None
        for alt in ("http://127.0.0.1:8188", "http://127.0.0.1:8000", "http://127.0.0.1:8189"):
            if alt != url:
                try:
                    http_json(alt + "/system_stats", 2)
                except Exception:  # noqa: BLE001
                    continue
                b.line(f"             но отвечает на {alt} — поправить comfy_url в настройках; дальше данные с него")
                url, b.comfy_url = alt, alt  # очередь, ноды и лог всё равно пригодятся
                break
        if not b.comfy_url:
            return
        st = http_json(url + "/system_stats", 5)
    b.comfy_url = url
    b.add("comfy_system_stats.json", st)
    s = st.get("system", {})
    b.line(f"ComfyUI:     {url} — версия {s.get('comfyui_version')}, Python {str(s.get('python_version', '')).split()[0]}, "
           f"PyTorch {s.get('pytorch_version')}")
    for d in st.get("devices") or []:
        b.line(f"GPU:         {d.get('name')} — VRAM {gb(d.get('vram_total'))}, свободно {gb(d.get('vram_free'))}")
    b.line(f"RAM:         {gb(s.get('ram_total'))}, свободно {gb(s.get('ram_free'))}")
    if s.get("argv"):
        b.line(f"             запуск: {' '.join(map(str, s['argv']))}")


def sec_comfy_extra(b):
    url = getattr(b, "comfy_url", None)
    if not url:
        return
    q = http_json(url + "/queue", 5)
    b.add("comfy_queue.json", q)
    b.line(f"             очередь ComfyUI: выполняется {len(q.get('queue_running') or [])}, ждут {len(q.get('queue_pending') or [])}")
    info = http_json(url + "/object_info", 60)  # большой: в архив только имена нод
    b.add("comfy_object_info_nodes.txt", "\n".join(sorted(info)))
    b.line(f"             нод в ComfyUI: {len(info)}")


def sec_comfy_log(b):
    """Лог ComfyUI: сначала у живого ComfyUI (/internal/logs), потом файлы Desktop-версии и папки user."""
    url = getattr(b, "comfy_url", None)
    got = []
    if url:
        try:
            with urllib.request.urlopen(url + "/internal/logs", timeout=10) as r:
                text = json.loads(r.read())
            if isinstance(text, str) and text.strip():
                rows = ANSI.sub("", text).splitlines()
                b.add("comfy_log.txt", "\n".join(rows[-3000:]))
                got.append("comfy_log.txt (из /internal/logs)")
        except Exception as e:  # noqa: BLE001 — старые ComfyUI без этого пути
            b.errors.append(f"== comfy /internal/logs\n{e}")
    # файлы: Desktop пишет в %APPDATA%\ComfyUI\logs, у portable/git — <ComfyUI>/user/comfyui*.log
    bases = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        bases.append(os.path.join(appdata, "ComfyUI", "logs"))
        try:
            with open(os.path.join(appdata, "ComfyUI", "config.json"), encoding="utf-8") as f:
                bp = json.load(f).get("basePath")
            if bp:
                bases.append(os.path.join(bp, "user"))
        except Exception:  # noqa: BLE001
            pass
    # новый ComfyUI Desktop (Comfy-Desktop) держит установки в %LOCALAPPDATA% — и лог там же; нужен, когда ComfyUI лежит
    local = os.environ.get("LOCALAPPDATA")
    if local:
        for inst in glob.glob(os.path.join(local, "Comfy-Desktop", "ComfyUI-Installs", "*")):
            bases += [os.path.join(inst, "logs"), os.path.join(inst, "ComfyUI", "user")]
    if url:
        try:  # корень ComfyUI = родитель папки models у любой из его папок моделей
            for paths in http_json(url + "/internal/folder_paths", 10).values():
                for p in paths if isinstance(paths, list) else []:
                    p = str(p)
                    if os.sep + "models" + os.sep in p:
                        bases.append(os.path.join(p.split(os.sep + "models" + os.sep)[0], "user"))
        except Exception:  # noqa: BLE001
            pass
    files = []
    for base in dict.fromkeys(bases):
        files += glob.glob(os.path.join(base, "comfyui*.log")) + glob.glob(os.path.join(base, "main.log"))
    files = sorted(set(files), key=os.path.getmtime, reverse=True)[:4]
    for f in files:
        # две последние папки пути: у Desktop и logs\comfyui.log, и ComfyUI\user\comfyui.log — иначе имена совпадут
        parts = os.path.normpath(f).split(os.sep)[-3:-1]
        name = "comfy_logs/" + re.sub(r"[^\w.\-]+", "_", "_".join(parts)) + "_" + os.path.basename(f)
        b.add(name, ANSI.sub("", tail(f, 1500)))
        got.append(f"{name} ← {f}")
    b.line("Лог ComfyUI: " + ("\n             ".join(got) if got else "не найден"))


def sec_blender(b):
    p = config.find_blender() if config else ""
    if not p:
        b.line("Blender:     НЕ НАЙДЕН — нужен 4.2+, путь в настройках (поле blender)")
        return
    try:
        out = run([p, "--version"], 90)[1]
        ver = next((l.strip() for l in out.splitlines() if l.strip()), "версию не сообщил")
    except Exception as e:  # noqa: BLE001
        ver = f"не запускается: {e}"
    b.line(f"Blender:     {ver} — {p}")


def sec_llm(b):
    c = _cfg()
    key = bool(config.openai_key()) if config else False
    src = ""
    if key:
        src = "config.json" if str(c.get("openai_key", "")).strip() else ".openai_key"
    b.line(f"LLM:         режим {c.get('llm_mode')}, ключ OpenAI {'задан (' + src + ')' if key else 'не задан'}, "
           f"модель {c.get('openai_model')}, LM Studio {c.get('lmstudio_url')}, локальная {c.get('local_model')}")


def sec_server(b):
    port = _cfg().get("port") or 8795
    base = f"http://127.0.0.1:{port}"
    try:
        v = http_json(base + "/api/version", 5)
    except Exception as e:  # noqa: BLE001
        b.line(f"Сервер:      propgen на {base} не отвечает ({e.__class__.__name__}) — запущен ли start.cmd?")
        return
    started = datetime.datetime.fromtimestamp(v["started"]).strftime("%Y-%m-%d %H:%M") if v.get("started") else "?"
    ver = f"версия {v.get('version') or '?'}" if v.get("installed") else f"коммит {v.get('head') or '—'}"
    b.line(f"Сервер:      {base} отвечает, {ver}, запущен {started}")
    try:
        q = http_json(base + "/api/queue", 5)
        b.add("server_queue.json", q)
        cur = q.get("current")
        b.line(f"             очередь: сейчас {json.dumps(cur, ensure_ascii=False) if cur else 'ничего'}, ждут {len(q.get('queued') or [])}")
    except Exception as e:  # noqa: BLE001
        b.line(f"             /api/queue: ошибка {e}")


def sec_doctor(b):
    """doctor из python приложения (.venv или встроенный python\\): его пункт «python» проверяет зависимости того
    интерпретатора, в котором запущен."""
    in_venv = os.path.exists(VENV_PY) and os.path.normcase(os.path.abspath(sys.executable)) == os.path.normcase(VENV_PY)
    # всегда отдельным процессом, если есть .venv: падение нативного модуля внутри doctor не должно убить сбор архива
    if os.path.exists(VENV_PY):
        rc, out, err = run([VENV_PY, os.path.join(TOOLS, "doctor.py"), "--json"], 400)
        try:
            r = json.loads(out)
            how = f"{PY_NAME} (отдельным процессом)"
        except ValueError:
            b.add("doctor.json", {"error": f"doctor.py --json из {PY_NAME} не отдал JSON", "rc": rc,
                                  "stdout": out[-4000:], "stderr": err[-4000:]})
            b.line(f"Doctor:      из {PY_NAME} упал — подробности в doctor.json")
            return
    else:
        try:
            import doctor
        except Exception:  # noqa: BLE001
            b.add("doctor.json", {"error": "tools/doctor.py не импортируется", "trace": traceback.format_exc()})
            b.line("Doctor:      не импортируется — подробности в doctor.json")
            return
        r = doctor.check_all()
        how = "этот Python" + ("" if in_venv else " (без python приложения — пункт python про системный Python)")
    b.add("doctor.json", r)
    m = r.get("minimum") or {}
    if m.get("ok"):
        b.line(f"Doctor:      минимальный набор есть ({how})")
    else:
        b.line(f"Doctor:      НЕ ХВАТАЕТ: {', '.join(map(str, m.get('missing') or [])) or '?'} ({how})")
    # шаги конвейера: какие варианты рабочие, а у нерабочих — чего не хватает (первые пункты)
    for s in r.get("steps") or []:
        ok = [o["title"] for o in s.get("options", []) if o.get("ok")]
        line = f"             {s.get('state', '?'):>3} · {s.get('title')}: " + (", ".join(ok) if ok else "нет рабочего варианта")
        bad = [o for o in s.get("options", []) if not o.get("ok") and o.get("missing")]
        if not ok and bad:
            line += f" (не хватает: {'; '.join(bad[0]['missing'][:3])})"
        b.line(line)
    py = r.get("python") or {}
    if py.get("missing"):
        b.line(f"             нет Python-пакетов: {', '.join(py['missing'])}")


def sec_config(b):
    path = os.path.join(DATA, "config.json")
    if not os.path.exists(path):
        b.add("config.json", "(config.json нет — все настройки по умолчанию)")
    else:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        try:
            data = json.loads(raw)
            data = {k: (mask(v) if SECRET_FIELD.search(k) and v else v) for k, v in data.items()}
            b.add("config.json", data)
        except ValueError as e:
            b.add("config.json", f"(config.json не разбирается как JSON: {e})\n" + raw)
    if config:
        eff = {k: (mask(v) if SECRET_FIELD.search(k) and v else v) for k, v in config.load().items()}
        eff["_openai_key_file"] = os.path.exists(os.path.join(DATA, ".openai_key"))  # только факт, не содержимое
        b.add("config_effective.json", eff)


def sec_pip(b):
    py = VENV_PY if os.path.exists(VENV_PY) else sys.executable
    rc, out, err = run([py, "-m", "pip", "freeze"], 120)
    b.add("pip_freeze.txt", f"# {py}\n" + (out or err))


def sec_nvidia(b):
    exe = shutil.which("nvidia-smi")
    if not exe:
        return
    rc, out, err = run([exe], 20)
    b.add("nvidia_smi.txt", out or err)
    # табличный вывод меняется от версии к версии драйвера — для сводки спрашиваем поля явно;
    # мощность рядом с загрузкой — признак деградации ComfyUI («100%» при ~40 Вт, см. CLAUDE.md)
    rc, out, _ = run([exe, "--query-gpu=name,driver_version,memory.used,memory.total,utilization.gpu,power.draw",
                      "--format=csv,noheader"], 20)
    for row in out.splitlines() if rc == 0 else []:
        name, drv, used, total, util, pw = [x.strip() for x in row.split(",")]
        b.line(f"NVIDIA:      {name}, драйвер {drv} — занято {used} из {total}, загрузка {util}, {pw}")


def sec_server_log(b):
    p = os.path.join(WORK, "server.log")
    if os.path.exists(p) and os.path.getsize(p):
        b.add("server.log", tail(p, 2000))
    else:
        b.add("server.log", "(work/server.log пуст или его нет: сервер пишет в него только после перезапуска из интерфейса;"
                            " при запуске через start.cmd вывод — в его окне)")
    p = os.path.join(WORK, "update-install.log")  # журнал установщика обновления (/LOG из server._update_release)
    if os.path.exists(p) and os.path.getsize(p):
        b.add("update-install.log", tail(p, 600))


def sec_props(b):
    if not os.path.isdir(PROPS):
        b.line("Пропы:       work/props нет")
        return
    metas = glob.glob(os.path.join(PROPS, "*", "meta.json"))
    metas.sort(key=os.path.getmtime, reverse=True)
    b.line(f"Пропы:       всего {len(metas)}, последние:")
    for m in metas[:3]:
        d = os.path.dirname(m)
        pid = os.path.basename(d)
        b.step(f"проп {pid}", lambda d=d, pid=pid, m=m: _prop(b, d, pid, m))


def _prop(b, d, pid, m):
    with open(m, encoding="utf-8") as f:
        raw = f.read()
    b.add(f"props/{pid}/meta.json", raw)
    try:
        meta = json.loads(raw)
        steps = ", ".join(f"{k}:{(v or {}).get('status')}" for k, v in (meta.get("steps") or {}).items())
        p = meta.get("params") or {}
        b.line(f"  {pid} «{meta.get('name', '')}» {p.get('mode', '')}/{p.get('model', '')} — {steps or 'шагов не было'}")
        for k, v in (meta.get("steps") or {}).items():
            if (v or {}).get("error"):
                b.line(f"      {k}: {str(v['error']).splitlines()[0][:200]}")
    except ValueError:
        b.line(f"  {pid}: meta.json битый")
    for log in sorted(glob.glob(os.path.join(d, "logs", "*.log"))):
        b.add(f"props/{pid}/logs/{os.path.basename(log)}", tail(log, 300))


SECTIONS = [  # порядок = порядок строк в сводке; ComfyUI раньше лога ComfyUI (нужен рабочий адрес)
    ("система", sec_system), ("git", sec_git), ("сервер propgen", sec_server), ("ComfyUI", sec_comfy),
    ("ComfyUI очередь и ноды", sec_comfy_extra), ("лог ComfyUI", sec_comfy_log), ("nvidia-smi", sec_nvidia),
    ("Blender", sec_blender), ("LLM", sec_llm), ("doctor", sec_doctor), ("config", sec_config),
    ("pip freeze", sec_pip), ("server.log", sec_server_log), ("пропы", sec_props),
]


def collect(out_dir=WORK):
    """Собрать архив и вернуть путь к нему. Без исключений наружу, кроме найденного в архиве ключа."""
    b = Bundle()
    b.git_status = ""
    t0 = time.time()
    for name, fn in SECTIONS:
        b.step(name, lambda fn=fn: fn(b))
    b.line()
    b.line(f"Ошибок сбора: {len(b.errors)}" + (" — см. errors.txt" if b.errors else "") + f" · собрано за {time.time() - t0:.0f} с")
    if b.git_status:
        b.line()
        b.line("git status --short:")
        b.line(b.git_status)
    head = ["propgen — отладочный отчёт", "=" * 26,
            "Начинать с этой сводки; подробности — doctor.json (что ComfyUI видит из нод и моделей),",
            "server.log, props/<id>/logs (последние пропы), comfy_log.txt.", ""]
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"debug-{datetime.datetime.now():%Y%m%d-%H%M%S}.zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("summary.txt", scrub("\n".join(head + b.summary) + "\n", b.secrets))
        for name, data in b.files.items():
            z.writestr(name, data)
        if b.errors:
            z.writestr("errors.txt", scrub("\n\n".join(b.errors), b.secrets))
    leaks = verify(path)
    if leaks:  # не должно случаться; если всё же — такой архив не оставляем, чтобы его не отправили
        os.remove(path)
        raise RuntimeError(f"в архиве нашёлся ключ ({', '.join(leaks)}) — архив удалён")
    for old in sorted(glob.glob(os.path.join(out_dir, "debug-*.zip")))[:-KEEP]:
        try:
            os.remove(old)
        except OSError:
            pass
    return path


def verify(path):
    """Имена файлов архива, в которых встречается ключ или секрет из настроек (сам ключ не печатаем)."""
    secrets = [s.encode("utf-8") for s in _secrets()]
    bad = []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            data = z.read(name)
            if any(s in data for s in secrets):
                bad.append(name)
    return bad


# ---------- багрепорт: тот же архив + report.json (описание от человека) уходит на приёмник разработчика
# Приёмник (stacks/bugs в beelink-server) требует report.json в корне zip и заголовок X-Propgen-Report — это фильтр
# от сканеров, а не секрет: репозиторий публичный. Ответ — JSON {ok, id} или {ok: false, error} с текстом по-русски.

SHOT_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
MAX_SHOT = 8 << 20  # приёмник берёт zip до 20 МБ; скриншот 4K в PNG — около 5 МБ


def add_report(path, title, text, contact="", prop="", shot=None, shot_type="", raw=""):
    """Дописать в готовый архив report.json и скриншот. Текст человека тоже проходит scrub: ключ вставят и туда."""
    secrets = _secrets()
    rep = {
        "title": scrub(str(title or "").strip()[:200], secrets),
        "text": scrub(str(text or "").strip()[:20000], secrets),
        "contact": scrub(str(contact or "").strip()[:200], secrets),
        "prop": str(prop or "")[:80],
        "raw": scrub(str(raw or "").strip()[:20000], secrets),  # рассказ как был, до оформления ИИ
        "version": config.version_info() if config else {},
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "created": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    with zipfile.ZipFile(path, "a", zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.json", json.dumps(rep, ensure_ascii=False, indent=1))
        if shot and len(shot) <= MAX_SHOT and shot_type in SHOT_EXT:
            z.writestr(f"screenshot.{SHOT_EXT[shot_type]}", shot)
    leaks = verify(path)
    if leaks:
        os.remove(path)
        raise RuntimeError(f"в отчёте нашёлся ключ ({', '.join(leaks)}) — отчёт удалён")
    return rep


def send(path, url=None, timeout=60):
    """Отправить архив разработчику. Возвращает id отчёта; RuntimeError с понятным текстом, если не ушло."""
    url = url or (config.get("report_url") if config else "")
    if not url:
        raise RuntimeError("адрес приёма отчётов не задан (report_url)")
    with open(path, "rb") as f:
        body = f.read()
    return str(_post(url, body, "application/zip", timeout).get("id") or "")


MAX_AUDIO = 5 << 20  # ~3 мин речи в opus с запасом; приёмник режет на том же пороге


def draft(text="", audio=None, audio_type="", context=None, timeout=120):
    """Сумбурный рассказ (текст и/или запись голоса) → оформленный отчёт {transcript, title, text}.
    Распознаёт и оформляет приёмник разработчика своим ключом OpenAI — у пользователя ключ не нужен."""
    import base64
    url = (config.get("report_url") if config else "").rstrip("/")
    if not url:
        raise RuntimeError("адрес приёма отчётов не задан (report_url)")
    body = json.dumps({"text": scrub(str(text or "")[:20000]), "context": context or {},
                       "audio": base64.b64encode(audio).decode("ascii") if audio else None,
                       "audio_type": audio_type or ""}, ensure_ascii=False).encode("utf-8")
    return _post(url + "/draft", body, "application/json", timeout)


def _post(url, body, ctype, timeout):
    """POST на приёмник: JSON-ответ {ok, …}; отказ — RuntimeError с его же текстом (размер, частота, место)."""
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": ctype, "X-Propgen-Report": "1",
        "User-Agent": f"propgen/{(config.version_info().get('version') if config else '') or 'dev'}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error")
        except Exception:  # noqa: BLE001
            msg = ""
        raise RuntimeError(msg or f"приёмник ответил {e.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise RuntimeError(f"нет связи с приёмником отчётов: {getattr(e, 'reason', e)}") from None
    if not d.get("ok"):
        raise RuntimeError(d.get("error") or "приёмник не принял запрос")
    return d


def _ask(prompt):
    """Вопрос в консоли debug.cmd: подсказка — в stderr, потому что stdout батник пишет в файл (последняя строка — путь)."""
    sys.stderr.write(prompt)
    sys.stderr.flush()
    return (sys.stdin.readline() or "").strip()


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):  # кириллица в консоли Windows без chcp 65001
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    # в stderr: debug.cmd читает stdout через for /f (выводит его только в конце), а это сообщение нужно сразу
    print("Собираю отладочный отчёт propgen (до пары минут: ComfyUI, Blender, pip)…", file=sys.stderr, flush=True)
    p = collect()
    with zipfile.ZipFile(p) as z:
        n = len(z.namelist())
    # collect() уже перепроверил архив и удалил бы его при утечке — здесь только сообщаем
    print(f"Файлов в архиве: {n}; " + ("ключа OpenAI в нём нет (проверено)." if _secrets() else "ключ OpenAI не задан."))
    # отправка прямо отсюда: debug.cmd нужен как раз тогда, когда сервер с кнопкой в интерфейсе не поднялся
    if "--no-send" not in sys.argv and config and config.get("report_url") and sys.stdin and sys.stdin.isatty():
        what = _ask("\nОтправить архив разработчику? Опишите, что случилось (Enter — не отправлять):\n> ")
        if what:
            who = config.get("report_contact") or _ask("Как с вами связаться — имя, Telegram (Enter — пропустить):\n> ")
            try:
                add_report(p, what[:120], what, who)
                if who and who != config.get("report_contact"):
                    config.save({"report_contact": who})
                rid = send(p)
                print(f"Отчёт отправлен разработчику (номер {rid}). Архив остался здесь:")
            except Exception as e:  # noqa: BLE001 — архив всё равно есть, его можно прислать руками
                print(f"Отправить не удалось: {e}. Пришлите архив вручную:")
    print(p)
