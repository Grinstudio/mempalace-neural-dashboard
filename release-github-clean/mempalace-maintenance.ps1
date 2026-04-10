param(
    [ValidateSet("auto", "monitor", "apply")]
    [string]$Mode = "auto"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot ".venv-mempalace\Scripts\python.exe"
$script = Join-Path $projectRoot "mempalace-maintenance.py"
$analyticsDir = Join-Path $projectRoot ".mempalace-analytics"

if (-not (Test-Path $python)) { Write-Error "Python venv not found: $python" }
if (-not (Test-Path $script)) { Write-Error "Maintenance script not found: $script" }

$env:PYTHONUTF8 = "1"

Write-Host "Running MemPalace maintenance ($Mode)..."
& $python $script --analytics-dir $analyticsDir --mode $Mode
