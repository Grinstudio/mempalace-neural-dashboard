$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot ".venv-mempalace\Scripts\python.exe"
$dashboard = Join-Path $projectRoot "mempalace-dashboard.py"

if (-not (Test-Path $python)) {
    Write-Error "Python venv not found: $python"
}

if (-not (Test-Path $dashboard)) {
    Write-Error "Dashboard script not found: $dashboard"
}

Write-Host "Ensuring dashboard dependencies..."
& $python -m pip install streamlit plotly pandas streamlit-autorefresh | Out-Host

Write-Host "Starting MemPalace dashboard..."
& $python -m streamlit run $dashboard
