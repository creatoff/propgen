@echo off
rem propgen server + browser. Exit code 3 = server updated itself from git and asks for a restart.
rem Keep this file ASCII-only: cmd misreads UTF-8 batch files with chcp 65001 (see CLAUDE.md).
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Run setup.cmd first.
  pause
  exit /b 1
)
set PYTHONIOENCODING=utf-8
rem if Python dies silently (DLL clash, antivirus) faulthandler at least prints where
set PYTHONFAULTHANDLER=1
rem no quotes inside for /f: with several pairs cmd strips the outer ones and the command breaks
for /f %%P in ('.venv\Scripts\python.exe tools\config.py port') do set PORT_CFG=%%P
if not defined PORT_CFG (
  echo Could not read config - does Python start? Using port 8795.
  set PORT_CFG=8795
)
start "" http://127.0.0.1:%PORT_CFG%
set PROPGEN_SUPERVISED=1
:run
echo propgen server: http://127.0.0.1:%PORT_CFG%  (Ctrl+C to stop)
.venv\Scripts\python.exe server.py
set RC=%ERRORLEVEL%
if "%RC%"=="3" (
  echo === restarting after update ===
  goto run
)
rem never close silently: the exit code is what tells us what broke
if not "%RC%"=="0" (
  echo.
  echo Server exited with code %RC%. Run debug.cmd and send the zip or a screenshot of this window.
  pause
)
