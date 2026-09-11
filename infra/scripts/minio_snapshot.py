"""Portable MinIO object snapshot, restore, and cross-store verification utility.

The host PowerShell wrappers run this module in the API image so boto3 and the
same MinIO connection settings used by the application are available. Object
keys are mapped to digest-named files to keep arbitrary S3 keys safe on every
host filesystem. S3 metadata is stored in the snapshot manifest and restored.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

FORMAT_VERSION = 1
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
CHUNK_BYTES = 1024 * 1024


def _client():
    endpoint = os.environ.get("MINIO_ENDPOINT", "http://minio:9000")
    access_key = os.environ.get("MINIO_ACCESS_KEY", "")
    secret_key = os.environ.get("MINIO_SECRET_KEY", "")
    if not access_key or not secret_key:
        raise RuntimeError("MINIO_ACCESS_KEY and MINIO_SECRET_KEY are required")
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4", retries={"max_attempts": 4}),
    )


def _validate_bucket(bucket: str) -> str:
    if not BUCKET_RE.fullmatch(bucket):
        raise ValueError(f"Invalid S3 bucket name: {bucket!r}")
    return bucket


def _json_dump(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _objects(client, bucket: str) -> Iterator[dict[str, Any]]:
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        yield from page.get("Contents", [])


def _stream_to_file(body, path: Path) -> tuple[int, str]:
    size = 0
    digest = hashlib.sha256()
    temporary = path.with_suffix(path.suffix + ".part")
    with temporary.open("wb") as output:
        while chunk := body.read(CHUNK_BYTES):
            output.write(chunk)
            size += len(chunk)
            digest.update(chunk)
    temporary.replace(path)
    return size, digest.hexdigest()


def _stream_digest(body) -> tuple[int, str]:
    size = 0
    digest = hashlib.sha256()
    while chunk := body.read(CHUNK_BYTES):
        size += len(chunk)
        digest.update(chunk)
    return size, digest.hexdigest()


def _file_digest(path: Path) -> tuple[int, str]:
    size = 0
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(CHUNK_BYTES):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _head_metadata(head: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "CacheControl": "cache_control",
        "ContentDisposition": "content_disposition",
        "ContentEncoding": "content_encoding",
        "ContentLanguage": "content_language",
        "ContentType": "content_type",
        "WebsiteRedirectLocation": "website_redirect_location",
    }
    metadata = {target: head[source] for source, target in fields.items() if head.get(source)}
    metadata["metadata"] = head.get("Metadata", {})
    return metadata


def _upload_args(record: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "cache_control": "CacheControl",
        "content_disposition": "ContentDisposition",
        "content_encoding": "ContentEncoding",
        "content_language": "ContentLanguage",
        "content_type": "ContentType",
        "website_redirect_location": "WebsiteRedirectLocation",
        "metadata": "Metadata",
    }
    source = record.get("s3_metadata", {})
    return {target: source[key] for key, target in fields.items() if source.get(key)}


def backup(bucket: str, destination: Path) -> dict[str, Any]:
    bucket = _validate_bucket(bucket)
    if destination.exists() and any(destination.iterdir()):
        raise RuntimeError(f"Snapshot destination is not empty: {destination}")
    object_dir = destination / "objects"
    object_dir.mkdir(parents=True, exist_ok=True)
    client = _client()
    records: list[dict[str, Any]] = []
    for summary in _objects(client, bucket):
        key = summary["Key"]
        filename = hashlib.sha256(key.encode("utf-8")).hexdigest()
        response = client.get_object(Bucket=bucket, Key=key)
        try:
            size, checksum = _stream_to_file(response["Body"], object_dir / filename)
        finally:
            response["Body"].close()
        if size != summary["Size"]:
            raise RuntimeError(f"Object size changed while snapshotting {key!r}")
        records.append(
            {
                "key": key,
                "file": f"objects/{filename}",
                "size": size,
                "sha256": checksum,
                "etag": str(response.get("ETag", "")).strip('"'),
                "s3_metadata": _head_metadata(response),
            }
        )
    records.sort(key=lambda item: item["key"])
    manifest = {
        "format_version": FORMAT_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "source_bucket": bucket,
        "object_count": len(records),
        "total_bytes": sum(item["size"] for item in records),
        "objects": records,
    }
    _json_dump(destination / "manifest.json", manifest)
    return manifest


def _load_manifest(snapshot: Path) -> dict[str, Any]:
    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format_version") != FORMAT_VERSION:
        raise RuntimeError(f"Unsupported object snapshot format: {manifest.get('format_version')}")
    if manifest.get("object_count") != len(manifest.get("objects", [])):
        raise RuntimeError("Object snapshot manifest count is inconsistent")
    return manifest


def _bucket_exists(client, bucket: str) -> bool:
    try:
        client.head_bucket(Bucket=bucket)
        return True
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in {"404", "NoSuchBucket", "NotFound"}:
            return False
        raise


def _delete_bucket_contents(client, bucket: str) -> None:
    batch: list[dict[str, str]] = []
    for item in _objects(client, bucket):
        batch.append({"Key": item["Key"]})
        if len(batch) == 1000:
            client.delete_objects(Bucket=bucket, Delete={"Objects": batch, "Quiet": True})
            batch = []
    if batch:
        client.delete_objects(Bucket=bucket, Delete={"Objects": batch, "Quiet": True})


def restore(snapshot: Path, bucket: str, *, replace: bool) -> dict[str, Any]:
    bucket = _validate_bucket(bucket)
    manifest = _load_manifest(snapshot)
    client = _client()
    if _bucket_exists(client, bucket):
        has_objects = next(_objects(client, bucket), None) is not None
        if has_objects and not replace:
            raise RuntimeError(f"Target bucket {bucket!r} is not empty; use --replace explicitly")
        if has_objects:
            _delete_bucket_contents(client, bucket)
    else:
        client.create_bucket(Bucket=bucket)

    restored = 0
    for record in manifest["objects"]:
        source = snapshot / record["file"]
        size, checksum = _file_digest(source)
        if size != record["size"] or checksum != record["sha256"]:
            raise RuntimeError(f"Snapshot object failed checksum validation: {record['key']!r}")
        client.upload_file(
            str(source),
            bucket,
            record["key"],
            ExtraArgs=_upload_args(record),
        )
        restored += 1
    return {
        "status": "RESTORED",
        "target_bucket": bucket,
        "object_count": restored,
        "total_bytes": manifest["total_bytes"],
    }


def _ready_assets(path: Path | None) -> list[dict[str, str]]:
    if path is None:
        return []
    with path.open(newline="", encoding="utf-8") as source:
        return [row for row in csv.DictReader(source) if row.get("status") == "READY"]


def verify(snapshot: Path, bucket: str, assets_csv: Path | None) -> dict[str, Any]:
    bucket = _validate_bucket(bucket)
    manifest = _load_manifest(snapshot)
    expected = {item["key"]: item for item in manifest["objects"]}
    client = _client()
    actual_keys = {item["Key"] for item in _objects(client, bucket)}
    missing = sorted(set(expected) - actual_keys)
    unexpected = sorted(actual_keys - set(expected))
    corrupt: list[str] = []
    for key in sorted(set(expected) & actual_keys):
        response = client.get_object(Bucket=bucket, Key=key)
        try:
            size, checksum = _stream_digest(response["Body"])
        finally:
            response["Body"].close()
        record = expected[key]
        if size != record["size"] or checksum != record["sha256"]:
            corrupt.append(key)

    ready_missing: list[str] = []
    ready_mismatch: list[str] = []
    for asset in _ready_assets(assets_csv):
        key = asset["object_key"]
        record = expected.get(key)
        if record is None or key not in actual_keys:
            ready_missing.append(key)
            continue
        checksum = asset.get("checksum") or ""
        size = int(asset.get("size_bytes") or 0)
        if (checksum and checksum != record["sha256"]) or size != record["size"]:
            ready_mismatch.append(key)

    status = (
        "PASS"
        if not (missing or unexpected or corrupt or ready_missing or ready_mismatch)
        else "FAIL"
    )
    return {
        "status": status,
        "target_bucket": bucket,
        "expected_objects": len(expected),
        "actual_objects": len(actual_keys),
        "missing_objects": missing,
        "unexpected_objects": unexpected,
        "corrupt_objects": corrupt,
        "ready_assets_missing_objects": sorted(set(ready_missing)),
        "ready_assets_checksum_or_size_mismatch": sorted(set(ready_mismatch)),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument("--bucket", required=True)
    backup_parser.add_argument("--destination", type=Path, required=True)
    backup_parser.add_argument("--report", type=Path, required=True)

    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--snapshot", type=Path, required=True)
    restore_parser.add_argument("--bucket", required=True)
    restore_parser.add_argument("--replace", action="store_true")
    restore_parser.add_argument("--report", type=Path, required=True)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--snapshot", type=Path, required=True)
    verify_parser.add_argument("--bucket", required=True)
    verify_parser.add_argument("--assets-csv", type=Path)
    verify_parser.add_argument("--report", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "backup":
            report = backup(args.bucket, args.destination)
        elif args.command == "restore":
            report = restore(args.snapshot, args.bucket, replace=args.replace)
        else:
            report = verify(args.snapshot, args.bucket, args.assets_csv)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        _json_dump(args.report, report)
        print(json.dumps(report, sort_keys=True))
        return 0 if report.get("status") != "FAIL" else 2
    except Exception as exc:
        print(f"minio snapshot operation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
