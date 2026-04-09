$ErrorActionPreference = "Stop"

param(
    [string]$SessionId = "",
    [ValidateSet("yes", "no", "unknown")]
    [string]$Helped = "unknown",
    [int]$MinutesSaved = 0,
    [string]$Note = "",
    [switch]$SkipScoreUpdate
)

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot ".venv-mempalace\Scripts\python.exe"
$scriptPath = Join-Path $projectRoot "mempalace-feedback.py"

if (-not (Test-Path $python)) {
    Write-Error "Python venv not found: $python"
}

if (-not (Test-Path $scriptPath)) {
    Write-Error "Script not found: $scriptPath"
}

$argsList = @(
    $scriptPath,
    "--helped", $Helped,
    "--minutes-saved", "$MinutesSaved",
    "--note", $Note,
    "--session-id", $SessionId
)

if ($SkipScoreUpdate) {
    $argsList += "--skip-score-update"
}

& $python @argsList
