<#
.SYNOPSIS
    Set up the GISDS runtime environment (Windows).

.DESCRIPTION
    Installs the web-layer dependencies (fastapi / uvicorn / mcp) into the
    project's own "deps" folder and injects them via PYTHONPATH.

    Why not a venv? On OSGeo4W the python312.dll lives in QGIS' bin\, so the
    Windows loader binds it from there and CPython resolves sys.prefix away
    from the venv -- the venv silently degrades to the base interpreter.
    A self-contained deps folder avoids that entirely and leaves the QGIS
    installation untouched.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File setup.ps1
#>
[CmdletBinding()]
param(
    [string]$QGISRoot = $env:QGIS_ROOT
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    OK  $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    !!  $msg" -ForegroundColor Yellow }

if (-not $QGISRoot) { $QGISRoot = 'C:\Program Files\QGIS 3.44.14' }

Write-Step "Locating QGIS"
$qgisPython = Join-Path $QGISRoot 'apps\Python312\python.exe'
if (-not (Test-Path $qgisPython)) {
    Write-Warn "QGIS python not found at $qgisPython"
    Write-Host "    Set QGIS_ROOT to your QGIS install directory, e.g.:"
    Write-Host "      `$env:QGIS_ROOT = 'C:\Program Files\QGIS 3.44.14'"
    exit 1
}
Write-Ok "QGIS python: $qgisPython"

Write-Step "Installing web-layer dependencies into .\deps"
New-Item -ItemType Directory -Force -Path 'deps' | Out-Null
$req = 'fastapi>=0.115', 'uvicorn[standard]>=0.30', 'mcp>=1.2', 'httpx>=0.27'
& $qgisPython -m pip install --target 'deps' --upgrade @req
if ($LASTEXITCODE -ne 0) {
    Write-Warn "pip install failed (exit $LASTEXITCODE)"
    exit $LASTEXITCODE
}
Write-Ok ("deps installed (" + (Get-ChildItem 'deps' -Directory).Count + " packages)")

Write-Step "Verifying the stack"
$env:PATH = (Join-Path $QGISRoot 'apps\qgis-ltr\bin') + ';' + $env:PATH
$env:QGIS_PREFIX_PATH = (Join-Path $QGISRoot 'apps\qgis-ltr') -replace '\\', '/'
$env:PYTHONPATH = (Join-Path $QGISRoot 'apps\qgis-ltr\python') + ';' + (Join-Path $PSScriptRoot 'deps')
$check = @'
import qgis.core, fastapi, uvicorn, mcp, httpx, osgeo.gdal
from qgis.core import Qgis
print("    QGIS", Qgis.QGIS_VERSION, "| fastapi", fastapi.__version__, "| uvicorn", uvicorn.__version__)
'@
$check | & $qgisPython -
if ($LASTEXITCODE -ne 0) {
    Write-Warn "import check failed"
    exit $LASTEXITCODE
}

Write-Step "Done"
Write-Host ""
Write-Host "  Start the REST service :  .\start.bat        (or: .\start.ps1)"
Write-Host "  Start the MCP server   :  .\mcp.bat         (stdio, for OpenClaw)"
Write-Host "  Check the environment  :  .\run-qgis-python.bat -m gisds doctor"
Write-Host ""
