[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string] $Backup,
    [string] $Database = "studio_restore",
    [string] $ComposeFile = "compose.yaml",
    [switch] $Force
)

$ErrorActionPreference = "Stop"
$backupPath = [IO.Path]::GetFullPath($Backup)
if (-not (Test-Path -LiteralPath $backupPath -PathType Container)) { throw "Backup directory not found" }
$dump = Join-Path $backupPath "studio.dump"
$objects = Join-Path $backupPath "minio"
$manifest = Join-Path $backupPath "manifest.sha256"
if (-not (Test-Path -LiteralPath $dump -PathType Leaf)) { throw "studio.dump is missing" }
if (-not (Test-Path -LiteralPath $objects -PathType Container)) { throw "minio snapshot is missing" }
if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) { throw "manifest.sha256 is missing" }
$dumpLine = Get-Content -LiteralPath $manifest | Where-Object { $_ -match "studio\.dump$" } | Select-Object -First 1
if (-not $dumpLine) { throw "studio.dump checksum is missing" }
$expectedHash = ($dumpLine -split "\s+", 2)[0].ToLowerInvariant()
$actualHash = (Get-FileHash -LiteralPath $dump -Algorithm SHA256).Hash.ToLowerInvariant()
if ($expectedHash -ne $actualHash) { throw "studio.dump checksum mismatch" }
if (-not $Force) { throw "Restore is destructive; pass -Force after selecting an isolated target" }

docker compose -f $ComposeFile cp $dump postgres:/tmp/studio-restore.dump
docker compose -f $ComposeFile exec -T postgres createdb -U studio $Database 2>$null
docker compose -f $ComposeFile exec -T postgres pg_restore -U studio -d $Database --clean --if-exists /tmp/studio-restore.dump
docker compose -f $ComposeFile run --rm --no-deps -T -v "${objects}:/snapshot" api `
    python /app/infra/scripts/minio_snapshot.py restore --snapshot /snapshot `
    --bucket ai-video-restore --replace --report /snapshot/restore-report.json
Write-Output "Restored PostgreSQL database '$Database' and MinIO bucket 'ai-video-restore'. Run reconciliation before cut-over."
