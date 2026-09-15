from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from apps.api.app.integrations.minio import (
    AssetObjectMissingError,
    AssetStore,
    AssetStoreError,
    AssetStoreUnavailableError,
    _translate_storage_error,
)


def _client_error(code: str, status: int = 400) -> ClientError:
    return ClientError(
        error_response={"Error": {"Code": code, "Message": f"Mock error {code}"}},
        operation_name="MockOperation",
    )


def test_translate_storage_error_classification():
    # NoSuchKey is object-level missing
    err = _translate_storage_error(_client_error("NoSuchKey", 404))
    assert isinstance(err, AssetObjectMissingError)

    # NoSuchObject is object-level missing
    err = _translate_storage_error(_client_error("NoSuchObject", 404))
    assert isinstance(err, AssetObjectMissingError)

    # NoSuchBucket is infrastructure / bucket-level unavailable
    err = _translate_storage_error(_client_error("NoSuchBucket", 404))
    assert isinstance(err, AssetStoreUnavailableError)
    assert not isinstance(err, AssetObjectMissingError)

    # 503 Service Unavailable is infrastructure unavailable
    err = _translate_storage_error(
        ClientError(
            error_response={
                "Error": {"Code": "ServiceUnavailable", "Message": "Service unavailable"},
                "ResponseMetadata": {"HTTPStatusCode": 503},
            },
            operation_name="MockOperation",
        )
    )
    assert isinstance(err, AssetStoreUnavailableError)


class FakeBotoClient:
    def __init__(
        self,
        put_error: Exception | None = None,
        head_response: dict | None = None,
        head_error: Exception | None = None,
    ):
        self.put_error = put_error
        self.head_response = head_response or {}
        self.head_error = head_error

    def put_object(self, **kwargs):
        if self.put_error:
            raise self.put_error
        return {}

    def head_object(self, **kwargs):
        if self.head_error:
            raise self.head_error
        return self.head_response


@pytest.mark.asyncio
async def test_asset_store_put_translates_503_to_unavailable():
    client = FakeBotoClient(
        put_error=ClientError(
            error_response={
                "Error": {"Code": "SlowDown", "Message": "Slow down"},
                "ResponseMetadata": {"HTTPStatusCode": 503},
            },
            operation_name="PutObject",
        )
    )
    store = AssetStore(client=client, public_client=client)
    with pytest.raises(AssetStoreUnavailableError):
        await store.put("key.txt", b"payload", "text/plain")


@pytest.mark.asyncio
async def test_asset_store_put_translates_nosuchbucket_to_unavailable():
    client = FakeBotoClient(put_error=_client_error("NoSuchBucket", 404))
    store = AssetStore(client=client, public_client=client)
    with pytest.raises(AssetStoreUnavailableError):
        await store.put("key.txt", b"payload", "text/plain")


@pytest.mark.asyncio
async def test_asset_store_put_immutable_conflict_same_bytes_succeeds():
    import hashlib

    data = b"same-bytes"
    checksum = hashlib.sha256(data).hexdigest()
    client = FakeBotoClient(
        put_error=ClientError(
            error_response={"Error": {"Code": "PreconditionFailed", "Message": "Condition failed"}},
            operation_name="PutObject",
        ),
        head_response={
            "ContentLength": len(data),
            "ContentType": "text/plain",
            "Metadata": {"sha256": checksum},
        },
    )
    store = AssetStore(client=client, public_client=client)
    result = await store.put("immutable.txt", data, "text/plain")
    assert result["checksum"] == checksum
    assert result["size"] == len(data)


@pytest.mark.asyncio
async def test_asset_store_put_immutable_conflict_different_bytes_fails():
    import hashlib

    data = b"new-bytes"
    checksum = hashlib.sha256(b"different-bytes").hexdigest()
    client = FakeBotoClient(
        put_error=ClientError(
            error_response={"Error": {"Code": "PreconditionFailed", "Message": "Condition failed"}},
            operation_name="PutObject",
        ),
        head_response={
            "ContentLength": 999,
            "ContentType": "text/plain",
            "Metadata": {"sha256": checksum},
        },
    )
    store = AssetStore(client=client, public_client=client)
    with pytest.raises(
        AssetStoreError, match="Immutable object key already contains different data"
    ):
        await store.put("immutable.txt", data, "text/plain")
