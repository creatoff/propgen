"""Сборка Windows-инсталлятора propgen: свой Python + зависимости + код → проверка → Inno Setup.

    python tools\\build\\build.py --version 1.2.0          # build\\app, build\\redist, dist\\propgen-setup-1.2.0.exe
    python tools\\build\\build.py --skip-installer         # только папка build\\app и проверка (без Inno Setup)
    python tools\\build\\build.py --smoke <папка>          # проверить готовую папку программы
    python tools\\build\\build.py --install-test dist\\propgen-setup-1.2.0.exe   # установка → обновление → удаление

Только stdlib: запускается любым Python 3.10+ (в CI — setup-python), сам propgen работает на python-build-standalone,
который скачивается сюда с закреплённой версией и сверкой sha256. Раскладка build\\app = будущий {app} инсталлятора:
копия репозитория (git ls-files) + python\\ + installed.json. Скачанное кэшируется в build\\cache (в CI — actions/cache).
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BUILD = os.path.join(ROOT, "build")
APP = os.path.join(BUILD, "app")
CACHE = os.path.join(BUILD, "cache")
REDIST = os.path.join(BUILD, "redist")
DIST = os.path.join(ROOT, "dist")
ISS = os.path.join(ROOT, "installer", "propgen.iss")

# python-build-standalone: версия закреплена — обновлять осознанно (новый CPython/сборка → прогнать smoke).
# stripped — без .pdb: вдвое меньше, для работы не нужны. sha256 — из «digest» ассета релиза на GitHub.
PBS_TAG = "20260929"
PBS_FILE = "cpython-3.12.14+20260929-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
PBS_SHA256 = "f38e68f4d612ade6dd50c894fc80b14c0be0c3b5201145d6fff5f20b9323204d"
PBS_URL = (f"https://github.com/astral-sh/python-build-standalone/releases/download/{PBS_TAG}/"
           + PBS_FILE.replace("+", "%2B"))

# VC++ Redistributable: aka.ms всегда отдаёт свежий, поэтому вместо sha256 — подпись Microsoft и версия файла.
# Без свежего msvcp140 pymeshlab падает 0xC0000005 (было у пользователя), поэтому он едет внутри инсталлятора.
VC_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
VC_MIN = (14, 40)

# что из репозитория не нужно установленной программе: служебное git, сборка, скриншоты README,
# setup.cmd (создаёт .venv — в установленной копии Python свой)
EXCLUDE_PREFIX = (".claude/", ".github/", "installer/", "tools/build/", "shots/")
EXCLUDE_FILES = {".gitattributes", ".gitignore", "setup.cmd"}

# импорт каждой зависимости — отдельным процессом: нативный модуль может уронить процесс целиком (0xC0000005),
# и тогда видно, какой именно. Не только import, но и минимальная работа — DLL грузятся лениво.
SMOKE_IMPORTS = {
    "fastapi": "import fastapi, fastapi.responses, fastapi.staticfiles",
    "uvicorn": "import uvicorn",
    "multipart": "import multipart",
    "PIL": "import io; from PIL import Image, ImageOps; Image.new('RGB', (8, 8)).save(io.BytesIO(), 'PNG')",
    "numpy": "import numpy as np; np.linalg.svd(np.random.rand(16, 16))",
    "scipy": "import numpy as np, scipy.spatial; scipy.spatial.cKDTree(np.random.rand(64, 3)).query(np.zeros(3))",
    "skimage": "import numpy as np, skimage, skimage.measure; skimage.measure.label(np.eye(8, dtype=int))",
    "trimesh": "import trimesh; trimesh.creation.icosphere(subdivisions=2).export(file_type='obj')",
    # тот же путь, что tools/solidify.py: сглаживание, упрощение, починка — здесь падал старый msvcp140
    "pymeshlab": ("import os, tempfile, numpy as np, pymeshlab, trimesh\n"
                  "s = trimesh.creation.icosphere(subdivisions=4)\n"
                  "ms = pymeshlab.MeshSet(); ms.add_mesh(pymeshlab.Mesh(np.asarray(s.vertices), np.asarray(s.faces)))\n"
                  "ms.apply_coord_taubin_smoothing(stepsmoothnum=3)\n"
                  "ms.meshing_decimation_quadric_edge_collapse(targetfacenum=200, qualitythr=0.5, preservenormal=True)\n"
                  "ms.meshing_repair_non_manifold_edges()\n"
                  "p = os.path.join(tempfile.mkdtemp(), 'm.obj'); ms.save_current_mesh(p)\n"
                  "assert ms.current_mesh().face_number() <= 220, ms.current_mesh().face_number()\n"),
}


def log(msg):
    print(f"[build {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def fail(msg):
    print(f"\nОШИБКА: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def size_mb(path):
    if os.path.isfile(path):
        return os.path.getsize(path) / 2**20
    total = 0
    for d, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(d, f))
            except OSError:
                pass
    return total / 2**20


def run(cmd, **kw):
    log("$ " + " ".join(str(c) for c in cmd))
    rc = subprocess.run(cmd, **kw).returncode
    if rc:
        fail(f"команда завершилась с кодом {rc}: {cmd[0]}")


def download(url, dest):
    tmp = dest + ".part"
    log(f"скачиваю {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "propgen-build"})
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    os.replace(tmp, dest)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def clean_env(**extra):
    """Окружение для собранного Python: без PYTHONPATH/PYTHONHOME и пользовательских site-packages — иначе
    проверка может «пройти» на пакетах системного Python и ничего не сказать о сборке."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")}
    env.update(PYTHONNOUSERSITE="1", PYTHONIOENCODING="utf-8", PYTHONFAULTHANDLER="1", PYTHONUTF8="1")
    env.pop("PROPGEN_OPEN", None)
    env.pop("PROPGEN_SUPERVISED", None)
    env.update(extra)
    return env


# ---------- версия

def resolve_version(arg):
    if arg:
        return arg[1:] if arg.startswith("v") else arg
    if os.environ.get("GITHUB_REF_TYPE") == "tag" and os.environ.get("GITHUB_REF_NAME", "").startswith("v"):
        return os.environ["GITHUB_REF_NAME"][1:]
    p = subprocess.run(["git", "describe", "--tags", "--exact-match"], cwd=ROOT, capture_output=True, text=True)
    if p.returncode == 0 and p.stdout.strip().startswith("v"):
        return p.stdout.strip()[1:]
    return "0.0.0-dev"


def numeric_version(v):
    """Для VersionInfoVersion Inno Setup нужно ровно a.b.c.d из цифр: 1.2.0-rc1 → 1.2.0.0."""
    m = re.match(r"\d+(?:\.\d+){0,3}", v)
    if not m:
        fail(f"версия должна начинаться с цифр (1.2.0, 1.2.0-rc1): {v!r}")
    parts = m.group(0).split(".")
    return ".".join(parts + ["0"] * (4 - len(parts)))


def git(*args):
    p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if p.returncode:
        fail(f"git {' '.join(args)}: {p.stderr.strip()}")
    return p.stdout


# ---------- шаги сборки

def stage_python():
    os.makedirs(CACHE, exist_ok=True)
    tgz = os.path.join(CACHE, PBS_FILE)
    if not os.path.exists(tgz) or sha256(tgz) != PBS_SHA256:
        download(PBS_URL, tgz)
    got = sha256(tgz)
    if got != PBS_SHA256:
        os.remove(tgz)
        fail(f"sha256 {PBS_FILE} не совпал: {got} (ждали {PBS_SHA256})")
    log(f"распаковываю {PBS_FILE} → build\\app\\python")
    with tarfile.open(tgz) as t:  # в архиве один корень python/
        t.extractall(APP, filter="data")
    py = os.path.join(APP, "python", "python.exe")
    if not os.path.exists(py):
        fail("в архиве python-build-standalone нет python/python.exe")
    return py


def stage_pip(py):
    env = clean_env(PIP_CACHE_DIR=os.path.join(CACHE, "pip"), PIP_DISABLE_PIP_VERSION_CHECK="1")
    if subprocess.run([py, "-m", "pip", "--version"], env=env, capture_output=True).returncode:
        run([py, "-m", "ensurepip", "--default-pip"], env=env)
    # --only-binary: компилятора в CI и у пользователя нет; если для пакета не нашлось колеса — пусть сборка упадёт
    # здесь, а не программа у пользователя
    run([py, "-m", "pip", "install", "--no-warn-script-location", "--only-binary=:all:",
         "-r", os.path.join(ROOT, "requirements.txt")], env=env)
    # exe-лаунчеры pip (Scripts\*.exe) зашивают абсолютный путь к build\app\python — после установки они битые
    scripts = os.path.join(APP, "python", "Scripts")
    for f in os.listdir(scripts) if os.path.isdir(scripts) else []:
        if f.lower().endswith(".exe"):
            os.remove(os.path.join(scripts, f))
    frozen = subprocess.run([py, "-m", "pip", "freeze"], env=env, capture_output=True, text=True).stdout
    with open(os.path.join(BUILD, "pip-freeze.txt"), "w", encoding="utf-8") as f:
        f.write(frozen)
    log("установлено: " + ", ".join(l.split("==")[0] + " " + l.split("==")[-1] for l in frozen.split() if "==" in l))


def repo_files():
    """Отслеживаемые и новые неигнорируемые файлы (локально в сборку попадают и ещё не добавленные в git)."""
    out = git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
    files = []
    for f in sorted(set(out.split("\0"))):
        if not f or f in EXCLUDE_FILES or f.startswith(EXCLUDE_PREFIX) or f.startswith(("build/", "dist/")):
            continue
        if os.path.isfile(os.path.join(ROOT, f)):  # удалённые, но ещё не закоммиченные — пропускаем
            files.append(f)
    return files


def stage_copy():
    files = repo_files()
    for f in files:
        dst = os.path.join(APP, f.replace("/", os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(ROOT, f), dst)
    log(f"скопировано файлов репозитория: {len(files)}")


def stage_installed_json(version):
    commit = git("rev-parse", "HEAD").strip()
    if git("status", "--porcelain", "--untracked-files=no").strip():
        commit += "-dirty"  # локальная сборка с незакоммиченными правками — видно в отчётах и debug-архивах
    info = {"version": version, "tag": f"v{version}", "commit": commit,
            "built": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()}
    with open(os.path.join(APP, "installed.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    log(f"installed.json: {info}")


def redist_info(path):
    """Подпись и версия файла через PowerShell: (Status, Subject, FileVersion)."""
    ps = ("$s = Get-AuthenticodeSignature -LiteralPath $env:VCR; $v = (Get-Item -LiteralPath $env:VCR).VersionInfo;"
          " Write-Output ('{0}|{1}|{2}' -f $s.Status, $s.SignerCertificate.Subject, $v.FileVersion)")
    p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True,
                       env=dict(os.environ, VCR=path))
    parts = (p.stdout.strip().splitlines() or [""])[-1].split("|")
    return parts if len(parts) == 3 else ["", "", ""]


def redist_ok(path):
    status, subject, ver = redist_info(path)
    nums = tuple(int(x) for x in re.findall(r"\d+", ver)[:2]) or (0, 0)
    ok = status == "Valid" and "Microsoft Corporation" in subject and nums >= VC_MIN
    return ok, f"подпись {status or '?'}, {subject.split(',')[0] or '?'}, версия {ver or '?'}"


def stage_redist():
    os.makedirs(REDIST, exist_ok=True)
    cached = os.path.join(CACHE, "vc_redist.x64.exe")
    if not os.path.exists(cached) or not redist_ok(cached)[0]:
        download(VC_URL, cached)
    ok, why = redist_ok(cached)
    if not ok:
        fail(f"vc_redist.x64.exe не прошёл проверку: {why}")
    shutil.copy2(cached, os.path.join(REDIST, "vc_redist.x64.exe"))
    log(f"vc_redist.x64.exe: {why}")


def tidy_app(app):
    """Убрать следы проверки: данные пропов, настройки, кэш байткода нашего кода (у пакетов в python\\ — оставить:
    иначе первый запуск у пользователя компилирует их заново)."""
    for name in ("work", "config.json", ".openai_key", "input"):
        p = os.path.join(app, name)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
        elif os.path.exists(p):
            os.remove(p)
    for d, dirs, _ in os.walk(app):
        if os.path.abspath(d) == os.path.abspath(os.path.join(app, "python")):
            dirs[:] = []
            continue
        for x in list(dirs):
            if x == "__pycache__":
                shutil.rmtree(os.path.join(d, x), ignore_errors=True)
                dirs.remove(x)


# ---------- проверка собранного/установленного

def free_port(start=8810):
    for port in range(start, start + 200):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    fail("не нашёл свободный порт от 8810")


def http_get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.read()


def smoke(app):
    py = os.path.join(app, "python", "python.exe")
    if not os.path.exists(py):
        fail(f"нет {py}")
    results = []
    data = tempfile.mkdtemp(prefix="propgen-smoke-")
    env = clean_env(PROPGEN_DATA=data)

    # 1. зависимости — каждая своим процессом
    for name, code in SMOKE_IMPORTS.items():
        t = time.time()
        p = subprocess.run([py, "-c", code], cwd=data, env=env, capture_output=True, text=True, timeout=300)
        ok = p.returncode == 0
        rc = p.returncode & 0xFFFFFFFF
        results.append((f"import {name}", ok, f"{time.time() - t:.1f} с" if ok else f"код 0x{rc:08X}: {p.stderr.strip()[-600:]}"))
        log(f"{'OK ' if ok else 'ПЛОХО'} import {name}")

    # 2. doctor --json: ComfyUI/Blender может не быть (CI) — doctor должен так и сказать, а не упасть
    t = time.time()
    p = subprocess.run([py, os.path.join(app, "tools", "doctor.py"), "--json"], cwd=app, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    try:
        r = json.loads(p.stdout)
        pyok = r.get("python", {}).get("ok")
        comfy = r.get("comfy", {})
        detail = (f"{time.time() - t:.0f} с; python-зависимости: {'ok' if pyok else r.get('python')}; "
                  f"ComfyUI: {'отвечает' if comfy.get('ok') else 'не отвечает'}")
        results.append(("tools/doctor.py --json", p.returncode == 0 and bool(pyok), detail))
    except ValueError:
        results.append(("tools/doctor.py --json", False, f"код {p.returncode}, не JSON: {(p.stderr or p.stdout)[-600:]}"))
    log(f"doctor: {results[-1][2]}")

    # 3. сервер на свободном порту, данные — во временную папку
    port = free_port()
    logf = open(os.path.join(data, "server-smoke.log"), "w", encoding="utf-8")
    srv = subprocess.Popen([py, os.path.join(app, "server.py")], cwd=app, env=dict(env, PORT=str(port)),
                           stdout=logf, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        t, up = time.time(), False
        while time.time() - t < 120 and srv.poll() is None:
            try:
                st, body = http_get(base + "/api/version", timeout=2)
                up = st == 200
                break
            except Exception:  # noqa: BLE001 — ещё поднимается
                time.sleep(0.5)
        results.append(("server.py стартует", up, f"порт {port}, {time.time() - t:.1f} с" if up
                        else f"код выхода {srv.poll()}"))
        if up:
            for path, timeout in (("/api/version", 10), ("/api/props", 30), ("/api/doctor", 600), ("/", 10)):
                t = time.time()
                try:
                    st, body = http_get(base + path, timeout=timeout)
                    if path.startswith("/api/"):
                        json.loads(body)
                    note = body[:160].decode("utf-8", "replace") if path in ("/api/version",) else f"{len(body)} байт"
                    results.append((f"GET {path}", st == 200, f"{st}, {time.time() - t:.1f} с, {note}"))
                except Exception as e:  # noqa: BLE001
                    results.append((f"GET {path}", False, str(e)[:300]))
    finally:
        srv.terminate()
        try:
            srv.wait(15)
        except subprocess.TimeoutExpired:
            srv.kill()
        logf.close()
    if not all(ok for _, ok, _ in results):
        with open(os.path.join(data, "server-smoke.log"), encoding="utf-8", errors="replace") as f:
            print("--- server-smoke.log ---\n" + f.read()[-4000:])
    shutil.rmtree(data, ignore_errors=True)

    print("\n=== smoke: " + app)
    for name, ok, detail in results:
        print(f"  {'OK   ' if ok else 'FAIL '} {name:28} {detail}")
    return all(ok for _, ok, _ in results)


# ---------- проверка инсталлятора: установка → smoke → обновление поверх запущенного → удаление

def _wait_http(url, proc, limit=120):
    t = time.time()
    while time.time() - t < limit and proc.poll() is None:
        try:
            if http_get(url, timeout=2)[0] == 200:
                return True
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return False


def install_test(exe):
    """Тихая установка в чистую временную папку тем же набором ключей, что у обновления из программы, плюс /NOSTART.
    Рабочий стол не трогаем (!desktopicon); ярлык в «Пуске» и запись в «Приложениях» снимает деинсталлятор в конце."""
    exe = os.path.abspath(exe)
    base = tempfile.mkdtemp(prefix="propgen-inst-")
    # пробелы в путях — нарочно: у пользователя «C:\Users\Имя Фамилия\…», а сервер передаёт /LOG= с путём к данным
    d = os.path.join(base, "Program Dir", "propgen")
    data = os.path.join(base, "data")
    os.makedirs(data)
    results = []

    def check(name, ok, detail=""):
        results.append((name, bool(ok), detail))
        log(f"{'OK ' if ok else 'ПЛОХО'} {name} {detail}")

    def setup(tag):
        lp = os.path.join(base, "log dir", f"setup-{tag}.log")
        os.makedirs(os.path.dirname(lp), exist_ok=True)
        t = time.time()
        rc = subprocess.run([exe, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS",
                             "/CURRENTUSER", "/NOSTART", "/MERGETASKS=!desktopicon", f"/DIR={d}", f"/LOG={lp}"]).returncode
        check(f"setup ({tag})", rc == 0 and os.path.exists(lp), f"код {rc}, {time.time() - t:.0f} с"
              + ("" if os.path.exists(lp) else ", /LOG= с пробелом в пути не сработал"))
        if rc and os.path.exists(lp):
            with open(lp, encoding="utf-8", errors="replace") as f:
                print("--- " + lp + "\n" + f.read()[-5000:])
        return rc == 0

    def ours():
        """Процессы python из установленной папки: после /NOSTART их быть не должно."""
        ps = ("Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and "
              "$_.ExecutablePath.StartsWith($env:D, [StringComparison]::OrdinalIgnoreCase) } | "
              "ForEach-Object { $_.ProcessId }")
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                             text=True, env=dict(os.environ, D=d + os.sep)).stdout
        return out.split()

    un = os.path.join(d, "unins000.exe")

    def uninstall():
        """unins000.exe перезапускает себя из %TEMP% и сразу выходит — ждём, пока исчезнет он сам."""
        subprocess.run([un, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
        t = time.time()
        while time.time() - t < 180 and os.path.exists(un):
            time.sleep(1)
        time.sleep(2)

    try:
        # 1. первая установка
        if setup("install"):
            need = ["python\\python.exe", "installed.json", "start.cmd", "server.py", "propgen.ico", "unins000.exe"]
            missing = [f for f in need if not os.path.exists(os.path.join(d, f))]
            check("файлы на месте", not missing, ", ".join(missing))
            check("нет лишнего", not any(os.path.exists(os.path.join(d, x)) for x in ("work", "config.json", ".openai_key", ".git")))
            check("/NOSTART: сервер не запущен", not ours(), " ".join(ours()))
            check("smoke установленного", smoke(d))

            # 2. обновление поверх запущенного сервера: инсталлятор должен его погасить, заменить код,
            # убрать устаревшие модули и не тронуть скачанные панорамы
            with open(os.path.join(d, "tools", "zz_stale.py"), "w", encoding="utf-8") as f:
                f.write("raise SystemExit('устаревший модуль не удалён')\n")
            hdr = os.path.join(d, "web", "hdri", "zz_test_4k.hdr")
            with open(hdr, "wb") as f:
                f.write(b"#?RADIANCE\n")
            port = free_port(8811)
            srv = subprocess.Popen([os.path.join(d, "python", "python.exe"), os.path.join(d, "server.py")], cwd=d,
                                   env=clean_env(PROPGEN_DATA=data, PORT=str(port)),
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            check("сервер из установленной папки", _wait_http(f"http://127.0.0.1:{port}/api/version", srv), f"порт {port}")
            try:
                if setup("update"):
                    try:
                        srv.wait(20)
                        stopped = True
                    except subprocess.TimeoutExpired:
                        stopped = False
                    check("обновление погасило сервер", stopped)
                    check("устаревший tools\\zz_stale.py удалён", not os.path.exists(os.path.join(d, "tools", "zz_stale.py")))
                    check("панорама *_4k.hdr сохранилась", os.path.exists(hdr))
                    check("python\\ на месте после обновления", os.path.exists(os.path.join(d, "python", "python.exe")))
            finally:
                if srv.poll() is None:
                    srv.kill()

        # 3. удаление
        if os.path.exists(un):
            uninstall()
            left = []
            for dd, _, files in os.walk(d):
                left += [os.path.relpath(os.path.join(dd, f), d) for f in files]
            check("удаление", not os.path.exists(un) and not left, f"осталось: {left[:10]}" if left else "")
    finally:
        # проверка оборвалась посередине — всё равно снять запись в «Приложениях» и ярлык в «Пуске»
        if os.path.exists(un):
            uninstall()
        shutil.rmtree(base, ignore_errors=True)

    print("\n=== проверка инсталлятора: " + exe)
    for name, ok, detail in results:
        print(f"  {'OK   ' if ok else 'FAIL '} {name:36} {detail}")
    return bool(results) and all(ok for _, ok, _ in results)


# ---------- Inno Setup

def find_iscc():
    cands = [shutil.which("iscc"), shutil.which("ISCC.exe")]
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        if base:
            cands.append(os.path.join(base, "Inno Setup 6", "ISCC.exe"))
    return next((c for c in cands if c and os.path.exists(c)), None)


def stage_installer(version):
    iscc = find_iscc()
    if not iscc:
        fail("не найден Inno Setup 6 (ISCC.exe): choco install innosetup / winget install JRSoftware.InnoSetup, "
             "или --skip-installer")
    os.makedirs(DIST, exist_ok=True)
    run([iscc, "/Qp", f"/DAppVersion={version}", f"/DAppVersionNum={numeric_version(version)}",
         f"/DAppSrc={APP}",f"/DRedistDir={REDIST}", f"/DOutputDir={DIST}", ISS])
    exe = os.path.join(DIST, f"propgen-setup-{version}.exe")
    if not os.path.exists(exe):
        fail(f"Inno Setup не создал {exe}")
    return exe


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description="Сборка Windows-инсталлятора propgen")
    ap.add_argument("--version", default="", help="1.2.0 или v1.2.0; по умолчанию — тег CI/git, иначе 0.0.0-dev")
    ap.add_argument("--skip-installer", action="store_true", help="не запускать Inno Setup")
    ap.add_argument("--skip-smoke", action="store_true", help="не проверять собранное")
    ap.add_argument("--smoke", metavar="DIR", help="только проверить готовую папку (например, после установки)")
    ap.add_argument("--install-test", metavar="EXE", help="только проверить готовый setup.exe: тихая установка во "
                    "временную папку, smoke, обновление поверх запущенного сервера, удаление")
    a = ap.parse_args()
    if sys.platform != "win32":
        fail("сборка — только под Windows (python-build-standalone для windows-msvc, Inno Setup)")

    if a.smoke:
        sys.exit(0 if smoke(os.path.abspath(a.smoke)) else 1)
    if a.install_test:
        sys.exit(0 if install_test(a.install_test) else 1)

    version = resolve_version(a.version)
    numeric_version(version)
    t0, times = time.time(), {}
    log(f"propgen {version}: сборка в {APP}")
    if os.path.isdir(APP):
        shutil.rmtree(APP)
    os.makedirs(APP)

    def timed(name, fn, *args):
        t = time.time()
        res = fn(*args)
        times[name] = time.time() - t
        return res

    py = timed("python", stage_python)
    timed("pip", stage_pip, py)
    timed("копия репозитория", stage_copy)
    stage_installed_json(version)
    timed("vc_redist", stage_redist)
    if not a.skip_smoke:
        ok = timed("smoke", smoke, APP)
        tidy_app(APP)
        if not ok:
            fail("проверка собранного не прошла (см. таблицу выше)")
    tidy_app(APP)
    exe = None if a.skip_installer else timed("Inno Setup", stage_installer, version)

    print("\n=== итог")
    print(f"  версия      {version} ({numeric_version(version)})")
    print(f"  build\\app   {size_mb(APP):.0f} МБ (python\\ {size_mb(os.path.join(APP, 'python')):.0f} МБ)")
    if exe:
        print(f"  инсталлятор {exe} — {size_mb(exe):.0f} МБ")
    print("  время       " + ", ".join(f"{k} {v:.0f} с" for k, v in times.items()) + f"; всего {time.time() - t0:.0f} с")


if __name__ == "__main__":
    main()
