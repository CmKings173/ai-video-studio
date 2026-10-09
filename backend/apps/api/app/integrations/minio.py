"""Async S3-compatible media store. Immutable keys are conditionally created."""

from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import boto3
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)


class AssetStoreError(RuntimeError):
    pass


class AssetObjectMissingError(AssetStoreError):
    """Raised only when the object store confirms an object is absent."""


class AssetObjectTooLargeError(AssetStoreError):
    """Raised when actual object bytes exceed the caller's trusted size limit."""


class AssetStoreUnavailableError(AssetStoreError):
    """Raised when the object store cannot answer reliably right now."""


def _translate_storage_error(exc: BaseException) -> AssetStoreError:
    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {})
        code = str(error.get("Code", ""))
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code == "NoSuchBucket":
            return AssetStoreUnavailableError(f"Storage bucket missing ({code})")
        if status == 503 or code in {"503", "SlowDown", "ServiceUnavailable"}:
            return AssetStoreUnavailableError(
                f"Object store service unavailable ({code or status})"
            )
        if code in {"NoSuchKey", "NoSuchObject"} or (
            (code in {"404", "NotFound"} or status == 404) and code != "NoSuchBucket"
        ):
            return AssetObjectMissingError("Object is missing")
        return AssetStoreUnavailableError(
            f"Object store request failed ({code or status or 'unknown'})"
        )
    if isinstance(
        exc,
        (
            BotoCoreError,
            ConnectionClosedError,
            ConnectTimeoutError,
            EndpointConnectionError,
            ReadTimeoutError,
            OSError,
            TimeoutError,
        ),
    ):
        return AssetStoreUnavailableError("Object store is temporarily unavailable")
    return AssetStoreUnavailableError("Object store failed without a reliable result")


class AssetStore:
    def __init__(self, settings: Any = None, client: Any = None, public_client: Any = None):
        self.bucket = getattr(settings, "minio_bucket", "ai-video")
        self.max_bytes = int(getattr(settings, "max_upload_bytes", 500 * 1024**2))
        self.max_generated_bytes = int(
            getattr(settings, "max_generated_output_bytes", 500 * 1024**2)
        )
        self.transfer_timeout = int(getattr(settings, "asset_validation_timeout_seconds", 900))
        endpoint = getattr(settings, "minio_endpoint", "http://127.0.0.1:9000")
        common = dict(
            aws_access_key_id=getattr(settings, "minio_access_key", None),
            aws_secret_access_key=getattr(settings, "minio_secret_key", None),
            region_name=getattr(settings, "minio_region", "us-east-1"),
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=10,
                read_timeout=30,
                retries={"max_attempts": 2},
            ),
        )
        self.client = client or boto3.client("s3", endpoint_url=endpoint, **common)
        public_endpoint = getattr(settings, "minio_public_endpoint", endpoint)
        self.public_client = public_client or (
            self.client
            if public_endpoint == endpoint
            else boto3.client("s3", endpoint_url=public_endpoint, **common)
        )

    @staticmethod
    def _key(key: str) -> str:
        if (
            not key
            or key.startswith("/")
            or "\\" in key
            or "\x00" in key
            or any(part in {".", "..", ""} for part in key.split("/"))
        ):
            raise AssetStoreError("Unsafe object key")
        return key

    async def presign_upload(self, key: str, content_type: str, expires: int = 900) -> dict:
        return await asyncio.to_thread(
            self.public_client.generate_presigned_post,
            Bucket=self.bucket,
            Key=self._key(key),
            Fields={"Content-Type": content_type},
            Conditions=[
                {"Content-Type": content_type},
                ["content-length-range", 1, self.max_bytes],
            ],
            ExpiresIn=expires,
        )

    async def presign_download(
        self, key: str, filename: str | None = None, expires: int = 900
    ) -> str:
        params = {"Bucket": self.bucket, "Key": self._key(key)}
        if filename:
            params["ResponseContentDisposition"] = "attachment; filename*=UTF-8''" + quote(
                filename, safe=""
            )
        return await asyncio.to_thread(
            self.public_client.generate_presigned_url,
            "get_object",
            Params=params,
            ExpiresIn=expires,
        )

    async def ensure_bucket(self) -> None:
        try:
            await asyncio.to_thread(self.client.head_bucket, Bucket=self.bucket)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code not in {"404", "NoSuchBucket", "NotFound"}:
                raise
            await asyncio.to_thread(self.client.create_bucket, Bucket=self.bucket)

    async def head(self, key: str) -> dict:
        try:
            value = await asyncio.to_thread(
                self.client.head_object, Bucket=self.bucket, Key=self._key(key)
            )
        except (ClientError, BotoCoreError, OSError, TimeoutError) as exc:
            raise _translate_storage_error(exc) from exc
        return {
            **value,
            "size": int(value["ContentLength"]),
            "content_type": value.get("ContentType", "application/octet-stream"),
            "checksum": value.get("Metadata", {}).get("sha256"),
            "key": key,
        }

    verify_object = head

    async def read(self, key: str, max_bytes: int | None = None) -> bytes:
        default = self.max_generated_bytes if key.startswith("outputs/") else self.max_bytes
        limit = default if max_bytes is None else max_bytes

        def fetch() -> bytes:
            value = self.client.get_object(Bucket=self.bucket, Key=self._key(key))
            body = value["Body"]
            try:
                if int(value.get("ContentLength", 0)) > limit:
                    raise AssetStoreError("Object exceeds permitted size")
                data = body.read(limit + 1)
                if len(data) > limit:
                    raise AssetStoreError("Object exceeds permitted size")
                return data
            finally:
                body.close()

        try:
            return await asyncio.to_thread(fetch)
        except (ClientError, BotoCoreError, OSError, TimeoutError) as exc:
            raise _translate_storage_error(exc) from exc

    get_bytes = read

    async def checksum_object(self, key: str, max_bytes: int) -> dict:
        """Verify actual persisted bytes without a second output-sized buffer."""
        self._key(key)

        def verify():
            deadline = time.monotonic() + self.transfer_timeout
            value = self.client.get_object(Bucket=self.bucket, Key=key)
            body = value["Body"]
            size = 0
            digest = hashlib.sha256()
            try:
                raw_declared = value.get("ContentLength")
                try:
                    declared = int(raw_declared) if raw_declared is not None else None
                except (TypeError, ValueError) as exc:
                    raise AssetStoreUnavailableError("Invalid object length") from exc
                if declared is not None and (declared <= 0 or declared > max_bytes):
                    raise AssetObjectTooLargeError("Object exceeds permitted size")
                while True:
                    if time.monotonic() > deadline:
                        raise TimeoutError("Object verification timed out")
                    chunk = body.read(min(1024 * 1024, max_bytes - size + 1))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise AssetObjectTooLargeError("Object exceeds permitted size")
                    digest.update(chunk)
                if size <= 0:
                    raise AssetStoreUnavailableError("Object is empty")
                if declared is not None and size != declared:
                    raise AssetStoreUnavailableError("Object download was truncated")
                return {"size": size, "checksum": digest.hexdigest()}
            finally:
                body.close()

        try:
            return await self._finish_thread(verify)
        except (ClientError, BotoCoreError, OSError, TimeoutError) as exc:
            raise _translate_storage_error(exc) from exc

    async def put(self, key: str, data: bytes, content_type: str) -> dict:
        self._key(key)
        limit = self.max_generated_bytes if key.startswith("outputs/") else self.max_bytes
        if not data or len(data) > limit:
            raise AssetStoreError("Invalid object size")
        checksum = hashlib.sha256(data).hexdigest()
        try:
            await asyncio.to_thread(
                self.client.put_object,
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                Metadata={"sha256": checksum},
                IfNoneMatch="*",
            )
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code not in {
                "PreconditionFailed",
                "412",
                "ConditionalRequestConflict",
                "409",
            } and status not in {409, 412}:
                raise _translate_storage_error(exc) from exc
            # A replay can reuse exactly the same object; a conflicting writer
            # may never overwrite existing bytes under this immutable key.
            existing = await self.head(key)
            if existing["checksum"] != checksum or existing["size"] != len(data):
                raise AssetStoreError(
                    "Immutable object key already contains different data"
                ) from exc
        except (BotoCoreError, OSError, TimeoutError) as exc:
            raise _translate_storage_error(exc) from exc
        return {"key": key, "checksum": checksum, "size": len(data), "content_type": content_type}

    put_bytes = put
    put_staging = put

    @staticmethod
    async def _finish_thread(operation, stopped=None):
        # Cancellation must not remove a temp file while its transfer thread
        # still owns it. Socket timeouts and the transfer deadline bound drain.
        task = asyncio.create_task(asyncio.to_thread(operation))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if stopped is not None:
                stopped.set()
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not task.cancelled():
                task.exception()  # observe transfer failure while preserving cancellation
            raise

    async def download_to_path(self, key: str, path: Path, max_bytes: int) -> dict:
        """Stream one consistent GET response to disk and hash its actual bytes."""
        self._key(key)
        if max_bytes < 1:
            raise AssetStoreError("Invalid object size limit")

        def fetch():
            deadline = time.monotonic() + self.transfer_timeout
            value = self.client.get_object(Bucket=self.bucket, Key=key)
            body = value["Body"]
            size = 0
            digest = hashlib.sha256()
            try:
                raw_declared = value.get("ContentLength")
                try:
                    declared = int(raw_declared) if raw_declared is not None else None
                except (TypeError, ValueError) as exc:
                    raise AssetStoreUnavailableError("Invalid object length") from exc
                if declared is not None and (declared <= 0 or declared > max_bytes):
                    raise AssetObjectTooLargeError("Object exceeds permitted size")
                with path.open("wb") as output:
                    while True:
                        if time.monotonic() > deadline:
                            raise TimeoutError("Object download timed out")
                        chunk = body.read(min(1024 * 1024, max_bytes - size + 1))
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > max_bytes:
                            raise AssetObjectTooLargeError("Object exceeds permitted size")
                        digest.update(chunk)
                        output.write(chunk)
                if size <= 0:
                    raise AssetStoreUnavailableError("Object is empty")
                if declared is not None and size != declared:
                    raise AssetStoreUnavailableError("Object download was truncated")
                return {
                    "size": size,
                    "checksum": digest.hexdigest(),
                    "content_type": value.get("ContentType", "application/octet-stream"),
                }
            except BaseException:
                path.unlink(missing_ok=True)
                raise
            finally:
                body.close()

        try:
            return await self._finish_thread(fetch)
        except (ClientError, BotoCoreError, OSError, TimeoutError) as exc:
            raise _translate_storage_error(exc) from exc

    async def put_file_immutable(
        self, key: str, path: Path, content_type: str, checksum: str, max_bytes: int
    ) -> dict:
        """Conditional destination creation from the exact validated local file.

        CopyObject offers no portable destination If-None-Match guarantee. A
        conditional streaming PUT avoids buffering and preserves immutability.
        """
        self._key(key)
        size = (await asyncio.to_thread(path.stat)).st_size
        if size <= 0 or size > max_bytes:
            raise AssetStoreError("Invalid object size")

        stopped = threading.Event()
        deadline = time.monotonic() + self.transfer_timeout

        class DeadlineReader:
            def __init__(self, source):
                self.source = source

            def check(self):
                if stopped.is_set() or time.monotonic() >= deadline:
                    raise TimeoutError("Object upload timed out")

            def read(self, size=-1):
                self.check()
                chunk = self.source.read(min(size, 1024 * 1024) if size >= 0 else 1024 * 1024)
                self.check()
                return chunk

            def seek(self, *args):
                self.check()
                return self.source.seek(*args)

            def __getattr__(self, name):
                return getattr(self.source, name)

        def send():
            with path.open("rb") as source:
                self.client.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=DeadlineReader(source),
                    ContentLength=size,
                    ContentType=content_type,
                    Metadata={"sha256": checksum},
                    IfNoneMatch="*",
                )
                if time.monotonic() >= deadline or stopped.is_set():
                    raise TimeoutError("Object upload timed out")

        collision = False
        try:
            await self._finish_thread(send, stopped)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code not in {
                "PreconditionFailed",
                "412",
                "ConditionalRequestConflict",
                "409",
            } and status not in {409, 412}:
                raise _translate_storage_error(exc) from exc
            collision = True
        except (BotoCoreError, OSError, TimeoutError) as exc:
            raise _translate_storage_error(exc) from exc
        actual = await self.checksum_object(key, max_bytes)
        if actual["checksum"] != checksum or actual["size"] != size:
            raise AssetStoreError("Persisted immutable object bytes do not match source")
        if collision:
            existing = await self.head(key)
            if existing["content_type"] != content_type:
                raise AssetStoreError("Immutable object key already contains different data")
        return {"key": key, "checksum": checksum, "size": size, "content_type": content_type}

    async def promote_immutable(
        self, staging_key: str, immutable_key: str, content_type: str
    ) -> dict:
        return await self.put(immutable_key, await self.read(staging_key), content_type)

    async def delete(self, key: str) -> None:
        try:
            await asyncio.to_thread(
                self.client.delete_object, Bucket=self.bucket, Key=self._key(key)
            )
        except (ClientError, BotoCoreError, OSError, TimeoutError) as exc:
            raise _translate_storage_error(exc) from exc

    async def list_objects(self, prefix: str = "") -> list[dict]:
        if prefix:
            self._key(prefix.rstrip("/"))

        def listing() -> list[dict]:
            pages = self.client.get_paginator("list_objects_v2").paginate(
                Bucket=self.bucket, Prefix=prefix
            )
            return [
                {"key": obj["Key"], "size": obj["Size"], "last_modified": obj["LastModified"]}
                for page in pages
                for obj in page.get("Contents", [])
            ]

        return await asyncio.to_thread(listing)

    async def health(self) -> bool:
        try:
            await asyncio.to_thread(self.client.head_bucket, Bucket=self.bucket)
            return True
        except Exception:
            return False
