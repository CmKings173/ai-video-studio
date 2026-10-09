[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string] $Backup,
    [ValidatePattern('^[a-z][a-z0-9_]{0,62}$')]
    [string] $Database = ("studio_restore_" + [Guid]::NewGuid().ToString("N")),
    [ValidatePattern('^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$')]
    [string] $Bucket = ("ai-video-restore-" + [Guid]::NewGuid().ToString("N")),
    [string] $ComposeFile = (Join-Path $PSScriptRoot "..\compose.yaml"),
    [string] $EnvironmentFile = (Join-Path $PSScriptRoot "..\..\.env"),
    [switch] $Force
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "backup_common.ps1")
$scriptRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$composeArguments = Get-ComposeArguments $ComposeFile $EnvironmentFile
$helperOptions = Get-HelperRunOptions
$backupPath = [IO.Path]::GetFullPath($Backup)
if (-not (Test-Path -LiteralPath $backupPath -PathType Container)) { throw "Backup directory not found" }
$dump = Join-Path $backupPath "studio.dump"
$objects = Join-Path $backupPath "minio"
$manifest = Join-Path $backupPath "manifest.sha256"
if (-not (Test-Path -LiteralPath $dump -PathType Leaf)) { throw "studio.dump is missing" }
if (-not (Test-Path -LiteralPath $objects -PathType Container)) { throw "minio snapshot is missing" }
if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) { throw "manifest.sha256 is missing" }
Assert-ChecksumManifest $backupPath
$snapshot = Assert-BackupArtifacts $backupPath
if ($Database -in @("studio", "postgres", "template0", "template1") -or
    $Bucket -eq "ai-video" -or $Bucket -eq $snapshot.source_bucket) {
    throw "Restore requires disposable database and bucket targets"
}
if (-not $Force) { throw "Restore is destructive; pass -Force after selecting an isolated target" }

$reports = Join-Path ([IO.Path]::GetTempPath()) ("studio-restore-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $reports | Out-Null
$containerDump = "/tmp/${Database}.dump"
Invoke-CheckedDocker -Arguments ($composeArguments + @("cp", $dump, "postgres:$containerDump"))
# A pre-existing database is an error, even with -Force. Never overwrite a live target.
Invoke-CheckedDocker -Arguments ($composeArguments + @("exec", "-T", "postgres", "createdb", "-U", "studio", $Database))
Invoke-CheckedDocker -Arguments ($composeArguments + @("exec", "-T", "postgres", "pg_restore",
    "-U", "studio", "-d", $Database, "--exit-on-error", $containerDump))
Invoke-CheckedDocker -Arguments ($composeArguments + @("run", "--rm", "--no-deps", "-T") + $helperOptions + @(
    "-v", "${objects}:/snapshot:ro", "-v", "${scriptRoot}:/infra-scripts:ro", "-v", "${reports}:/reports", "api",
    "python", "/infra-scripts/minio_snapshot.py", "restore", "--snapshot", "/snapshot",
    "--bucket", $Bucket, "--report", "/reports/restore-report.json"))
$report = Get-Content -Raw -LiteralPath (Join-Path $reports "restore-report.json") | ConvertFrom-Json
if ($report.status -ne "RESTORED" -or $report.target_bucket -ne $Bucket -or
    $report.object_count -ne $snapshot.object_count -or $report.total_bytes -ne $snapshot.total_bytes) {
    throw "Restore report is missing or inconsistent"
}
Write-Output "Restored PostgreSQL database '$Database' and MinIO bucket '$Bucket'. Report: $reports. Run reconciliation before cut-over."
