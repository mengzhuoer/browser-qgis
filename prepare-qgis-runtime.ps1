<#
.SYNOPSIS
    Build a relocatable QGIS runtime that can be shipped inside the release.

.DESCRIPTION
    Copies the local QGIS 3.44 install into a self-contained tree that works
    from ANY path (no "C:\Program Files\QGIS ..." anywhere), then verifies it
    by actually importing PyQGIS from the copy.

    Why it works: QGIS is an OSGeo4W install whose env scripts derive
    OSGEO4W_ROOT from their own location, so the tree is relocatable. Only a
    handful of files hardcode the original path, and they live in the parts we
    drop (grass) or in uninstaller scripts we never run.

    What gets dropped (and why it is safe):
      apps/grass, apps/saga   GRASS/SAGA Processing providers. The "grass"
                              provider registers but exposes 0 algorithms (GRASS
                              is not functional in this install anyway) and
                              "saga" is not registered at all. Neither counts
                              towards the 440 algorithms the server exposes.
      apps/Qt5/qml            Qt Quick / 3D / Charts QML modules. The server is
                              headless -- it renders raster maps through
                              QgsMapSettings, never through QML.
      i18n (except zh-Hans+en) 142 MB of translations for ~40 languages.
      Qt5/translations (except zh_CN+en)
      site-packages/wx        wxPython bindings, only used by the GRASS/SAGA
                              provider plugins.
      __pycache__ / *.pyc     regenerated on first run.

    What is essential and always kept:
      bin/ (DLLs + python3.exe)  etc/  lib/  share/proj  apps/Python312
      apps/qgis-ltr  apps/Qt5  apps/gdal  apps/gs
      share/proj carries proj.db -- without it every coordinate transform
      fails, so it is NOT optional.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File prepare-qgis-runtime.ps1
#>
[CmdletBinding()]
param(
    [string]$Source = '',
    [string]$Dest = '',
    [switch]$KeepQml,
    [switch]$KeepGrass,
    [switch]$KeepSaga
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Set-Location $root

if (-not $Source) {
    $Source = if ($env:QGIS_ROOT) { $env:QGIS_ROOT } else { 'C:\Program Files\QGIS 3.44.14' }
}
if (-not (Test-Path (Join-Path $Source 'bin\o4w_env.bat'))) {
    throw "not a QGIS/OSGeo4W install: $Source"
}
if (-not $Dest) { $Dest = Join-Path $root 'release\_qgis_runtime\qgis' }

Write-Output "source : $Source"
Write-Output "dest   : $Dest"

if (Test-Path $Dest) { Remove-Item $Dest -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Dest | Out-Null

function Copy-Tree {
    param(
        [string]$From,
        [string]$To,
        [string[]]$ExcludeDirs = @(),
        [string[]]$ExcludeFiles = @()
    )
    $fromAbs = Join-Path $Source $From
    if (-not (Test-Path $fromAbs)) {
        Write-Output "  skip (missing): $From"
        return
    }
    $toAbs = Join-Path $Dest $To
    New-Item -ItemType Directory -Force -Path $toAbs | Out-Null
    $a = @($fromAbs, $toAbs, '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/NP', '/R:1', '/W:1', '/COPY:DAT', '/DCOPY:DAT')
    if ($ExcludeDirs.Count) { $a += '/XD'; $a += $ExcludeDirs }
    if ($ExcludeFiles.Count) { $a += '/XF'; $a += $ExcludeFiles }
    & robocopy @a | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE): $From" }
    Write-Output ("  copied: {0,-22} -> {1}" -f $From, $To)
}

# ---------------------------------------------------------------- the tree

Write-Output ''
Write-Output '== copying =='

Copy-Tree 'bin' 'bin' @('__pycache__') @('*.pyc')
Copy-Tree 'etc' 'etc' @('__pycache__') @('*.pyc')
Copy-Tree 'lib' 'lib' @('__pycache__') @('*.pyc')
Copy-Tree 'share' 'share' @('__pycache__') @('*.pyc')
Copy-Tree 'apps\Python312' 'apps\Python312' @('__pycache__', 'wx') @('*.pyc')
Copy-Tree 'apps\qgis-ltr' 'apps\qgis-ltr' @('__pycache__', 'doc') @('*.pyc')
Copy-Tree 'apps\Qt5' 'apps\Qt5' @('__pycache__') @('*.pyc')
Copy-Tree 'apps\gdal' 'apps\gdal' @('__pycache__') @('*.pyc')
Copy-Tree 'apps\gs' 'apps\gs' @('__pycache__') @('*.pyc')

if (-not $KeepGrass) { Write-Output '  dropped: apps\grass (346 MB, provider has 0 algorithms)' }
else { Copy-Tree 'apps\grass' 'apps\grass' @('__pycache__') @('*.pyc') }
if (-not $KeepSaga) { Write-Output '  dropped: apps\saga (45 MB, provider not registered)' }
else { Copy-Tree 'apps\saga' 'apps\saga' @('__pycache__') @('*.pyc') }

# ---------------------------------------------------------------- trims

Write-Output ''
Write-Output '== trimming =='

# QGIS translations: keep only the two languages the UI actually offers.
$i18n = Join-Path $Dest 'apps\qgis-ltr\i18n'
if (Test-Path $i18n) {
    $removed = 0
    Get-ChildItem $i18n -File -Filter '*.qm' | Where-Object {
        $_.Name -notlike '*zh-Hans*' -and $_.Name -notlike '*_en*'
    } | ForEach-Object { Remove-Item $_.FullName -Force; $removed++ }
    Write-Output "  i18n: kept zh-Hans + en, removed $removed translation files"
}

# Qt translations: same idea.
$qtTr = Join-Path $Dest 'apps\Qt5\translations'
if (Test-Path $qtTr) {
    $removed = 0
    Get-ChildItem $qtTr -File -Filter '*.qm' | Where-Object {
        $_.Name -notlike '*zh_CN*' -and $_.Name -notlike '*en*'
    } | ForEach-Object { Remove-Item $_.FullName -Force; $removed++ }
    Write-Output "  Qt5/translations: kept zh_CN + en, removed $removed files"
}

if (-not $KeepQml) {
    $qml = Join-Path $Dest 'apps\Qt5\qml'
    if (Test-Path $qml) {
        Remove-Item $qml -Recurse -Force
        Write-Output '  dropped: apps\Qt5\qml (302 MB, headless server never loads QML)'
    }
}

# ---------------------------------------------------------------- size

$size = (Get-ChildItem $Dest -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
$count = (Get-ChildItem $Dest -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count
Write-Output ''
Write-Output ("runtime: {0:N0} MB / {1:N0} files" -f ($size / 1MB), $count)

# ---------------------------------------------------------------- verify

Write-Output ''
Write-Output '== verifying the runtime imports from its NEW location =='

$probe = @'
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from qgis.core import QgsApplication
print("sys.executable =", sys.executable)
print("sys.prefix     =", sys.prefix)
hits = [p for p in sys.path if "Program Files" in p or "QGIS 3." in p]
print("sys.path hits  =", hits)
app = QgsApplication([], False)
app.initQgis()
from qgis.core import QgsProviderRegistry
print("providers      =", len(QgsProviderRegistry.instance().providerList()))
app.exitQgis()
print("OK")
'@
$probeFile = Join-Path $Dest '_probe.py'
Set-Content -Path $probeFile -Value $probe -Encoding ASCII

# A .bat file, not a one-liner: cmd.exe expands %VAR% for the WHOLE line at
# parse time, so "call o4w_env.bat && set X=%OSGEO4W_ROOT%\..." would expand
# OSGEO4W_ROOT while it is still empty. One statement per line is the only
# way to let the env script take effect first -- exactly how
# run-qgis-python.bat does it.
$probeBat = @'
@echo off
call "%~dp0bin\o4w_env.bat"
set "PYTHONPATH=%OSGEO4W_ROOT%\apps\qgis-ltr\python"
set "PATH=%OSGEO4W_ROOT%\apps\qgis-ltr\bin;%PATH%"
set "QGIS_PREFIX_PATH=%OSGEO4W_ROOT:\=/%/apps/qgis-ltr"
set "QT_PLUGIN_PATH=%OSGEO4W_ROOT%\apps\qgis-ltr\qtplugins;%OSGEO4W_ROOT%\apps\Qt5\plugins"
set "QT_QPA_PLATFORM=offscreen"
set "PYTHONUTF8=1"
"%OSGEO4W_ROOT%\bin\python3.exe" "%~dp0_probe.py"
'@
$probeBatFile = Join-Path $Dest '_probe.bat'
Set-Content -Path $probeBatFile -Value $probeBat -Encoding ASCII

$out = & cmd.exe /c "`"$probeBatFile`"" 2>&1
$out | ForEach-Object { "  $_" }

Remove-Item $probeFile -Force -ErrorAction SilentlyContinue
Remove-Item $probeBatFile -Force -ErrorAction SilentlyContinue

$text = ($out | Out-String)
if ($text -notmatch 'OK') {
    Write-Output ''
    Write-Output 'RUNTIME VERIFY FAILED'
    exit 1
}
if ($text -match 'sys.path hits  =\s*\[(.+)\]') {
    $hits = $Matches[1].Trim()
    if ($hits) {
        Write-Output ''
        Write-Output "RUNTIME VERIFY FAILED: sys.path still references the original install: $hits"
        exit 1
    }
}

Write-Output ''
Write-Output 'runtime verified: imports from its own location, no reference to the original install.'
exit 0
