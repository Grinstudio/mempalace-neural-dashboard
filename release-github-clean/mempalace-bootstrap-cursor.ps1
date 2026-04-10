param(
    [Parameter(Mandatory = $true)]
    [string]$TargetProjectPath,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

function Backup-IfExists {
    param(
        [string]$PathToBackup
    )
    if (Test-Path $PathToBackup) {
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $backupPath = "$PathToBackup.bak-$stamp"
        Copy-Item -Path $PathToBackup -Destination $backupPath -Recurse -Force
        Write-Host "Backup created: $backupPath" -ForegroundColor DarkYellow
    }
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$targetRoot = $TargetProjectPath
if (-not [System.IO.Path]::IsPathRooted($targetRoot)) {
    $targetRoot = Join-Path (Get-Location) $targetRoot
}
if (-not (Test-Path $targetRoot)) {
    New-Item -ItemType Directory -Path $targetRoot -Force | Out-Null
}
$targetRoot = (Resolve-Path -Path $targetRoot).Path
$targetCursor = Join-Path $targetRoot ".cursor"
$targetSkillsDir = Join-Path $targetCursor "skills\mempalace-neural-memory"
$targetRulesDir = Join-Path $targetCursor "rules"

$sourceSkill = Join-Path $scriptRoot ".cursor\skills\mempalace-neural-memory\SKILL.md"
$sourceRule = Join-Path $scriptRoot ".cursor\rules\mempalace-priority-workflow.mdc"
$sourceSkillsConfig = Join-Path $scriptRoot "skills-config.json"

if (-not (Test-Path $sourceSkill)) { throw "Source skill not found: $sourceSkill" }
if (-not (Test-Path $sourceRule)) { throw "Source rule not found: $sourceRule" }

New-Item -ItemType Directory -Path $targetSkillsDir -Force | Out-Null
New-Item -ItemType Directory -Path $targetRulesDir -Force | Out-Null

$targetSkill = Join-Path $targetSkillsDir "SKILL.md"
$targetRule = Join-Path $targetRulesDir "mempalace-priority-workflow.mdc"
$targetSkillsConfig = Join-Path $targetRoot "skills-config.json"

if (((Test-Path $targetSkill) -or (Test-Path $targetRule) -or (Test-Path $targetSkillsConfig)) -and -not $Force) {
    throw "Target files already exist. Re-run with -Force to overwrite with backup."
}

if ($Force) {
    Backup-IfExists -PathToBackup $targetSkill
    Backup-IfExists -PathToBackup $targetRule
    Backup-IfExists -PathToBackup $targetSkillsConfig
}

Copy-Item -Path $sourceSkill -Destination $targetSkill -Force
Copy-Item -Path $sourceRule -Destination $targetRule -Force
if (Test-Path $sourceSkillsConfig) {
    Copy-Item -Path $sourceSkillsConfig -Destination $targetSkillsConfig -Force
}

Write-Host "Cursor bootstrap completed." -ForegroundColor Green
Write-Host "Installed skill: $targetSkill" -ForegroundColor Cyan
Write-Host "Installed rule : $targetRule" -ForegroundColor Cyan
if (Test-Path $targetSkillsConfig) {
    Write-Host "Installed config: $targetSkillsConfig" -ForegroundColor Cyan
}
