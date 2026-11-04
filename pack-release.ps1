<#
.SYNOPSIS
    Package Browser QGIS as a distributable release archive.

.DESCRIPTION
    Builds "<OutDir>\BrowserQGIS-<version>.zip" straight from the source tree
    (no staging copy), then VERIFIES the archive before declaring success.

    What goes in:
      gisds/ web/ assets/ deps/ plugins/ tests/ + data/shared/ + the root files.
      deps/ is included on purpose: the whole point of this project is that it
      runs on the bundled QGIS Python with no venv and no install step.

    What stays out (and is asserted to be absent afterwards):
      logs/                     run logs
      data/users/*              per-user private data (can be gigabytes)
      data/gisds.db*            the user database (accounts, password hashes)
      data/admin_password.txt   the admin password in plain text
      data/bookmarks.json       user bookmarks
      data/jobs/*               transient algorithm outputs
      __pycache__/ *.pyc       build artifacts
      tests/_workspace          scratch space
      tests/sample_data/jobs    algorithm outputs left by the self-checks
      tests/sample_data/users   uploads left by the self-checks
      tests/out_render.png      a stray render

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File pack-release.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File pack-release.ps1 -OutDir D:\out
#>
[CmdletBinding()]
param(
    [string]$OutDir = '',
    [string]$Version = '',
    [switch]$SkipDeps,
    [switch]$Standalone,
    [string]$RuntimeDir = ''
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Set-Location $root

# ---------------------------------------------------------------- version

if (-not $Version) {
    $initPath = Join-Path $root 'gisds\__init__.py'
    $initText = Get-Content $initPath -Raw -Encoding UTF8
    if ($initText -match '__version__\s*=\s*"([^"]+)"') {
        $Version = $Matches[1]
    } else {
        throw "cannot read __version__ from $initPath"
    }
}
$pkgName = "BrowserQGIS-$Version"
if ($Standalone) { $pkgName = "$pkgName-standalone" }

# ---------------------------------------------------------------- what to pack

$includeDirs = @(
    'gisds',
    'web',
    'assets',
    'plugins',
    'tests',
    'data\shared'
)
if (-not $SkipDeps) { $includeDirs += 'deps' }

$includeFiles = @(
    'config.json',
    'config.example.json',
    'config.open.json',
    'requirements.txt',
    'symbology-style.db',
    'LICENSE',
    'README.md',
    'RELEASE_NOTES.md',
    'doctor.bat',
    'launcher.bat',
    'launcher-files.bat',
    'launcher-open.bat',
    'mcp.bat',
    'run-qgis-python.bat',
    'setup.ps1',
    'start.bat',
    'start-open.bat',
    'start.ps1',
    'pack-release.ps1',
    'prepare-qgis-runtime.ps1'
)

#: Extra trees mapped to a different prefix inside the package.
#: The QGIS runtime goes to "qgis/" so run-qgis-python.bat finds it next to
#: itself (that is the whole trick behind the standalone build).
$extraMaps = @()
if ($Standalone) {
    if (-not $RuntimeDir) { $RuntimeDir = Join-Path $root 'release\_qgis_runtime\qgis' }
    if (-not (Test-Path (Join-Path $RuntimeDir 'bin\o4w_env.bat'))) {
        throw "no QGIS runtime at '$RuntimeDir' -- run prepare-qgis-runtime.ps1 first"
    }
    $extraMaps += @{ Source = $RuntimeDir; Prefix = 'qgis' }
}

#: Empty directories that must exist in the package (the service creates them
#: on demand, but an empty dir is friendlier than a missing one).
$emptyDirs = @(
    'data/users',
    'data/jobs',
    'workspace'
)

function Test-Excluded {
    param([string]$RelPath)
    $r = $RelPath -replace '/', '\'
    if ($r -match '(^|\\)__pycache__(\\|$)') { return $true }
    if ($r -match '\.pyc$') { return $true }
    if ($r -match '(^|\\)\.DS_Store$') { return $true }
    if ($r -like 'tests\_workspace\*') { return $true }
    if ($r -like 'tests\sample_data\jobs\*') { return $true }
    if ($r -like 'tests\sample_data\users\*') { return $true }
    if ($r -eq 'tests\out_render.png') { return $true }
    if ($r -like '*\.deleted') { return $true }
    return $false
}

#: Password-ish keys in config.json. They MUST be empty in a distributed
#: package: the recipient gets a random initial admin password (written to
#: their own data\admin_password.txt) and no shared super account.
#: Shipping the real ones would hand every recipient a working admin key.
$secretConfigKeys = @('admin_password', 'super_admin_password', 'api_key')

# ---------------------------------------------------------------- collect

#: Each entry is @{ Full = <path on disk>; Entry = <path inside the package> }.
$entries = New-Object System.Collections.Generic.List[object]
foreach ($dir in $includeDirs) {
    $abs = Join-Path $root $dir
    if (-not (Test-Path $abs)) {
        Write-Output "skip (missing): $dir"
        continue
    }
    Get-ChildItem $abs -Recurse -File -Force | ForEach-Object {
        $rel = $_.FullName.Substring($root.Length + 1)
        if (-not (Test-Excluded $rel)) {
            $entries.Add(@{ Full = $_.FullName; Entry = $rel })
        }
    }
}
foreach ($f in $includeFiles) {
    $abs = Join-Path $root $f
    if ($f -eq 'config.json') { continue }   # sanitized separately, below
    if (Test-Path $abs) { $entries.Add(@{ Full = $abs; Entry = $f }) }
    else { Write-Output "skip (missing): $f" }
}
foreach ($m in $extraMaps) {
    $src = $m.Source
    $count = 0
    Get-ChildItem $src -Recurse -File -Force | ForEach-Object {
        $rel = $_.FullName.Substring($src.Length + 1)
        # the runtime carries its own .pyc / __pycache__; dropping them keeps
        # the archive small and they are regenerated on first run anyway
        if ($rel -match '(^|\\)__pycache__(\\|$)' -or $rel -match '\.pyc$') { return }
        if ($rel -like '_probe.*') { return }
        $entries.Add(@{ Full = $_.FullName; Entry = "$($m.Prefix)/$rel" })
        $count++
    }
    Write-Output ("runtime : {0} files from {1}" -f $count, $src)
}

# ---------------------------------------------------------------- zip

#: Add a file to the archive, reading it with **shared** access.
#:
#: ZipFile.CreateEntryFromFile opens the source with File.OpenRead, i.e.
#: it refuses to run while anything else has the file open. That bites:
#: a running service holds the sample data open (QGIS keeps the layer
#: loaded), and a self-check may be writing to it right now. Reading with
#: FileShare.ReadWrite lets packaging proceed anyway.
function Add-FileToZip {
    param($Zip, [string]$FullPath, [string]$EntryName)
    $entry = $Zip.CreateEntry($EntryName, [System.IO.Compression.CompressionLevel]::Optimal)
    $out = $entry.Open()
    try {
        $in = New-Object System.IO.FileStream(
            $FullPath, [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        try { $in.CopyTo($out) } finally { $in.Dispose() }
    } finally { $out.Dispose() }
}

if (-not $OutDir) { $OutDir = Join-Path $root 'release' }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$zipPath = Join-Path $OutDir "$pkgName.zip"
# Write to a temp name and rename it only when the archive is complete.
#
# Why: writing straight to the final name means an interrupted write (session
# reaped, disk full, someone killed it) leaves a TRUNCATED zip under the final
# name. It looks like a real package (the header is valid) but will not open.
# Hit for real: the regression reported "File is not a zip file" while the
# packaging step itself had reported "ok, 525 MB".
# With the atomic rename a half-written archive only ever exists as *.tmp and
# can never pose as the finished one.
$zipTmp = "$zipPath.tmp"
if (Test-Path $zipTmp) { Remove-Item $zipTmp -Force }
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$rawSize = 0
$zip = [System.IO.Compression.ZipFile]::Open($zipTmp, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($e in $entries) {
        $rawSize += (Get-Item $e.Full).Length
        $entry = "$pkgName/" + ($e.Entry -replace '\\', '/')
        try {
            Add-FileToZip $zip $e.Full $entry
        } catch {
            throw ("packaging failed: cannot read " + $e.Entry +
                   " (" + $_.Exception.Message + ")." +
                   " If it lives under data\, the service may be writing to it" +
                   " right now -- stop the service first.")
        }
    }
    foreach ($d in $emptyDirs) {
        $abs = Join-Path $root $d
        if (-not (Test-Path $abs)) { New-Item -ItemType Directory -Force -Path $abs | Out-Null }
        [void]$zip.CreateEntry("$pkgName/$d/")
    }

    # ---- config.json goes in sanitized: the password fields must be empty.
    # The repo's config.json is the *live* one -- it may carry a working
    # admin / super-admin password. Shipping it as-is would hand every
    # recipient a working admin key, so blank those fields here and check
    # it again after the archive is written.
    $cfgSrc = Join-Path $root 'config.json'
    $cfgTmp = Join-Path ([System.IO.Path]::GetTempPath()) ("gisds_cfg_" + [Guid]::NewGuid().ToString("N") + ".json")
    $cfg = Get-Content $cfgSrc -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($k in $secretConfigKeys) {
        if ($cfg.PSObject.Properties.Name -contains $k) { $cfg.$k = '' }
    }
    $cfg | ConvertTo-Json -Depth 8 | Set-Content -Path $cfgTmp -Encoding UTF8
    Add-FileToZip $zip $cfgTmp "$pkgName/config.json"
    Remove-Item $cfgTmp -Force -ErrorAction SilentlyContinue
} finally {
    $zip.Dispose()
}

# Rename only now that the archive is complete (a half-written one must never
# pose as the finished package).
Move-Item -Path $zipTmp -Destination $zipPath -Force

$zipInfo = Get-Item $zipPath
Write-Output ''
Write-Output "package : $pkgName"
Write-Output "files   : $($entries.Count) + $($emptyDirs.Count) empty dirs"
Write-Output "raw     : $([math]::Round($rawSize / 1MB, 1)) MB"
Write-Output "archive : $([math]::Round($zipInfo.Length / 1MB, 1)) MB"
Write-Output "path    : $zipPath"

# ---------------------------------------------------------------- verify

Write-Output ''
Write-Output '== verifying the archive =='
$problems = New-Object System.Collections.Generic.List[string]

$archive = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
try {
    $names = @($archive.Entries | ForEach-Object { $_.FullName })
    $body = @($names | Where-Object { -not $_.EndsWith('/') })

    # 1) no build artifacts
    $bad = @($body | Where-Object { $_ -match '__pycache__' -or $_ -match '\.pyc$' })
    if ($bad.Count) { $problems.Add("pyc/pycache leaked in: $($bad[0])") }

    # 2) no user data
    $bad = @($body | Where-Object { $_ -like "*/data/users/*" })
    if ($bad.Count) { $problems.Add("private user data leaked in: $($bad[0])") }

    # 3) no user database / credentials
    foreach ($needle in @('/data/gisds.db', 'admin_password.txt', '/data/bookmarks.json', '/data/jobs/')) {
        $bad = @($body | Where-Object { $_ -like "*$needle*" })
        if ($bad.Count) { $problems.Add("user state leaked in ($needle): $($bad[0])") }
    }

    # 4) no logs
    $bad = @($body | Where-Object { $_ -like '*/logs/*' })
    if ($bad.Count) { $problems.Add("logs leaked in: $($bad[0])") }

    # 4b) the packaged config.json must not carry a working password.
    # This one matters: the repo's config.json is the *live* one and may hold
    # a real admin / super-admin password. The archive gets a sanitized copy,
    # and this re-reads it to prove the sanitizing actually happened.
    $cfgEntry = $archive.GetEntry("$pkgName/config.json")
    if ($null -eq $cfgEntry) {
        $problems.Add("package has no config.json")
    } else {
        $sr = New-Object System.IO.StreamReader($cfgEntry.Open(), [System.Text.Encoding]::UTF8)
        $cfgText = $sr.ReadToEnd()
        $sr.Close()
        $pkgCfg = $cfgText | ConvertFrom-Json
        foreach ($k in $secretConfigKeys) {
            if ($pkgCfg.PSObject.Properties.Name -contains $k) {
                $v = [string]$pkgCfg.$k
                if ($v.Trim() -ne '') {
                    $problems.Add("config.json in the package still has a password in '$k'")
                }
            }
        }
    }

    # 4c) the "open" preset must not smuggle a credential either.
    #     It carries no password by design, but a hand-edit could add one.
    $openEntry = $archive.GetEntry("$pkgName/config.open.json")
    if ($null -eq $openEntry) {
        $problems.Add("package has no config.open.json")
    } else {
        $sr = New-Object System.IO.StreamReader($openEntry.Open(), [System.Text.Encoding]::UTF8)
        $openText = $sr.ReadToEnd()
        $sr.Close()
        $pkgOpen = $openText | ConvertFrom-Json
        foreach ($k in $secretConfigKeys) {
            if ($pkgOpen.PSObject.Properties.Name -contains $k) {
                if (([string]$pkgOpen.$k).Trim() -ne '') {
                    $problems.Add("config.open.json in the package has a password in '$k'")
                }
            }
        }
    }

    # 5) the things that MUST be there
    $mustHave = @(
        "$pkgName/config.json",
        "$pkgName/config.open.json",
        "$pkgName/README.md",
        "$pkgName/LICENSE",
        "$pkgName/run-qgis-python.bat",
        "$pkgName/start.bat",
        "$pkgName/start-open.bat",
        "$pkgName/launcher-open.bat",
        "$pkgName/gisds/core/gis_tools.py",
        "$pkgName/gisds/core/llm.py",
        "$pkgName/gisds/core/ai.py",
        "$pkgName/gisds/core/i18n.py",
        "$pkgName/gisds/core/processing.py",
        "$pkgName/gisds/api/routes_ai.py",
        "$pkgName/web/ai.js",
        "$pkgName/web/login.html",
        "$pkgName/web/index.html",
        "$pkgName/web/vendor/leaflet.js",
        "$pkgName/tests/check_passwordless.py"
    )
    foreach ($m in $mustHave) {
        if ($names -notcontains $m) { $problems.Add("missing from package: $m") }
    }

    # 6) deps present unless skipped
    if (-not $SkipDeps) {
        $depsCount = @($body | Where-Object { $_ -like "*/deps/*" }).Count
        if ($depsCount -lt 100) { $problems.Add("deps/ looks empty ($depsCount files)") }
        Write-Output "deps/ entries : $depsCount"
    }

    # 7) the bundled QGIS runtime, when this is the standalone build
    if ($Standalone) {
        $runtimeMust = @(
            "$pkgName/qgis/bin/o4w_env.bat",
            "$pkgName/qgis/bin/python3.exe",
            "$pkgName/qgis/share/proj/proj.db",
            "$pkgName/qgis/apps/qgis-ltr/python/qgis/core/__init__.py",
            "$pkgName/qgis/apps/Python312/python312.dll",
            "$pkgName/qgis/apps/gdal/share/gdal/gdalvrt.xsd"
        )
        foreach ($m in $runtimeMust) {
            if ($names -notcontains $m) { $problems.Add("runtime incomplete, missing: $m") }
        }
        $rtCount = @($body | Where-Object { $_ -like "$pkgName/qgis/*" }).Count
        if ($rtCount -lt 5000) { $problems.Add("runtime looks truncated ($rtCount files)") }
        Write-Output "qgis/ entries  : $rtCount"
    }

    Write-Output "entries total  : $($names.Count)"
} finally {
    $archive.Dispose()
}

if ($problems.Count) {
    Write-Output ''
    Write-Output 'PACKAGING FAILED:'
    foreach ($p in $problems) { Write-Output "  ! $p" }
    exit 1
}

Write-Output 'package verified: no user data, no credentials, no build artifacts.'
exit 0
