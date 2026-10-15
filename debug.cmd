@echo off
rem Debug bundle: run it when propgen does not start or something breaks, then send the zip.
rem Works without .venv and without the server (tools\debug.py is stdlib only). No keys inside.
rem Keep this file ASCII-only: cmd misreads UTF-8 batch files with chcp 65001 (see CLAUDE.md).
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONFAULTHANDLER=1

set PY=
if exist .venv\Scripts\python.exe set PY=.venv\Scripts\python.exe
if not defined PY (
  py -3 -c "import sys" >nul 2>&1 && set PY=py -3
)
if not defined PY (
  python -c "import sys" >nul 2>&1 && set PY=python
)
if not defined PY (
  echo Python not found. Send a screenshot of this window and the output of setup.cmd.
  pause
  exit /b 1
)

if not exist work mkdir work
rem stdout to a file instead of for /f: this way we keep the exit code if Python crashes
%PY% tools\debug.py > work\debug_out.txt
set RC=%ERRORLEVEL%
type work\debug_out.txt
set OUT=
for /f "usebackq delims=" %%L in ("work\debug_out.txt") do set "OUT=%%L"
if not exist "%OUT%" (
  echo.
  echo Debug bundle failed: %PY% exited with code %RC%. Send a screenshot of this window.
  %PY% -c "import sys; print(sys.version, sys.executable)"
  pause
  exit /b 1
)
rem the last stdout line is the zip path: open its folder with the file selected
explorer /select,"%OUT%"
echo.
echo Send this file: %OUT%
pause
