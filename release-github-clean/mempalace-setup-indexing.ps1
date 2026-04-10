param()

$ErrorActionPreference = "Stop"

function Read-NonEmpty([string]$Prompt, [string]$Default = "") {
    while ($true) {
        $suffix = ""
        if ($Default) { $suffix = " [$Default]" }
        $value = Read-Host "$Prompt$suffix"
        if (-not $value -and $Default) { return $Default }
        if ($value -and $value.Trim().Length -gt 0) { return $value.Trim() }
        Write-Host "Value cannot be empty." -ForegroundColor Yellow
    }
}

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$configPath = Join-Path $projectRoot "mempalace-indexing.json"

$config = @{
    palace_path = (Join-Path $projectRoot ".mempalace-child\palace")
    targets = @()
}

if (Test-Path $configPath) {
    try {
        $existing = Get-Content -Raw -Encoding UTF8 $configPath | ConvertFrom-Json
        if ($existing.palace_path) { $config.palace_path = [string]$existing.palace_path }
        if ($existing.targets) { $config.targets = @($existing.targets) }
    } catch {
        Write-Host "Warning: existing config is invalid JSON. Starting fresh." -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "MemPalace indexing setup" -ForegroundColor Cyan
Write-Host "Select folders to scan. You can add multiple targets." -ForegroundColor Gray
Write-Host ""

$config.palace_path = Read-NonEmpty -Prompt "Palace path" -Default $config.palace_path

while ($true) {
    Write-Host ""
    $pathInput = Read-Host "Folder path to index (leave empty to finish)"
    if (-not $pathInput -or $pathInput.Trim().Length -eq 0) { break }

    $fullPath = [System.IO.Path]::GetFullPath($pathInput.Trim())
    if (-not (Test-Path $fullPath)) {
        Write-Host "Path does not exist: $fullPath" -ForegroundColor Red
        continue
    }

    $defaultWing = "project_code"
    $defaultName = Split-Path -Leaf $fullPath
    $wing = Read-NonEmpty -Prompt "Wing name for this folder" -Default $defaultWing
    $mode = Read-Host "Mode (code/convos) [code]"
    if (-not $mode) { $mode = "code" }
    $mode = $mode.Trim().ToLowerInvariant()
    if ($mode -ne "code" -and $mode -ne "convos") {
        Write-Host "Unknown mode '$mode'. Using 'code'." -ForegroundColor Yellow
        $mode = "code"
    }

    $entry = @{
        name = $defaultName
        path = $fullPath
        wing = $wing
        mode = $mode
    }
    $config.targets += $entry
    Write-Host "Added: $($entry.path) -> wing '$($entry.wing)' (mode=$($entry.mode))" -ForegroundColor Green
}

if (-not $config.targets -or $config.targets.Count -eq 0) {
    Write-Host ""
    Write-Host "No targets configured. Nothing saved." -ForegroundColor Yellow
    exit 0
}

$json = $config | ConvertTo-Json -Depth 8
Set-Content -Path $configPath -Value $json -Encoding UTF8

Write-Host ""
Write-Host "Saved indexing config: $configPath" -ForegroundColor Green
Write-Host "Run next: .\mempalace-refresh-index.ps1" -ForegroundColor Cyan
