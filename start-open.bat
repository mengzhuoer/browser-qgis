@echo off
REM ===========================================================================
REM  start-open.bat -- start the service in "open" mode (no password).
REM
REM  In this mode every visitor picks a username and walks straight in:
REM  no password, no registration. Each username still gets its own data
REM  space, and the same username means the same space on any device.
REM
REM  The admin account keeps using its password, so management tasks are
REM  still protected.
REM
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

REM Print the LAN address so people know where to point their browser.
echo.
echo   Starting Browser QGIS in OPEN mode (no password) ...
for /f "tokens=2 delims=:" %%A in ('ipconfig ^| findstr /c:"IPv4"') do (
    for /f "tokens=* delims= " %%B in ("%%A") do echo     http://%%B:8848/
)
echo   Anyone on this network can open it and pick a username.
echo   Close this window to stop the service.
echo.
call "%~dp0run-qgis-python.bat" -m gisds --config "%~dp0config.open.json" serve
echo.
echo   Service stopped.
pause
