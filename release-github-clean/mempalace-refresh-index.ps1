param()

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot ".venv-mempalace\Scripts\python.exe"
$configPath = Join-Path $projectRoot "mempalace-indexing.json"

if (-not (Test-Path $python)) {
    Write-Error "Python venv not found: $python"
}
if (-not (Test-Path $configPath)) {
    Write-Error "Index config not found: $configPath. Run .\mempalace-setup-indexing.ps1 first."
}

$config = Get-Content -Raw -Encoding UTF8 $configPath | ConvertFrom-Json
if (-not $config.targets -or @($config.targets).Count -eq 0) {
    Write-Error "No indexing targets in $configPath. Run setup again."
}

$palacePath = [string]$config.palace_path
if (-not $palacePath) {
    $palacePath = Join-Path $projectRoot ".mempalace-child\palace"
}

$env:PYTHONUTF8 = "1"
$env:MEMPALACE_PALACE_PATH = $palacePath

Write-Host "Refreshing MemPalace index using config: $configPath" -ForegroundColor Cyan
Write-Host "Palace: $palacePath" -ForegroundColor Gray
Write-Host ""

foreach ($target in @($config.targets)) {
    $path = [string]$target.path
    $wing = [string]$target.wing
    $mode = [string]$target.mode
    if (-not (Test-Path $path)) {
        Write-Host "Skip missing path: $path" -ForegroundColor Yellow
        continue
    }

    if (-not $wing) { $wing = "project_code" }
    if (-not $mode) { $mode = "code" }

    Write-Host "Indexing: $path -> wing '$wing' (mode=$mode)" -ForegroundColor Green
    if ($mode -eq "convos") {
        & $python -m mempalace mine $path --mode convos --wing $wing
    } else {
        & $python -m mempalace mine $path --wing $wing
    }
    Write-Host ""
}

Write-Host "Current status:" -ForegroundColor Cyan
& $python -m mempalace status
