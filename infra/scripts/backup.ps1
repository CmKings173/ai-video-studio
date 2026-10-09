[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string] $Destination,
    [string] $ComposeFile = (Join-Path $PSScriptRoot "..\compose.yaml"),
    [string] $EnvironmentFile = (Join-Path $PSScriptRoot "..\..\.env")
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "backup_common.ps1")
$scriptRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$composeArguments = Get-ComposeArguments $ComposeFile $EnvironmentFile
$helperOptions = Get-HelperRunOptions
$destinationRoot = [IO.Path]::GetFullPath($Destination)
New-Item -ItemType Directory -Force -Path $destinationRoot | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ") + "-" + [Guid]::NewGuid().ToString("N")
$target = Join-Path $destinationRoot $stamp
New-Item -ItemType Directory -Force -Path $target | Out-Null

Invoke-CheckedDocker -Arguments ($composeArguments + @("exec", "-T", "postgres", "pg_dump", "-U", "studio", "-d", "studio", "-Fc", "-f", "/tmp/studio.dump"))
Invoke-CheckedDocker -Arguments ($composeArguments + @("cp", "postgres:/tmp/studio.dump", (Join-Path $target "studio.dump")))
New-Item -ItemType Directory -Force -Path (Join-Path $target "minio") | Out-Null
Invoke-CheckedDocker -Arguments ($composeArguments + @("run", "--rm", "--no-deps", "-T") + $helperOptions + @(
    "-v", "${target}:/backup", "-v", "${scriptRoot}:/infra-scripts:ro", "api",
    "python", "/infra-scripts/minio_snapshot.py", "backup", "--bucket", "ai-video",
    "--destination", "/backup/minio", "--report", "/backup/minio-report.json"))
$null = Assert-BackupArtifacts $target

$files = Get-ChildItem -LiteralPath $target -File -Recurse | Sort-Object FullName
$manifest = foreach ($file in $files) {
    $hash = Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256
    "{0}  {1}" -f $hash.Hash.ToLowerInvariant(), $file.FullName.Substring($target.Length + 1)
}
Set-Content -LiteralPath (Join-Path $target "manifest.sha256") -Value $manifest -Encoding UTF8
Assert-ChecksumManifest $target
@{
    completed_at = (Get-Date).ToUniversalTime().ToString("o")
    backup_path = $target
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $destinationRoot "backup-status.json") -Encoding UTF8
Write-Output $target
