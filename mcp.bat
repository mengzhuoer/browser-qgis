@echo off
REM ===========================================================================
REM  mcp.bat -- launch the Browser QGIS MCP server over stdio.
REM
REM  Register this with OpenClaw (or any MCP client) as:
REM    {
REM      "mcpServers": {
REM        "gisds": {
REM          "command": "C:\\Windows\\System32\\cmd.exe",
REM          "args": ["/c", "D:\\MiniMax\\gis-data-server\\mcp.bat"]
REM        }
REM      }
REM    }
REM
REM  Keep this file ASCII-only (cmd.exe + system ANSI code page).
REM ===========================================================================
setlocal
cd /d "%~dp0"
call "%~dp0run-qgis-python.bat" -m gisds mcp --transport stdio
exit /b %ERRORLEVEL%
