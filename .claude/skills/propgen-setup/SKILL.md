---
name: propgen-setup
description: Установка и настройка propgen на новой машине — Python-окружение, ComfyUI (где бы он ни стоял) и его ноды, докачка моделей (~33 ГБ), Blender, текстовая модель (ключ OpenAI или локальный LM Studio), проверка прогоном. Используй, когда propgen запускают впервые, когда «ничего не работает», при ошибках про ComfyUI/модели/Blender, по просьбам «установи», «настрой», «почини запуск».
---

# Установка propgen

propgen — локальный генератор игровых пропов: фото → чистый фронт (Qwen-Image-Edit) → 3D (TRELLIS.2) → лоу-поли.
Тяжёлое делает ComfyUI, лоу-поли — Blender, интерфейс — `server.py` (FastAPI) на http://127.0.0.1:8795.

Главный инструмент — `tools/doctor.py`: он спрашивает у запущенного ComfyUI, какие ноды есть, какие модели он видит
и в какие папки их класть, проверяет Blender, LM Studio, ключ и Python-зависимости, и докачивает недостающее.
Всё это же есть в интерфейсе: шестерёнка «Настройки» → «Проверка системы». Настройки — `config.json`
(создаётся сам, в git не попадает; пустые поля = автопоиск).

Порядок ниже. Не перепрыгивай: без запущенного ComfyUI doctor не знает ни нод, ни папок моделей.

## 0. Что за машина

- Видеокарта NVIDIA, от 8 ГБ видеопамяти (`nvidia-smi`). На 8 ГБ вид ~70 с, на 12–16 ГБ заметно быстрее.
  AMD/Intel/Mac — ComfyUI-часть, скорее всего, не взлетит: честно скажи об этом пользователю.
- Оперативка от 32 ГБ (16 ГБ — тяжело: Qwen-Edit частично живёт в RAM). Диск: ~35 ГБ под модели + место под ComfyUI.
- Windows — основной сценарий (`setup.cmd`, `start.cmd`). На Linux/macOS — те же шаги руками: venv, `pip install -r requirements.txt`, `python server.py`.

## 1. Python-окружение

`setup.cmd` (двойной клик или из терминала): ищет Python 3.10–3.12, создаёт `.venv`, ставит `requirements.txt`, печатает отчёт doctor.
Python 3.13+ не годится — у pymeshlab нет колёс. Нет подходящего Python — поставь (`winget install Python.Python.3.12`), спросив пользователя.

## 2. ComfyUI

1. Найди установку и узнай, запущен ли он. Варианты:
   - ComfyUI Desktop: `%LOCALAPPDATA%\Programs\ComfyUI\` (приложение), данные в `%LOCALAPPDATA%\Comfy-Desktop\` или `Documents\ComfyUI`.
     **Слушает порт 8000** по умолчанию (меняется в его настройках).
   - Portable: папка `ComfyUI_windows_portable` (запуск `run_nvidia_gpu.bat`), порт 8188.
   - git-установка: `python main.py` в папке ComfyUI, порт 8188.
   Не нашёл — спроси пользователя; нет вовсе — предложи ComfyUI Desktop с comfy.org (установку делает пользователь).
2. Запусти его (или попроси пользователя) и пропиши адрес: `config.json` → `"comfy_url"` или экран настроек.
   doctor сам пробует 8188 и 8000 и подскажет, где нашёл.
3. Нужен **свежий ComfyUI**: TRELLIS.2, Pixal3D, RemoveBackground, 3D-ноды (RemeshMesh/UnwrapMesh/BakeTextureFromVoxel) —
   в ядре с 2026 года; проверено на 0.41. doctor перечислит недостающие ноды — тогда обнови ComfyUI
   (Desktop обновляется сам при запуске; portable — `update\update_comfyui.bat`; git — `git pull` + `pip install -r requirements.txt`).
4. Единственная сторонняя нода — **ComfyUI-GGUF** (`UnetLoaderGGUF`): через ComfyUI Manager («Install Custom Nodes» → ComfyUI-GGUF)
   или `git clone https://github.com/city96/ComfyUI-GGUF` в `custom_nodes` и `pip install gguf` **в Python самого ComfyUI**
   (Desktop: `…\ComfyUI\.venv`, portable: `python_embeded\python.exe -m pip`). После — перезапуск ComfyUI.

## 3. Модели

`.venv\Scripts\python tools\doctor.py` → блоки «модели core / multiview / pixal3d».
- `core` (~32 ГБ) обязателен: Qwen-Image-Edit-2511 Q4 GGUF, Qwen2.5-VL 7B fp8, VAE, Lightning LoRA, TRELLIS.2 int8, DINOv3, BiRefNet.
- `multiview` (~6 ГБ) — режим «4 вида», `pixal3d` (~6 ГБ) — модель Pixal3D для одного фото. По желанию.

Перед докачкой **скажи пользователю объём и куда ляжет** (doctor кладёт в первую папку нужного типа из настроек ComfyUI,
обычно `ComfyUI\models\…`), проверь свободное место на том диске. Другое место — `--dir D:\models` (запомнится в config.json),
но тогда эту папку должен видеть ComfyUI (`extra_model_paths.yaml` или «Пути к моделям» в настройках Desktop).

```
.venv\Scripts\python tools\doctor.py download --group core
.venv\Scripts\python tools\doctor.py download --group hdri     # необязательно: панорамы 4k для фона просмотрщика
```
Качает в 6 потоков с докачкой кусков; прерванный файл лежит как `*.part` и перекачивается. После — снова `doctor.py`:
ComfyUI видит новые файлы без перезапуска. Если модель скачана, а doctor её не видит — она не в той папке.

## 4. Blender 4.2+

doctor ищет в PATH и `C:\Program Files\Blender Foundation\Blender *\`. Нет — `winget install BlenderFoundation.Blender`
(спросив) или путь к `blender.exe` в настройках (`"blender"` в config.json).

## 5. Текстовая модель (перевод заметки → промпт)

Спроси пользователя, что ему удобнее:
- **Ключ OpenAI** — секунды, ~$0.0003 за проп, дневной лимит в настройках (по умолчанию $0.20). Ключ вводится
  в «Настройках» (кнопка «Проверить» — бесплатный запрос) или `"openai_key"` в config.json. Никогда не выводи ключ в чат и логи.
- **Локально, без ключа** — LM Studio (lmstudio.ai, бесплатно): установить, запустить один раз, затем кнопка
  «Скачать локальную модель» в настройках или `lms get google/gemma-3-4b -y`. Режим «авто» или «локально».
  Qwen3/3.5 из LM Studio не бери — они «думают» и возвращают пустой ответ.
- **Выключено** — заметки идут в генератор как есть (тогда их нужно писать по-английски).

## 6. Запуск и проверка

1. `start.cmd` — откроет http://127.0.0.1:8795 (порт — `"port"`). Если чего-то не хватает, сразу откроются «Настройки» с проверкой.
2. Прогон целиком: `.venv\Scripts\python tools\smoke.py` — проп из `examples\kettle.png` проходит фронт → 3D → лоу-поли,
   печатается время шагов. Ориентир на RTX 5060 8 ГБ: фронт ~70 с (первый после запуска ComfyUI ~3,5 мин), 3D ~3 мин, лоу-поли ~20 с.
   Тестовый проп потом можно удалить в интерфейсе.

## Если что-то не так

- **Вид генерируется десятки минут** (строка состояния: минуты на шаге выборки, GPU «100%» при малом энергопотреблении):
  ComfyUI вынес часть памяти в «общую» GPU-память Windows. Перезапусти ComfyUI. Профилактика — панель NVIDIA →
  «Управление параметрами 3D» → программа = python.exe ComfyUI → «CUDA — политика резервной системной памяти» → «Предпочитать отсутствие».
- **Шаг упал** — лог шага: кнопка ≡ у шага в интерфейсе или `work\props\<id>\logs\<шаг>.log`.
- **Out of memory в ComfyUI** — закрой другие программы с видеокартой; на 8 ГБ одновременно с propgen ничего тяжёлого не запускать.
- **Лоу-поли падает** — смотри лог: обычно не найден Blender или нет pymeshlab в `.venv` (запусти setup.cmd).
