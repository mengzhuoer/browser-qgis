@echo off
REM ===========================================================================
REM  launcher-files.bat -- open the Browser QGIS file browser as its own window.
REM
REM  Same window as the "File Browser" tab of launcher.bat, but standalone:
REM  browse / upload / download / delete datasets in every data space.
REM
REM  The GUI only needs a normal Python with tkinter (the service still runs on
REM  QGIS's own Python, see run-qgis-python.bat).
REM
REM  Keep this file ASCII-only: cmd.exe reads .bat files with the system ANSI
REM  code page, so UTF-8 Chinese characters would corrupt the batch parser.
REM ===========================================================================
setlocal
cd /d "%~dp0"

REM This GUI runs on a NORMAL python (it needs tkinter). If this script is
REM started from inside the QGIS runtime, that normal python inherits
REM QGIS's PYTHONHOME/PYTHONPATH and loads QGIS's Python 3.12 standard
REM library -> "AssertionError: SRE module mismatch" while importing.
REM The symptom looks like "tkinter is missing" but has nothing to do with
REM tkinter, so the probe below would report a wrong reason.
REM Clear both for this script and its children; the service is started
REM through run-qgis-python.bat, which sets them again for itself.
set "PYTHONHOME="
set "PYTHONPATH="

REM Find a NORMAL python that has tkinter.
REM
REM Do NOT rely on "py -3" alone: the py launcher may pick **QGIS's own**
REM python 3.12, which does not bundle tkinter -- and in the QGIS runtime
REM the PATH is replaced, so bare "python"/"python3" are not there either.
REM That combination is exactly what produced the misleading
REM "No Python with tkinter was found" message.
REM So: try the known install paths first, skip any python that lives under
REM a QGIS/OSGeo4W directory, and only then fall back to the PATH commands.
set "PY="
set "PYARGS="
for /D %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if not defined PY if exist "%%~fD\python.exe" call :probe "%%~fD\python.exe"
if not defined PY for /D %%D in ("%ProgramFiles%\Python3*") do if not defined PY if exist "%%~fD\python.exe" call :probe "%%~fD\python.exe"
if not defined PY if exist "%SystemDrive%\Python311\python.exe" call :probe "%SystemDrive%\Python311\python.exe"
if not defined PY call :probe "py" "-3"
if not defined PY call :probe "python"
if not defined PY call :probe "python3"

if not defined PY (
    echo.
    echo [Browser QGIS] No Python with tkinter was found.
    echo         Install Python 3 from python.org -- pick the build that has tkinter.
    echo         and run this file again.
    echo.
    pause
    exit /b 1
)

echo [Browser QGIS] Using: %PY% %PYARGS%
"%PY%" %PYARGS% -m gisds.launcher files
if errorlevel 1 (
    echo.
    echo [Browser QGIS] The file browser exited with an error.
    pause
)
exit /b %ERRORLEVEL%

REM %1 = executable, %2 = extra args (may be empty).
REM The "qgis"/"osgeo" check rejects QGIS's own python: it has no tkinter.
:probe
"%~1" %2 -c "import sys,tkinter; sys.exit(0 if ('qgis' not in sys.executable.lower() and 'osgeo' not in sys.executable.lower()) else 1)" >nul 2>&1
if errorlevel 1 goto :eof
set "PY=%~1"
set "PYARGS=%2"
goto :eof
