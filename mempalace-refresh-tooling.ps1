param()

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot ".venv-mempalace\Scripts\python.exe"
$palacePath = Join-Path $projectRoot ".mempalace-child\palace"
$snapshotRoot = Join-Path $projectRoot "tmp\mempalace-tooling-index"

if (-not (Test-Path $python)) {
    Write-Error "Python venv not found: $python"
}

$env:PYTHONUTF8 = "1"
$env:MEMPALACE_PALACE_PATH = $palacePath

if (Test-Path $snapshotRoot) {
    Remove-Item -Recurse -Force $snapshotRoot
}
New-Item -ItemType Directory -Force -Path $snapshotRoot | Out-Null

$rootConfig = Join-Path $projectRoot "mempalace.yaml"
$snapshotConfig = Join-Path $snapshotRoot "mempalace.yaml"
if (Test-Path $rootConfig) {
    Copy-Item -Path $rootConfig -Destination $snapshotConfig -Force
}

# Build a focused snapshot of tooling files to avoid indexing entire project root.
$toolingFiles = @(
    "README.md",
    "LICENSE",
    ".cursor\mcp.json",
    ".cursor\rules\mempalace-priority-workflow.mdc",
    ".cursor\skills\mempalace-neural-memory\SKILL.md"
)

$toolingFiles += Get-ChildItem -Path $projectRoot -Filter "mempalace-*.py" -File | ForEach-Object { $_.Name }
$toolingFiles += Get-ChildItem -Path $projectRoot -Filter "mempalace-*.ps1" -File | ForEach-Object { $_.Name }
$toolingFiles = $toolingFiles | Sort-Object -Unique

foreach ($relPath in $toolingFiles) {
    $src = Join-Path $projectRoot $relPath
    if (-not (Test-Path $src)) { continue }
    $dst = Join-Path $snapshotRoot $relPath
    $dstDir = Split-Path -Parent $dst
    if ($dstDir -and -not (Test-Path $dstDir)) {
        New-Item -ItemType Directory -Force -Path $dstDir | Out-Null
    }
    Copy-Item -Path $src -Destination $dst -Force
}

if (-not (Test-Path $snapshotConfig)) {
    & $python -m mempalace init $snapshotRoot | Out-Null
}

Write-Host "Refreshing MemPalace index for tooling snapshot..." -ForegroundColor Cyan
& $python -m mempalace mine $snapshotRoot --wing mempalace_tooling

Write-Host ""
Write-Host "Current status:" -ForegroundColor Cyan
& $python -m mempalace status
