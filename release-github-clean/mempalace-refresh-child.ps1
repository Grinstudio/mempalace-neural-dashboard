param(
    [switch]$ChildOnly
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot ".venv-mempalace\Scripts\python.exe"
$childThemePath = Join-Path $projectRoot "themes\listeo-child"
$palacePath = Join-Path $projectRoot ".mempalace-child\palace"

if (-not (Test-Path $python)) {
    Write-Error "Python venv not found: $python"
}

if (-not (Test-Path $childThemePath)) {
    Write-Error "Child theme path not found: $childThemePath"
}

$env:PYTHONUTF8 = "1"
$env:MEMPALACE_PALACE_PATH = $palacePath

Write-Host "Refreshing MemPalace index for child theme..."
& $python -m mempalace mine $childThemePath

Write-Host ""
Write-Host "Current status:"
& $python -m mempalace status

if (-not $ChildOnly) {
    $toolingScript = Join-Path $projectRoot "mempalace-refresh-tooling.ps1"
    if (Test-Path $toolingScript) {
        Write-Host ""
        Write-Host "Refreshing tooling memory..." -ForegroundColor Cyan
        & powershell -NoProfile -ExecutionPolicy Bypass -File $toolingScript
    }
}
