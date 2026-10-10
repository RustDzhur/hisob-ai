"""Хранилище файлов за интерфейсом: S3-совместимое (SeaweedFS/любой S3). Ключи — по содержимому, не по имени файла."""

from collections.abc import Iterator
from functools import lru_cache
from typing import Any, Protocol

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.platform.config import get_settings


class ObjectStorage(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def stream(self, key: str, chunk_size: int = 64 * 1024) -> Iterator[bytes]: ...
    def exists(self, key: str) -> bool: ...


class S3Storage:
    def __init__(self) -> None:
        s = get_settings()
        self._bucket = s.s3_bucket
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint_url,
            aws_access_key_id=s.s3_access_key,
            aws_secret_access_key=s.s3_secret_key,
            region_name=s.s3_region,
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 3},
                s3={"addressing_style": "path"},
            ),
        )
        self._ready = False

    def _ensure_bucket(self) -> None:
        if self._ready:
            return
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError:
            try:
                self._client.create_bucket(Bucket=self._bucket)
            except ClientError as exc:  # параллельный запрос мог создать бакет первым
                if exc.response.get("Error", {}).get("Code") not in (
                    "BucketAlreadyOwnedByYou",
                    "BucketAlreadyExists",
                ):
                    raise
        self._ready = True

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self._ensure_bucket()
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)

    def stream(self, key: str, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
        self._ensure_bucket()
        body = self._client.get_object(Bucket=self._bucket, Key=key)["Body"]
        try:
            yield from body.iter_chunks(chunk_size)
        finally:
            body.close()

    def exists(self, key: str) -> bool:
        self._ensure_bucket()
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError:
            return False
        return True


@lru_cache
def get_storage() -> ObjectStorage:
    return S3Storage()
