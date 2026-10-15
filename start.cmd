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
".venv\Scripts\python.exe" server.py
