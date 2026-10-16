@echo off
rem Debug bundle: run it when propgen does not start or something breaks, then send the zip.
rem Works without python\ or .venv and without the server (tools\debug.py is stdlib only). No keys inside.
rem Keep this file ASCII-only: cmd misreads UTF-8 batch files with chcp 65001 (see CLAUDE.md).
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
rem bundled Python must not pick up packages from the user's own Python 3.12 (%APPDATA%\Python)
set PYTHONNOUSERSITE=1
set PYTHONFAULTHANDLER=1

rem installed version: bundled python\; git clone: .venv; else any system Python
set PY=
if exist python\python.exe set PY="python\python.exe"
if not defined PY if exist .venv\Scripts\python.exe set PY=".venv\Scripts\python.exe"
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

rem the zip goes to the data folder (work\ here or %LOCALAPPDATA%\propgen\work), its path is the last stdout line;
rem stdout to a file instead of for /f: this way we keep the exit code if Python crashes
set OUTF=%TEMP%\propgen_debug_out.txt
%PY% tools\debug.py > "%OUTF%"
set RC=%ERRORLEVEL%
type "%OUTF%"
set OUT=
for /f "usebackq delims=" %%L in ("%OUTF%") do set "OUT=%%L"
if not exist "%OUT%" (
  echo.
  echo Debug bundle failed: %PY% exited with code %RC%. Send a screenshot of this window.
  %PY% -c "import sys; print(sys.version, sys.executable)"
  pause
  exit /b 1
)
rem open the zip folder with the file selected
explorer /select,"%OUT%"
echo.
echo If it was not sent above, send this file: %OUT%
pause
