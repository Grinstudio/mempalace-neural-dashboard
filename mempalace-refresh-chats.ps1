$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot ".venv-mempalace\Scripts\python.exe"
$transcriptsPath = "C:\Users\user\.cursor\projects\d-PROJECTS-Minupidu-FTP\agent-transcripts"
$palacePath = Join-Path $projectRoot ".mempalace-child\palace"

if (-not (Test-Path $python)) {
    Write-Error "Python venv not found: $python"
}

if (-not (Test-Path $transcriptsPath)) {
    Write-Error "Transcripts path not found: $transcriptsPath"
}

$env:PYTHONUTF8 = "1"
$env:MEMPALACE_PALACE_PATH = $palacePath

Write-Host "Refreshing MemPalace index for Cursor transcripts..."
& $python -m mempalace mine $transcriptsPath --mode convos --wing cursor_chats

Write-Host ""
Write-Host "Current status:"
& $python -m mempalace status
