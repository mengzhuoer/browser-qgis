<#
.SYNOPSIS
    Start the GISDS REST service.

.DESCRIPTION
    Launches the GIS data server on the configured host/port (default
    0.0.0.0:8848). The console shows the local and LAN URLs.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File start.ps1
    powershell -ExecutionPolicy Bypass -File start.ps1 -Port 9000
#>
[CmdletBinding()]
param(
    [int]$Port = 0,
    [string]$Host = '0.0.0.0',
    [switch]$DemoData
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if (-not (Test-Path 'deps')) {
    Write-Host "Dependencies are missing. Running setup first..." -ForegroundColor Yellow
    & (Join-Path $PSScriptRoot 'setup.ps1')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if ($Port -gt 0) {
    $cfgPath = Join-Path $PSScriptRoot 'config.json'
    if (Test-Path $cfgPath) {
        $cfg = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $cfg.port = $Port
        $cfg.host = $Host
        $cfg | ConvertTo-Json -Depth 5 | Set-Content -Path $cfgPath -Encoding UTF8
    }
}

if ($DemoData) {
    & (Join-Path $PSScriptRoot 'run-qgis-python.bat') -m gisds demo-data
}

& (Join-Path $PSScriptRoot 'run-qgis-python.bat') -m gisds serve
exit $LASTEXITCODE
