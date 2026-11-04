@echo off
REM ===========================================================================
REM  launcher-open.bat -- open the Browser QGIS graphical launcher with the
REM  service running in "open" mode (no password).
REM
REM  The launcher always starts the service with a fixed "-m gisds serve",
REM  so the config is switched through the GISDS_CONFIG environment variable
REM  instead of a command-line flag.
REM
REM  Keep this file ASCII-only: cmd.exe reads .bat files with the system ANSI
REM  code page, so UTF-8 Chinese characters would corrupt the batch parser.
REM ===========================================================================
setlocal
cd /d "%~dp0"

set "GISDS_CONFIG=%~dp0config.open.json"
call "%~dp0launcher.bat"
endlocal
