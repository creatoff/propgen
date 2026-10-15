@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Сначала запусти setup.cmd
  pause
  exit /b 1
)
set PYTHONIOENCODING=utf-8
for /f %%P in ('".venv\Scripts\python.exe" -c "import sys; sys.path.insert(0,'tools'); import config; print(config.get('port'))"') do set PORT_CFG=%%P
start "" http://127.0.0.1:%PORT_CFG%
rem код выхода 3 — сервер обновился из репозитория и просит перезапуск
set PROPGEN_SUPERVISED=1
:run
".venv\Scripts\python.exe" server.py
if %ERRORLEVEL%==3 (
  echo === перезапуск после обновления ===
  goto run
)
