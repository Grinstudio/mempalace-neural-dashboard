param(
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonVenv = Join-Path $projectRoot ".venv-mempalace\Scripts\python.exe"
$smartSearch = Join-Path $projectRoot "mempalace-smart-search.py"
$dashboard = Join-Path $projectRoot "mempalace-dashboard.py"
$touchScript = Join-Path $projectRoot "mempalace-touch.ps1"
$analyticsDir = Join-Path $projectRoot ".mempalace-analytics"

$issues = @()
$warnings = @()

if (-not (Test-Path $pythonVenv)) {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        $issues += "Missing Python runtime: no .venv-mempalace and no 'python' in PATH."
    } else {
        $warnings += "Project venv not found, will use system 'python' from PATH."
    }
}
if (-not (Test-Path $smartSearch)) {
    $issues += "Missing script: mempalace-smart-search.py"
}
if (-not (Test-Path $dashboard)) {
    $issues += "Missing script: mempalace-dashboard.py"
}
if (-not (Test-Path $touchScript)) {
    $issues += "Missing script: mempalace-touch.ps1"
}
if (-not (Test-Path $analyticsDir)) {
    $warnings += "Analytics directory not found yet (will be created on first run): $analyticsDir"
}

$palacePath = if ($env:MEMPALACE_PALACE_PATH) { $env:MEMPALACE_PALACE_PATH } else { Join-Path $projectRoot ".mempalace-child\palace" }
if (-not (Test-Path $palacePath)) {
    $warnings += "Palace path not found: $palacePath"
}

if (-not $Quiet) {
    Write-Host "MemPalace preflight report" -ForegroundColor Cyan
    Write-Host "Project root: $projectRoot" -ForegroundColor DarkGray
}

if ($warnings.Count -gt 0 -and -not $Quiet) {
    foreach ($w in $warnings) {
        Write-Host "WARN: $w" -ForegroundColor Yellow
    }
}

if ($issues.Count -gt 0) {
    foreach ($i in $issues) {
        Write-Host "ERROR: $i" -ForegroundColor Red
    }
    throw "Preflight failed."
}

if (-not $Quiet) {
    Write-Host "Preflight passed." -ForegroundColor Green
}
