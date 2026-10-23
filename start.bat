@echo off
REM ===========================================================================
REM  start.bat -- launch the Browser QGIS REST service (double-click friendly).
REM  Keep this file ASCII-only: cmd.exe reads .bat files with the system ANSI
REM  code page, so UTF-8 Chinese characters would corrupt the batch parser.
REM ===========================================================================
setlocal
cd /d "%~dp0"

if not exist "deps" (
    echo [Browser QGIS] Dependencies missing - running setup first ...
    powershell -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
    if errorlevel 1 (
        echo [Browser QGIS] Setup failed.
        pause
        exit /b 1
    )
)

if not exist "data" mkdir "data"

echo.
echo   Starting Browser QGIS ...
echo   Close this window to stop the service.
echo.
call "%~dp0run-qgis-python.bat" -m gisds serve
echo.
echo   Service stopped.
pause
