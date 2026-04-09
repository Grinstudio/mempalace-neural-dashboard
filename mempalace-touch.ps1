param(
    [Parameter(Mandatory = $true)]
    [string]$Tool,
    [string]$Query = "",
    [string]$Wing = "",
    [string]$Room = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot ".venv-mempalace\Scripts\python.exe"
$script = Join-Path $projectRoot "mempalace-route-pulse.py"
$eventsPath = Join-Path $projectRoot ".mempalace-analytics\search_events.jsonl"

if (-not (Test-Path $python)) { Write-Error "Python venv not found: $python" }
if (-not (Test-Path $script)) { Write-Error "Route pulse script not found: $script" }

$env:PYTHONUTF8 = "1"
$args = @($script, "--tool", $Tool, "--events-path", $eventsPath)
if ($Query) { $args += @("--query", $Query) }
if ($Wing) { $args += @("--wing", $Wing) }
if ($Room) { $args += @("--room", $Room) }

& $python @args
