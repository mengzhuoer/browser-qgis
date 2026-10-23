# Run every self-check script in order and report pass/fail per script.
# QGIS-backed scripts use run-qgis-python.bat; the launcher check needs a
# system Python that actually ships tkinter (the QGIS runtime does not).
#
# **Log file names must be unique per run.** This script does not write the
# log itself -- the caller redirects it, e.g.
#   powershell -File tests\run_all_checks.ps1 > logs\run_<who>_<n>.txt
# Two sessions using the same file name overwrite each other's results (the
# `>` truncates), and then nobody can tell whose run was green.
#
# NOTE: this file must stay pure ASCII -- PowerShell 5.1 parses a BOM-less
# UTF-8 script as ANSI, and the mangled bytes can break the quoting.

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# Is the real instance (8848) alive before the run? Check again afterwards --
# **the self-checks killed the real instance once** (the pid-lineage backstop hit
# a recycled pid). This guard turns that class of accident from "figure it out
# later" into "the run goes red right here": alive before, gone after = some
# script killed it.
# NOTE: this file must stay pure ASCII -- PowerShell 5.1 parses a BOM-less
# UTF-8 script as ANSI, and the mangled bytes can break the quoting.
$prodBefore = @(Get-NetTCPConnection -LocalPort 8848 -State Listen -ErrorAction SilentlyContinue).Count -gt 0
if ($prodBefore) { Write-Output 'production instance on 8848: alive (before)' }

$qgisTests = @(
    'tests\check_core.py',
    'tests\check_api.py',
    'tests\check_auth.py',
    'tests\check_mcp.py',
    'tests\check_mcp_stdio.py',
    'tests\check_mcp_panels.py',
    'tests\check_new.py',
    'tests\check_processing_types.py',
    'tests\check_api_surface.py',
    'tests\check_plugin_console_mcp.py',
    'tests\check_panels.py',
    'tests\check_live.py',
    'tests\check_server_helper.py',
    'tests\check_webui.py',
    'tests\check_webui_panels.py',
    'tests\check_webui_login.py',
    'tests\check_ui_paths.py',
    'tests\check_webui_files.py',
    'tests\check_webui_sidefold.py',
    'tests\check_webui_settings.py',
    'tests\check_web_js_syntax.py',
    'tests\check_admin_section.py',
    'tests\check_webui_admin.py',
    'tests\check_web_merge.py',
    'tests\check_console.py',
    'tests\check_ai.py',
    'tests\check_plugins.py',
    'tests\check_tiles.py',
    'tests\check_password_note.py',
    'tests\check_password_flag.py',
    'tests\check_service_key.py',
    'tests\check_standalone.py'
)

$sysPy = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'
$results = @()

foreach ($t in $qgisTests) {
    Write-Output "==================== $t ===================="
    & "$root\run-qgis-python.bat" $t
    $results += [pscustomobject]@{ Script = $t; Exit = $LASTEXITCODE }
}

Write-Output "==================== tests\check_launcher.py ===================="
if (Test-Path $sysPy) {
    # The system Python must NOT inherit the QGIS runtime's PYTHONHOME/PYTHONPATH:
    # it would then load QGIS's Python 3.12 standard library and die at
    # "import re" with "AssertionError: SRE module mismatch" -- the two
    # tkinter scripts fail for a reason that has nothing to do with tkinter.
    # Harmless when the runner starts from a clean shell, but it breaks
    # whenever the runner is started from inside the QGIS runtime (e.g. an
    # agent running run-qgis-python.bat ... first).
    $savedHome = $env:PYTHONHOME
    $savedPath = $env:PYTHONPATH
    $savedEnc = $env:PYTHONIOENCODING
    $env:PYTHONHOME = $null
    $env:PYTHONPATH = $null
    # These two scripts print Chinese result lines. The system Python writes
    # stdout with the ANSI code page (GBK) by default, but the log file is
    # read as UTF-8 -- so the summary lines turn into mojibake and cannot be
    # grepped. Force UTF-8 so "== result: N passed ==" stays readable.
    $env:PYTHONIOENCODING = 'utf-8'
    try {
        & $sysPy "$root\tests\check_launcher.py"
        $results += [pscustomobject]@{ Script = 'tests\check_launcher.py'; Exit = $LASTEXITCODE }
        Write-Output "==================== tests\check_first_login.py ===================="
        & $sysPy "$root\tests\check_first_login.py"
        $results += [pscustomobject]@{ Script = 'tests\check_first_login.py'; Exit = $LASTEXITCODE }
    } finally {
        $env:PYTHONHOME = $savedHome
        $env:PYTHONPATH = $savedPath
        $env:PYTHONIOENCODING = $savedEnc
    }
} else {
    Write-Output "SKIP: system python not found at $sysPy"
    $results += [pscustomobject]@{ Script = 'tests\check_launcher.py'; Exit = 'SKIP' }
    $results += [pscustomobject]@{ Script = 'tests\check_first_login.py'; Exit = 'SKIP' }
}

Write-Output ''
Write-Output '==================== SUMMARY ===================='
$results | ForEach-Object { "{0,-28} exit={1}" -f $_.Script, $_.Exit }
# Wrap in @() so .Count works even when there is exactly ONE bad script:
# a bare Where-Object result is a single PSCustomObject, and .Count on it is
# $null -> the summary printed "FAILED:  script(s)" with a blank count.
$bad = @($results | Where-Object { $_.Exit -ne 0 })
if ($bad.Count -gt 0) {
    Write-Output "FAILED: $($bad.Count) script(s): $(($bad | ForEach-Object { $_.Script }) -join ', ')"
    # Do NOT exit here. The "final hygiene" block below carries the two
    # assertions that matter most exactly when a script failed:
    #   - the real instance on 8848 is still alive (before vs. after)
    #   - no leftover temp services, no leftover temp files
    # The failed script may well be the one that broke the workspace.
    # This used to be `exit 1`, which skipped the whole hygiene block.
    Write-Output '(continuing to the final hygiene checks - those lines tell whether the workspace survived)'
}

# Final hygiene check: a full run must not leave garbage behind.
# 1) Leftover temp DBs / password notes in tests/_workspace (name carries owner pid)
# 2) Orphan service processes -- taskkill /T sometimes misses a grandchild
#    (the middle cmd in the batch chain exits first, so python3 gets re-parented
#    and the process tree breaks). Those survivors keep holding the port and the
#    SQLite file; a locked file cannot be deleted (WinError 32), so dead files
#    pile up in the workspace.
# NOTE: this file must stay pure ASCII -- PowerShell 5.1 parses a BOM-less
# UTF-8 script as ANSI, and the mangled bytes can break the quoting.
Write-Output ''
Write-Output '==================== FINAL HYGIENE ===================='
$ws = Join-Path $root 'tests\_workspace'
# Match every temp artifact a self-check can leave behind, not just the
# "testserver_<port>_<pid>" naming: a stray *.db / *.db-wal / *.db-shm /
# <name>_admin_password.txt in here means something leaked too.
#
# The gisds.db family is the exception: check_api.py / check_auth.py point
# GISDS_DB_PATH here on purpose (it is the isolated workspace library), so it
# is always present. Counting it as a leftover makes the hygiene check cry
# wolf on every run.
# NOTE: this file must stay pure ASCII -- PowerShell 5.1 parses a BOM-less
# UTF-8 script as ANSI, and the mangled bytes can break the quoting.
$known = @('gisds.db', 'gisds.db-wal', 'gisds.db-shm')
$junk = @(Get-ChildItem $ws -ErrorAction SilentlyContinue |
    Where-Object { $known -notcontains $_.Name -and
                   ($_.Name -like 'testserver_*' -or
                    $_.Name -like '*.db' -or $_.Name -like '*.db-wal' -or
                    $_.Name -like '*.db-shm' -or $_.Name -like '*_admin_password.txt') })
if ($junk) {
    Write-Output "workspace leftovers: $($junk.Count) file(s): $($junk.Name -join ', ')"
    Write-Output '  (not the intentional gisds.db workspace library -- these are leftovers)'
} else {
    Write-Output 'workspace: no temp leftovers'
}
# Kill only the TEMP services started by the self-checks. They listen on a
# dynamic port (_free_port starts at 8851 and walks up); the real instance
# always uses 8848.
#
# The old logic was "kill every gisds serve process except the one listening
# on 8848". That had a hole: $prod is the pid that happens to be LISTENING on
# 8848 *right now*. When the real instance is restarting, it is not listening
# yet (QGIS init takes ~15s) -> $prod is empty -> the filter matches every
# process -> the instance that was booting gets killed as an "orphan" and the
# service never comes up. (Hit this for real: the service was gone after 15:09.)
#
# So: kill a gisds serve process only if it is listening on a port OTHER than
# 8848. A booting instance is not listening -> leave it alone; a live temp
# server is -> kill it.
$serveProcs = @(Get-CimInstance Win32_Process -Filter "Name='python3.exe'" |
                Where-Object { $_.CommandLine -like '*gisds*serve*' })
$orphans = @()
$skipped = @()
foreach ($proc in $serveProcs) {
    $listen = @(Get-NetTCPConnection -OwningProcess $proc.ProcessId -State Listen -ErrorAction SilentlyContinue)
    $onProdPort = @($listen | Where-Object { $_.LocalPort -eq 8848 }).Count -gt 0
    $onOtherPort = @($listen | Where-Object { $_.LocalPort -ne 8848 }).Count -gt 0
    if (-not ($onOtherPort -and -not $onProdPort)) { continue }
    # **Parent process still alive -> that service belongs to ANOTHER session.
    # Do not kill it.**
    #
    # What this cleanup targets is the services THIS run leaked: taskkill /T
    # sometimes misses a grandchild (the middle cmd exits first, python3 gets
    # re-parented), and those are the real orphans.
    #
    # A temp service started by a *parallel* session (e.g. another agent
    # running its UI self-check) has a live cmd.exe parent -- the old rule
    # killed it anyway ("listening on a port other than 8848"), so the other
    # session's service got taken down by our cleanup. Same accident class as
    # the historical "production instance disappeared during a regression run".
    $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($proc.ParentProcessId)" -ErrorAction SilentlyContinue
    if ($parent) {
        $skipped += $proc
        continue
    }
    $orphans += $proc
}
if ($skipped) {
    Write-Output "skipped live services (parent alive, belong to another session): $($skipped.ProcessId -join ', ')"
}
if ($orphans) {
    Write-Output "orphan service processes: $($orphans.Count): $($orphans.ProcessId -join ', ')"
    $orphans | ForEach-Object { taskkill /F /PID $_.ProcessId | Out-Null }
    Write-Output '  (killed; TestServer.stop() backstop should have caught them)'
} else {
    Write-Output 'no orphan service processes'
}

# Leftover headless browsers.
#
# The browser self-checks start Edge with a debugging port. If the script
# that started it died, Edge (and its children) keep running and keep holding
# that port -- so the NEXT check picks the same port, fails to bind, and then
# talks to the PREVIOUS run's browser over /json/list. The symptom is
# "green when run alone, crashes inside a regression".
#
# Only kill the ones whose parent is gone. A live parent means another
# session is using that browser right now -- leave it alone.
$edgeProcs = @(Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" |
                Where-Object { $_.CommandLine -like '*remote-debugging-port*' })
$edgeOrphans = @()
$edgeSkipped = @()
foreach ($ep in $edgeProcs) {
    $epParent = Get-CimInstance Win32_Process -Filter "ProcessId=$($ep.ParentProcessId)" -ErrorAction SilentlyContinue
    if ($epParent) { $edgeSkipped += $ep } else { $edgeOrphans += $ep }
}
if ($edgeSkipped) {
    Write-Output "skipped live browsers (parent alive, belong to another session): $($edgeSkipped.ProcessId -join ', ')"
}
if ($edgeOrphans) {
    Write-Output "orphan browser processes: $($edgeOrphans.Count): $($edgeOrphans.ProcessId -join ', ')"
    $edgeOrphans | ForEach-Object { taskkill /T /F /PID $_.ProcessId | Out-Null }
    Write-Output '  (killed; they were holding CDP ports)'
} else {
    Write-Output 'no orphan browser processes'
}

# Self-guard: the instance on 8848 is the REAL one and must never be killed.
# If this ever fires, the classification above regressed -- fail loudly instead
# of silently taking the service down.
$real = (Get-NetTCPConnection -LocalPort 8848 -State Listen -ErrorAction SilentlyContinue |
         Select-Object -First 1 -ExpandProperty OwningProcess)
if ($real -and @($orphans).ProcessId -contains $real) {
    Write-Output "BUG: the real instance (pid $real) was classified as an orphan"
    exit 1
}

# Check the real instance once more after the run: alive before, gone after =
# some script killed it. That kind of accident (the pid-lineage backstop hitting
# a recycled pid) is invisible to the "no orphan service processes" check --
# the backstop reported clean while the real instance was already dead.
# NOTE: this file must stay pure ASCII -- PowerShell 5.1 parses a BOM-less
# UTF-8 script as ANSI, and the mangled bytes can break the quoting.
$prodAfter = @(Get-NetTCPConnection -LocalPort 8848 -State Listen -ErrorAction SilentlyContinue).Count -gt 0
if ($prodBefore -and -not $prodAfter) {
    Write-Output 'BUG: the production instance on 8848 was alive before this run and is gone now.'
    Write-Output '      Some self-check killed it. Do NOT rely on pid to identify it --'
    Write-Output '      pids get reused. Identify by PORT (our temp servers use 88xx+).'
    exit 1
}
if ($prodAfter) { Write-Output 'production instance on 8848: alive (after)' }

# Exit code: a failed script still means a failed run -- but only decide that
# AFTER the hygiene checks above have run (they are the evidence of "the
# workspace is still intact").
if ($bad.Count -gt 0) { exit 1 }

Write-Output 'ALL SCRIPTS OK'
exit 0
