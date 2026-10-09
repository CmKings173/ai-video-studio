# Shared checks for the host wrappers. Never include command arguments (credentials)
# in native failure messages.
function Get-ComposeArguments {
    param([string] $ComposeFile, [string] $EnvironmentFile)
    $compose = [IO.Path]::GetFullPath($ComposeFile)
    $environment = [IO.Path]::GetFullPath($EnvironmentFile)
    if (-not (Test-Path -LiteralPath $compose -PathType Leaf)) { throw "Compose file not found" }
    if (-not (Test-Path -LiteralPath $environment -PathType Leaf)) { throw "Compose environment file not found" }
    return @("compose", "--env-file", $environment, "-f", $compose)
}

function Get-HelperRunOptions {
    # Only the disposable MinIO helper overrides the image user. On POSIX, match
    # the invoking owner so normal 0755/0700 output directories remain writable.
    # Docker Desktop Windows bind mounts use host ACLs, not host POSIX identities.
    if ([IO.Path]::DirectorySeparatorChar -eq '\') { return @("--user", "0:0") }
    $helperUid = & id -u
    if ($LASTEXITCODE -ne 0) { throw "Cannot resolve helper user ID" }
    $helperGid = & id -g
    if ($LASTEXITCODE -ne 0) { throw "Cannot resolve helper group ID" }
    if ($helperUid -notmatch '^\d+$' -or $helperGid -notmatch '^\d+$') { throw "Invalid helper identity" }
    return @("--user", "${helperUid}:${helperGid}")
}

function Invoke-CheckedDocker {
    param([string[]] $Arguments)
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker operation failed (exit $LASTEXITCODE)" }
}

function Assert-BackupArtifacts {
    param([string] $Root)
    $dump = Join-Path $Root "studio.dump"
    if (-not (Test-Path -LiteralPath $dump -PathType Leaf) -or (Get-Item -LiteralPath $dump).Length -eq 0) {
        throw "studio.dump is missing or empty"
    }
    $snapshotRoot = Join-Path $Root "minio"
    $snapshot = Get-Content -Raw -LiteralPath (Join-Path $snapshotRoot "manifest.json") | ConvertFrom-Json
    $report = Get-Content -Raw -LiteralPath (Join-Path $Root "minio-report.json") | ConvertFrom-Json
    foreach ($artifact in @($snapshot, $report)) {
        if ($artifact.format_version -ne 1 -or $null -eq $artifact.objects -or
            $null -eq $artifact.object_count -or $null -eq $artifact.total_bytes -or
            $artifact.object_count -ne @($artifact.objects).Count -or -not $artifact.source_bucket) {
            throw "MinIO snapshot/report is invalid"
        }
    }
    if ($report.source_bucket -ne $snapshot.source_bucket -or
        $report.object_count -ne $snapshot.object_count -or $report.total_bytes -ne $snapshot.total_bytes) {
        throw "MinIO snapshot/report disagree"
    }
    [long] $total = 0
    foreach ($record in $snapshot.objects) {
        if ($record.file -notmatch '^objects/[a-f0-9]{64}$' -or
            $record.sha256 -notmatch '^[a-f0-9]{64}$' -or $null -eq $record.size -or $record.size -lt 0) {
            throw "Invalid snapshot object record"
        }
        $object = Join-Path $snapshotRoot $record.file
        if (-not (Test-Path -LiteralPath $object -PathType Leaf) -or
            (Get-Item -LiteralPath $object).Length -ne $record.size -or
            (Get-FileHash -LiteralPath $object -Algorithm SHA256).Hash.ToLowerInvariant() -ne $record.sha256) {
            throw "Snapshot object checksum or size mismatch"
        }
        $total += $record.size
    }
    if ($total -ne $snapshot.total_bytes) { throw "Snapshot byte count mismatch" }
    return $snapshot
}

function Assert-ChecksumManifest {
    param([string] $Root)
    $manifestPath = Join-Path $Root "manifest.sha256"
    $entries = @{}
    foreach ($line in Get-Content -LiteralPath $manifestPath) {
        if ($line -notmatch '^([a-fA-F0-9]{64})  (.+)$') { throw "Invalid checksum manifest entry" }
        $checksum = $Matches[1].ToLowerInvariant()
        $relative = $Matches[2]
        $file = [IO.Path]::GetFullPath((Join-Path $Root $relative))
        $prefix = $Root.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
        if (-not $file.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or
            $entries.ContainsKey($file) -or -not (Test-Path -LiteralPath $file -PathType Leaf)) {
            throw "Unsafe, duplicate, or missing checksum manifest file"
        }
        if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $checksum) {
            throw "Backup checksum mismatch"
        }
        $entries[$file] = $true
    }
    foreach ($file in Get-ChildItem -LiteralPath $Root -File -Recurse) {
        if ($file.FullName -ne $manifestPath -and -not $entries.ContainsKey($file.FullName)) {
            throw "Backup file is missing from checksum manifest"
        }
    }
    if ($entries.Count -eq 0) { throw "Checksum manifest is empty" }
}
