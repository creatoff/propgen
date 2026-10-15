@echo off
chcp 65001 >nul
rem Отладочный архив: если propgen не запускается или что-то сломалось — запусти и пришли получившийся zip.
rem Работает и без .venv и без запущенного сервера (tools\debug.py — только стандартная библиотека). Ключей в архиве нет.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

set PY=
if exist ".venv\Scripts\python.exe" set PY=".venv\Scripts\python.exe"
if not defined PY (
  py -3 -c "import sys" >nul 2>&1 && set PY=py -3
)
if not defined PY (
  python -c "import sys" >nul 2>&1 && set PY=python
)
if not defined PY (
  echo Не нашёл Python. Пришлите хотя бы снимок этого окна и вывод setup.cmd.
  pause
  exit /b 1
)

set OUT=
for /f "delims=" %%L in ('%PY% tools\debug.py') do (
  echo %%L
  set "OUT=%%L"
)
if not exist "%OUT%" (
  echo.
  echo Архив не собрался — пришлите снимок этого окна.
  pause
  exit /b 1
)
rem последняя строка вывода — путь к архиву: открываем папку с выделенным файлом
explorer /select,"%OUT%"
echo.
echo Пришлите этот файл: %OUT%
pause
