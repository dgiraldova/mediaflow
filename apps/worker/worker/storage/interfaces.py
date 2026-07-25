"""Storage contracts shared by local-disk and R2 implementations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class StorageObject:
    key: str
    byte_size: int
    etag: str
    content_type: str | None


class StorageClient(Protocol):
    def generate_presigned_put_url(
        self,
        *,
        key: str,
        content_type: str,
        ttl_seconds: int | None = None,
    ) -> str: ...

    async def head_object(self, *, key: str) -> StorageObject | None: ...

    async def put_object(
        self,
        *,
        key: str,
        body: bytes,
        content_type: str,
    ) -> StorageObject: ...

    async def read_object(
        self,
        *,
        key: str,
        byte_range: tuple[int, int] | None = None,
    ) -> bytes: ...

    async def download_to_path(self, *, key: str, destination_path: str) -> None: ...

    async def upload_from_path(
        self,
        *,
        key: str,
        source_path: str,
        content_type: str,
    ) -> StorageObject: ...

    async def delete_object(self, *, key: str) -> None: ...
