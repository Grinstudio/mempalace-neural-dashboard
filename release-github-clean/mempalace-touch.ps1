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
$smartSearchScript = Join-Path $projectRoot "mempalace-smart-search.py"
$eventsPath = Join-Path $projectRoot ".mempalace-analytics\search_events.jsonl"
$lastSearchPath = Join-Path $projectRoot ".mempalace-analytics\last_search.json"
$defaultPalacePath = Join-Path $projectRoot ".mempalace-child\palace"
$mcpSmartThrottlePath = Join-Path $projectRoot ".mempalace-analytics\mcp_smartsearch_throttle.json"
$mcpSmartMinIntervalSec = 12

if (-not (Test-Path $python)) { Write-Error "Python venv not found: $python" }
if (-not (Test-Path $script)) { Write-Error "Route pulse script not found: $script" }

$env:PYTHONUTF8 = "1"

# Make neural dashboard feel "live" on MCP search:
# when mempalace_search is touched, also emit a real smart-search telemetry event.
if ($Tool -eq "mempalace_search" -and $Query -and (Test-Path $smartSearchScript)) {
    $nowUtc = [DateTime]::UtcNow
    $shouldRunSmart = $true
    if (Test-Path $mcpSmartThrottlePath) {
        try {
            $rawThrottle = Get-Content -Raw -Path $mcpSmartThrottlePath | ConvertFrom-Json
            $lastRaw = [string]($rawThrottle.last_run_utc)
            if ($lastRaw) {
                $lastRun = [DateTimeOffset]::Parse($lastRaw).UtcDateTime
                $elapsed = ($nowUtc - $lastRun).TotalSeconds
                $lastQuery = [string]($rawThrottle.last_query)
                $sameQuery = ($lastQuery -eq $Query)
                if ($sameQuery -and $elapsed -lt $mcpSmartMinIntervalSec) {
                    $shouldRunSmart = $false
                }
            }
        } catch {
            $shouldRunSmart = $true
        }
    }

    if ($shouldRunSmart) {
    $palacePath = if ($env:MEMPALACE_PALACE_PATH) { $env:MEMPALACE_PALACE_PATH } else { $defaultPalacePath }
    if (Test-Path $palacePath) {
        $smartArgs = @(
            $smartSearchScript,
            $Query,
            "--palace-path", $palacePath,
            "--top-k", "4",
            "--candidate-k", "12"
        )
        if ($Wing) { $smartArgs += @("--wing", $Wing) }
        if ($Room) { $smartArgs += @("--room", $Room) }
        & $python @smartArgs | Out-Null
            $throttleObj = @{
                last_run_utc = $nowUtc.ToString("o")
                last_query = $Query
            }
            $throttleObj | ConvertTo-Json | Set-Content -Path $mcpSmartThrottlePath -Encoding UTF8
            Write-Host "MCP smart-search emitted (top-k=4, candidate-k=12)." -ForegroundColor DarkGray
        }
    }
    else {
        Write-Host "MCP smart-search throttled (min interval ${mcpSmartMinIntervalSec}s)." -ForegroundColor DarkGray
    }
}

$args = @($script, "--tool", $Tool, "--events-path", $eventsPath, "--last-search-path", $lastSearchPath)
if ($Query) { $args += @("--query", $Query) }
if ($Wing) { $args += @("--wing", $Wing) }
if ($Room) { $args += @("--room", $Room) }

& $python @args
