@echo off
REM ===========================================================================
REM  run-qgis-python.bat -- run Python inside the full QGIS runtime environment.
REM
REM  NOTE: keep this file ASCII-only. cmd.exe reads .bat files using the system
REM  ANSI code page (GBK on zh-CN Windows); UTF-8 Chinese comments get mangled
REM  and break the batch parser.
REM
REM  Design notes:
REM   * QGIS ships o4w_env.bat, which sets PYTHONHOME=apps\Python312. That is
REM     fine for the bundled interpreter, so we keep it.
REM   * Web-layer dependencies (fastapi / uvicorn / mcp) live in the project's
REM     own deps\ folder and are injected via PYTHONPATH. This keeps the QGIS
REM     installation untouched and makes the project self-contained.
REM   * A real venv is NOT used on purpose: OSGeo4W keeps python312.dll in
REM     bin\, so the Windows loader binds the DLL from there and CPython then
REM     resolves sys.prefix away from the venv -- the venv silently degrades
REM     to the base interpreter.
REM
REM  Usage: run-qgis-python.bat <any python args>   e.g. -m gisds serve
REM ===========================================================================
setlocal

REM Always work from the project root: "python -m gisds" needs the package
REM to be importable, and the project root is not on PYTHONPATH.
cd /d "%~dp0"

REM Which QGIS runtime to use, in this order:
REM   1. an explicit QGIS_ROOT (the user always wins)
REM   2. the runtime bundled next to this file (the "standalone" release:
REM      <package>\qgis\ -- that package then needs no QGIS install at all)
REM   3. the system install
if "%QGIS_ROOT%"=="" (
    if exist "%~dp0qgis\bin\o4w_env.bat" (
        set "QGIS_ROOT=%~dp0qgis"
    ) else (
        set "QGIS_ROOT=C:\Program Files\QGIS 3.44.14"
    )
)

if not exist "%QGIS_ROOT%\bin\o4w_env.bat" (
    echo [Browser QGIS] QGIS env script not found: "%QGIS_ROOT%\bin\o4w_env.bat"
    echo [Browser QGIS] Set the QGIS_ROOT environment variable to your QGIS install dir.
    exit /b 1
)

REM Set up the OSGeo4W environment.
REM
REM We deliberately do NOT call "%QGIS_ROOT%\bin\o4w_env.bat" here: that script
REM wraps everything in pushd/popd, and a transient Windows hiccup makes
REM pushd fail with "The network path was not found." -- which leaves the
REM batch directory stack unbalanced and can re-run the command line (we saw
REM one script print its whole output twice this way).
REM
REM So we do the same three things it does, without pushd:
REM   1. OSGEO4W_ROOT = the short path of the QGIS root (short path because
REM      the install dir may contain spaces)
REM   2. a clean PATH
REM   3. run every etc\ini\*.bat (those are what set PYTHONHOME, GDAL_DATA,
REM      PROJ_DATA, QT_PLUGIN_PATH ...)
for %%i in ("%QGIS_ROOT%") do set "OSGEO4W_ROOT=%%~fsi"
set "PATH=%OSGEO4W_ROOT%\bin;%WINDIR%\system32;%WINDIR%;%WINDIR%\system32\WBEm"
if exist "%OSGEO4W_ROOT%\etc\ini\*.bat" (
    for %%f in ("%OSGEO4W_ROOT%\etc\ini\*.bat") do call "%%f"
) else (
    call "%QGIS_ROOT%\bin\o4w_env.bat"
)

set "PATH=%OSGEO4W_ROOT%\apps\qgis-ltr\bin;%PATH%"
set "QGIS_PREFIX_PATH=%OSGEO4W_ROOT:\=/%/apps/qgis-ltr"
set "GDAL_FILENAME_IS_UTF8=YES"
set "VSI_CACHE=TRUE"
set "VSI_CACHE_SIZE=1000000"
set "QT_PLUGIN_PATH=%OSGEO4W_ROOT%\apps\qgis-ltr\qtplugins;%OSGEO4W_ROOT%\apps\qt5\plugins"
set "PYTHONUTF8=1"

REM PyQGIS modules + this project's web-layer dependencies
set "PYTHONPATH=%OSGEO4W_ROOT%\apps\qgis-ltr\python;%~dp0deps"

set "PY=%OSGEO4W_ROOT%\bin\python3.exe"

"%PY%" %*
exit /b %ERRORLEVEL%
