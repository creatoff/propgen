# propgen — памятка агенту

Локальный генератор игровых пропов: фото → чистый фронт (Qwen-Image-Edit в ComfyUI) → 3D (TRELLIS.2 в ComfyUI) →
лоу-поли и запекание (Blender) → архив для движков. Обзор для людей — `README.md`.

## Первым делом

| Ситуация | Что делать |
|---|---|
| Новая машина, «установи», «не запускается» | скилл **`propgen-setup`** (`.claude/skills/propgen-setup/SKILL.md`) — по шагам |
| Узнать состояние системы | `.venv\Scripts\python tools\doctor.py` (или `--json`) — ComfyUI, ноды, модели, Blender, LLM, Python, панорамы |
| Проверить, что всё едет целиком | сервер запущен → `.venv\Scripts\python tools\smoke.py` (пример `examples/kettle.png`, печатает время шагов) |
| Что сейчас происходит | `GET http://127.0.0.1:8795/api/queue` — текущий шаг, очередь, живой прогресс ComfyUI (`comfy.cls/value/max/last_event`) |
| Почему шаг упал | `work/props/<id>/logs/<views|mesh|lowpoly|export>.log`, статус — `work/props/<id>/meta.json` → `steps` |

## Запуск

- `setup.cmd` — venv (Python 3.10–3.12) + `requirements.txt` + отчёт doctor. `start.cmd` — сервер и браузер.
- Сервер: `.venv\Scripts\python server.py` → http://127.0.0.1:8795 (порт — `config.json` → `port`).
- Нужен **запущенный ComfyUI** (адрес — `comfy_url`; Desktop по умолчанию 8000, portable/git — 8188) и Blender 4.2+.

## Настройки

- Всё машинное — в `config.json` (в .gitignore) через `tools/config.py`: `config.get(key)`, `config.save({...})`,
  `config.find_blender()`, `config.find_lms()`, `config.openai_key()`. Пустое значение = автопоиск.
  **Не зашивай пути, адреса, порты и ключи в код.** Поля и умолчания — `config.DEFAULTS`.
- Экран «⚙ Настройки» в интерфейсе = `GET/POST /api/settings`, `POST /api/settings/test_openai`, `GET /api/doctor`,
  `POST /api/doctor/download {groups}` (+ `GET` — прогресс), `POST /api/doctor/local_model` (LM Studio `lms get`).
- Текстовая модель: `llm_mode` = `auto` (OpenAI при наличии ключа, иначе LM Studio) · `openai` · `local` · `off`.
  OpenAI — `tools/llm_openai.py` (кэш `work/llm_cache.json`, расход `work/llm_usage.jsonl`, дневной лимит).
  Локально — `tools/llm.py`: Gemma-3-4B через LM Studio; перед ней ComfyUI выгружает модели (`/free`), поэтому
  локальный вызов идёт **только через очередь задач**. Qwen3/3.5 в LM Studio не годятся — «думают» и отвечают пусто.
- **Ключ OpenAI** (`config.json` → `openai_key` или файл `.openai_key`) никогда не выводи в чат, логи, коммиты;
  наружу API отдаёт только маску.
- Модели: манифест `tools/doctor.py` → `MODELS` (папка ComfyUI, файл, HF-репозиторий, размер, группа `core`/`multiview`/`pixal3d`).
  Наличие проверяется через `/object_info` ComfyUI, папки — через `/internal/folder_paths`.

## Как устроено

- `server.py` (FastAPI): пропы в `work/props/<id>/`, **одна очередь задач** (видеокарта одна): `views` → `mesh` → `lowpoly`
  (+ `describe` для локальной LLM). Шаги — отдельные процессы `tools/*.py`; результат пишется в `<шаг>.new/` и
  подменяет старый только после успеха. `POST /api/props/<id>/run {step}`; без рамки (`cropped`) — 409, `force: true` в обход.
- `POST /api/props/<id>/export {height_cm, sources}` → `tools/pack.py` + Blender `tools/export.py` → zip по схеме Poly Haven.
- `web/` — статика без сборки: `app.js` (вся логика), `controls.js` (свои select/range/number поверх нативных),
  `index.html`, `style.css`. three.js с CDN. Просмотрщик 3D создаётся только на вкладке «Модель» при пустой очереди
  и выгружается целиком (WebGL-контекст) во время задач — видеопамять нужна ComfyUI.

## Правила работы

- **Перезапуск сервера обрывает идущий шаг.** Перед перезапуском проверь `/api/queue` → `current: null`.
  Сервер держит импортированные модули (`config`, `llm*`, `doctor`, `pack`, `comfywatch`) — после их правки нужен перезапуск.
  Скрипты шагов (`views.py`, `mesh.py`, `lowpoly.py`, `solidify.py`, `export.py`) подхватывают правки сами. `web/` — обнови страницу.
- **Правь файлы через Edit/Write или патч-скриптом из файла.** heredoc в Git Bash портит `\n` в строках и обратные слеши в путях.
- Проверка после правок: `python -c "import ast; ast.parse(open('server.py', encoding='utf-8').read())"` для Python,
  `tools/doctor.py`, `tools/smoke.py`; интерфейс — открыть страницу (chrome-devtools), консоль без ошибок,
  инспектор влезает в 1440×860 без общей прокрутки страницы (`document.documentElement.scrollHeight == innerHeight`).
- **Видеокарта 8 ГБ — дефицит.** Не запускай рядом с генерацией ничего на GPU (LLM на GPU, свой просмотрщик в браузере).
  Признак деградации ComfyUI: минуты на шаге выборки, GPU «100%» при ~40 Вт, у python ComfyUI растёт «Shared Usage» —
  лечится перезапуском ComfyUI (это делает пользователь или с его согласия). Норма на 5060: фронт ~70 с, 3D ~3 мин, лоу-поли ~20 с.
- Интерфейс и тексты — по-русски. Комментарии в коде — по-русски, объясняют «почему».
