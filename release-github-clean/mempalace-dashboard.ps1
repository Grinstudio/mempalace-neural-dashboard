param(
    [switch]$Restart
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonVenv = Join-Path $projectRoot ".venv-mempalace\Scripts\python.exe"
$python = $pythonVenv
$dashboard = Join-Path $projectRoot "mempalace-dashboard.py"
$preflight = Join-Path $projectRoot "mempalace-preflight.ps1"

if (-not (Test-Path $pythonVenv)) {
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCmd) {
        Write-Error "Python runtime not found (.venv-mempalace missing and no 'python' in PATH)."
    }
    $python = "python"
    Write-Host "Using system python from PATH (project venv not found)." -ForegroundColor DarkYellow
}

if (-not (Test-Path $dashboard)) {
    Write-Error "Dashboard script not found: $dashboard"
}

if (Test-Path $preflight) {
    & $preflight -Quiet
}

$existing = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq "python.exe" -and
    $_.CommandLine -match "streamlit run" -and
    $_.CommandLine -match "mempalace-dashboard.py"
}

if ($existing -and -not $Restart) {
    $ids = ($existing | Select-Object -ExpandProperty ProcessId) -join ", "
    Write-Host "MemPalace dashboard is already running (PID: $ids)." -ForegroundColor Yellow
    Write-Host "Open: http://localhost:8501" -ForegroundColor Cyan
    Write-Host "Use -Restart to stop existing instance and start a new one." -ForegroundColor DarkGray
    return
}

if ($existing -and $Restart) {
    Write-Host "Stopping existing MemPalace dashboard instance..." -ForegroundColor Yellow
    foreach ($proc in $existing) {
        try {
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
        } catch {
            Write-Host "Could not stop PID $($proc.ProcessId): $($_.Exception.Message)" -ForegroundColor DarkYellow
        }
    }
    Start-Sleep -Milliseconds 500
}

Write-Host "Ensuring dashboard dependencies..."
& $python -m pip install streamlit plotly pandas streamlit-autorefresh | Out-Host

Write-Host "Starting MemPalace dashboard..."
& $python -m streamlit run $dashboard --server.headless true --browser.gatherUsageStats false
