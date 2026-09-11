"""Async S3-compatible media store. Immutable keys are conditionally created."""

from __future__ import annotations

import asyncio
import hashlib
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


class AssetStoreUnavailableError(AssetStoreError):
    """Raised when the object store cannot answer reliably right now."""


def _translate_storage_error(exc: BaseException) -> AssetStoreError:
    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {})
        code = str(error.get("Code", ""))
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if (
            code in {"404", "NoSuchKey", "NoSuchObject", "NoSuchBucket", "NotFound"}
            or status == 404
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
        self.max_bytes = int(getattr(settings, "max_upload_bytes", 512 * 1024 * 1024))
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
        limit = self.max_bytes if max_bytes is None else min(max_bytes, self.max_bytes)

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

    async def put(self, key: str, data: bytes, content_type: str) -> dict:
        self._key(key)
        if not data or len(data) > self.max_bytes:
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
            if str(exc.response.get("Error", {}).get("Code")) not in {
                "PreconditionFailed",
                "412",
                "ConditionalRequestConflict",
                "409",
            }:
                raise
            # A replay can reuse exactly the same object; a conflicting writer
            # may never overwrite existing bytes under this immutable key.
            existing = await self.head(key)
            if existing["checksum"] != checksum or existing["size"] != len(data):
                raise AssetStoreError(
                    "Immutable object key already contains different data"
                ) from exc
        return {"key": key, "checksum": checksum, "size": len(data), "content_type": content_type}

    put_bytes = put
    put_staging = put

    async def promote_immutable(
        self, staging_key: str, immutable_key: str, content_type: str
    ) -> dict:
        return await self.put(immutable_key, await self.read(staging_key), content_type)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=self._key(key))

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
