"""Provider-agnostic object storage gateway for canonical content bodies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.settings import get_settings
from app.services.vendor_costs import record_vendor_usage_out_of_band

StorageProvider = Literal["local", "s3_compatible"]


@dataclass(frozen=True)
class StoredObjectMetadata:
    """Normalized metadata returned by storage HEAD/read operations."""

    provider: StorageProvider
    bucket: str | None
    key: str
    size_bytes: int | None = None
    etag: str | None = None


class ObjectStorageGateway(ABC):
    """Abstract object storage interface."""

    provider: StorageProvider

    @abstractmethod
    def put_text(self, *, key: str, text: str, content_type: str) -> StoredObjectMetadata:
        """Persist UTF-8 text."""

    @abstractmethod
    def get_text(self, *, key: str) -> str:
        """Fetch UTF-8 text."""

    @abstractmethod
    def exists(self, *, key: str) -> bool:
        """Return whether the key exists."""

    @abstractmethod
    def delete(self, *, key: str) -> None:
        """Delete one object if it exists."""

    @abstractmethod
    def head(self, *, key: str) -> StoredObjectMetadata | None:
        """Return metadata for an object when present."""

    @abstractmethod
    def copy(self, *, source_key: str, destination_key: str) -> StoredObjectMetadata:
        """Copy one object within the same provider."""


class LocalObjectStorageGateway(ObjectStorageGateway):
    """Filesystem-backed object storage implementation."""

    provider: StorageProvider = "local"

    def __init__(self, root_dir: Path) -> None:
        self._root_dir = root_dir.resolve()
        self._root_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, key: str) -> Path:
        return (self._root_dir / key).resolve()

    def put_text(self, *, key: str, text: str, content_type: str) -> StoredObjectMetadata:
        del content_type
        path = self._resolve_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return StoredObjectMetadata(
            provider=self.provider,
            bucket=None,
            key=key,
            size_bytes=path.stat().st_size,
        )

    def get_text(self, *, key: str) -> str:
        return self._resolve_path(key).read_text(encoding="utf-8")

    def exists(self, *, key: str) -> bool:
        return self._resolve_path(key).exists()

    def delete(self, *, key: str) -> None:
        path = self._resolve_path(key)
        if path.exists():
            path.unlink()

    def head(self, *, key: str) -> StoredObjectMetadata | None:
        path = self._resolve_path(key)
        if not path.exists():
            return None
        return StoredObjectMetadata(
            provider=self.provider,
            bucket=None,
            key=key,
            size_bytes=path.stat().st_size,
        )

    def copy(self, *, source_key: str, destination_key: str) -> StoredObjectMetadata:
        source = self._resolve_path(source_key)
        destination = self._resolve_path(destination_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        return StoredObjectMetadata(
            provider=self.provider,
            bucket=None,
            key=destination_key,
            size_bytes=destination.stat().st_size,
        )


class S3CompatibleObjectStorageGateway(ObjectStorageGateway):
    """S3-compatible implementation used for both AWS S3 and Cloudflare R2."""

    provider: StorageProvider = "s3_compatible"

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str | None,
        region_name: str | None,
        access_key: str | None,
        secret_key: str | None,
        timeout_seconds: int,
    ) -> None:
        session = boto3.session.Session()
        self._bucket = bucket
        self._client: BaseClient = session.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region_name,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(
                connect_timeout=timeout_seconds,
                read_timeout=timeout_seconds,
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )

    def _record_usage(
        self,
        *,
        model: str,
        operation: str,
        key: str,
        size_bytes: int | None = None,
    ) -> None:
        """Persist one S3-compatible storage API call."""
        record_vendor_usage_out_of_band(
            provider="s3_compatible",
            model=model,
            feature="object_storage",
            operation=operation,
            source="backend",
            usage={"request_count": 1},
            metadata={
                "bucket": self._bucket,
                "key": key,
                "size_bytes": size_bytes,
            },
        )

    def put_text(self, *, key: str, text: str, content_type: str) -> StoredObjectMetadata:
        body = text.encode("utf-8")
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
        )
        self._record_usage(
            model="put_object",
            operation="object_storage.put_text",
            key=key,
            size_bytes=len(body),
        )
        return self._head(key=key, record_usage=False) or StoredObjectMetadata(
            provider=self.provider,
            bucket=self._bucket,
            key=key,
        )

    def get_text(self, *, key: str) -> str:
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        body = response["Body"].read()
        self._record_usage(
            model="get_object",
            operation="object_storage.get_text",
            key=key,
            size_bytes=len(body),
        )
        return body.decode("utf-8")

    def exists(self, *, key: str) -> bool:
        return self.head(key=key) is not None

    def delete(self, *, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)
        self._record_usage(model="delete_object", operation="object_storage.delete", key=key)

    def head(self, *, key: str) -> StoredObjectMetadata | None:
        return self._head(key=key, record_usage=True)

    def _head(self, *, key: str, record_usage: bool) -> StoredObjectMetadata | None:
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            error_code = str(exc.response.get("Error", {}).get("Code") or "")
            if error_code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        if record_usage:
            self._record_usage(
                model="head_object",
                operation="object_storage.head",
                key=key,
                size_bytes=response.get("ContentLength"),
            )
        return StoredObjectMetadata(
            provider=self.provider,
            bucket=self._bucket,
            key=key,
            size_bytes=response.get("ContentLength"),
            etag=str(response.get("ETag") or "").strip('"') or None,
        )

    def copy(self, *, source_key: str, destination_key: str) -> StoredObjectMetadata:
        self._client.copy_object(
            Bucket=self._bucket,
            Key=destination_key,
            CopySource={"Bucket": self._bucket, "Key": source_key},
        )
        self._record_usage(
            model="copy_object",
            operation="object_storage.copy",
            key=destination_key,
        )
        return self._head(key=destination_key, record_usage=False) or StoredObjectMetadata(
            provider=self.provider,
            bucket=self._bucket,
            key=destination_key,
        )


_object_storage_gateway: ObjectStorageGateway | None = None


def build_object_storage_gateway() -> ObjectStorageGateway:
    """Create an object storage gateway from settings."""
    settings = get_settings()
    storage_settings = settings.storage
    if storage_settings.content_body_storage_provider == "local":
        return LocalObjectStorageGateway(root_dir=settings.content_body_root_dir)

    bucket = settings.content_body_storage_bucket
    if not bucket:
        raise ValueError("CONTENT_BODY_STORAGE_BUCKET must be set for s3_compatible storage")
    return S3CompatibleObjectStorageGateway(
        bucket=bucket,
        endpoint_url=settings.content_body_storage_endpoint,
        region_name=settings.content_body_storage_region,
        access_key=settings.content_body_storage_access_key,
        secret_key=settings.content_body_storage_secret_key,
        timeout_seconds=storage_settings.content_body_storage_timeout_seconds,
    )


def get_object_storage_gateway() -> ObjectStorageGateway:
    """Return a cached object storage gateway."""
    global _object_storage_gateway
    if _object_storage_gateway is None:
        _object_storage_gateway = build_object_storage_gateway()
    return _object_storage_gateway
