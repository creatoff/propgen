@echo off
rem propgen first-time setup: Python venv + requirements, then system check.
rem Keep this file ASCII-only: cmd misreads UTF-8 batch files with chcp 65001 (see CLAUDE.md).
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONFAULTHANDLER=1

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
  echo Python 3.10-3.12 is required: https://www.python.org/downloads/ ^(check "Add to PATH"^)
  pause
  exit /b 1
)

if not exist .venv (
  echo Creating .venv with %PY% ...
  %PY% -m venv .venv || (echo Failed to create .venv & pause & exit /b 1)
)
.venv\Scripts\python.exe -m pip install --upgrade pip >nul
.venv\Scripts\python.exe -m pip install -r requirements.txt || (echo pip install failed & pause & exit /b 1)

echo.
echo === System check ===
.venv\Scripts\python.exe tools\doctor.py
if errorlevel 1 echo System check exited with code %ERRORLEVEL% - run debug.cmd and send the zip.
echo.
echo Next: start.cmd - and the gear "Settings" in the app if anything above has a cross.
pause
