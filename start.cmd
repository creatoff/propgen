@echo off
rem propgen server + browser. Exit code 3 = server updated itself from git and asks for a restart.
rem Exit code 0 after an update of the installed version: the installer replaces the files and runs start.cmd itself.
rem Keep this file ASCII-only: cmd misreads UTF-8 batch files with chcp 65001 (see CLAUDE.md).
chcp 65001 >nul
cd /d "%~dp0"
rem installed version: bundled python\ (installer); git clone: .venv made by setup.cmd
set PY=
if exist python\python.exe set PY=python\python.exe
if not defined PY if exist .venv\Scripts\python.exe set PY=.venv\Scripts\python.exe
if not defined PY (
  echo Python for propgen not found. Git clone: run setup.cmd first. Installed version: reinstall propgen.
  pause
  exit /b 1
)
set PYTHONIOENCODING=utf-8
rem bundled Python must not pick up packages from the user's own Python 3.12 (%APPDATA%\Python)
set PYTHONNOUSERSITE=1
rem if Python dies silently (DLL clash, antivirus) faulthandler at least prints where
set PYTHONFAULTHANDLER=1
rem the server opens the browser itself once it answers (opening it here showed a connection error first)
set PROPGEN_OPEN=1
set PROPGEN_SUPERVISED=1
:run
echo Starting propgen server... (Ctrl+C to stop)
"%PY%" server.py
set RC=%ERRORLEVEL%
if "%RC%"=="3" (
  echo === restarting after update ===
  rem the open page reloads by itself after an update - no second tab
  set PROPGEN_OPEN=
  goto run
)
rem never close silently: the exit code is what tells us what broke
if not "%RC%"=="0" (
  echo.
  echo Server exited with code %RC%. Run debug.cmd and send the zip or a screenshot of this window.
  pause
)
