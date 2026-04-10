param(
    [ValidateSet("Daily", "Hourly")]
    [string]$Frequency = "Daily",
    [string]$Time = "03:00",
    [string]$TaskName = "MemPalaceMaintenance"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$scriptPath = Join-Path $projectRoot "mempalace-maintenance.ps1"

if (-not (Test-Path $scriptPath)) { Write-Error "Maintenance wrapper not found: $scriptPath" }

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -Mode auto"

if ($Frequency -eq "Hourly") {
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 1)
} else {
    $trigger = New-ScheduledTaskTrigger -Daily -At $Time
}

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Description "Auto maintenance for MemPalace analytics data." -Force | Out-Null
Write-Host "Scheduled task '$TaskName' created ($Frequency)."
