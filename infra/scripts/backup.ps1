[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string] $Destination,
    [string] $ComposeFile = "compose.yaml"
)

$ErrorActionPreference = "Stop"
$destinationRoot = [IO.Path]::GetFullPath($Destination)
New-Item -ItemType Directory -Force -Path $destinationRoot | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$target = Join-Path $destinationRoot $stamp
New-Item -ItemType Directory -Force -Path $target | Out-Null

docker compose -f $ComposeFile exec -T postgres pg_dump -U studio -d studio -Fc -f /tmp/studio.dump
docker compose -f $ComposeFile cp postgres:/tmp/studio.dump (Join-Path $target "studio.dump")
New-Item -ItemType Directory -Force -Path (Join-Path $target "minio") | Out-Null
docker compose -f $ComposeFile run --rm --no-deps -T -v "${target}:/backup" api `
    python /app/infra/scripts/minio_snapshot.py backup --bucket ai-video `
    --destination /backup/minio --report /backup/minio-report.json

$files = Get-ChildItem -LiteralPath $target -File -Recurse | Sort-Object FullName
$manifest = foreach ($file in $files) {
    $hash = Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256
    "{0}  {1}" -f $hash.Hash.ToLowerInvariant(), $file.FullName.Substring($target.Length + 1)
}
Set-Content -LiteralPath (Join-Path $target "manifest.sha256") -Value $manifest -Encoding UTF8
@{
    completed_at = (Get-Date).ToUniversalTime().ToString("o")
    backup_path = $target
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $destinationRoot "backup-status.json") -Encoding UTF8
Write-Output $target
