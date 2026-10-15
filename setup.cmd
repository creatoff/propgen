@echo off
chcp 65001 >nul
rem Первичная установка propgen: Python-окружение и зависимости, затем проверка системы.
rem Модели, ноды ComfyUI и ключи — через экран «Настройки» (шестерёнка) или tools\doctor.py.
cd /d "%~dp0"

set PY=
for %%V in (3.12 3.11 3.10) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>&1 && set PY=py -%%V
  )
)
if not defined PY (
  python -c "import sys; assert (3,10) <= sys.version_info[:2] <= (3,12)" >nul 2>&1 && set PY=python
)
if not defined PY (
  echo Нужен Python 3.10-3.12: https://www.python.org/downloads/ ^(галка «Add to PATH»^)
  pause
  exit /b 1
)

if not exist .venv (
  echo Создаю .venv на %PY% ...
  %PY% -m venv .venv || (echo Не получилось создать .venv & pause & exit /b 1)
)
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt || (echo Ошибка установки зависимостей & pause & exit /b 1)

echo.
echo === Проверка системы ===
".venv\Scripts\python.exe" tools\doctor.py
echo.
echo Дальше: start.cmd — и шестерёнка «Настройки» в окне, если что-то выше с крестиком.
pause
