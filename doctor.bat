@echo off
REM ===========================================================================
REM  doctor.bat -- check the Browser QGIS runtime environment.
REM ===========================================================================
setlocal
cd /d "%~dp0"
call "%~dp0run-qgis-python.bat" -m gisds doctor
pause
