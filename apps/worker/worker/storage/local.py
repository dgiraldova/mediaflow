"""Filesystem-backed media storage for local development."""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import os
import shutil
import uuid
from pathlib import Path
from urllib.parse import quote

from worker.logging import get_logger
from worker.storage.interfaces import StorageObject

logger = get_logger(__name__)


class LocalStorage:
    def __init__(
        self,
        *,
        root: str,
        public_base_url: str = "http://127.0.0.1:8001",
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._public_base_url = public_base_url.rstrip("/")

    def _path(self, key: str) -> Path:
        if not key or key.startswith("/") or ".." in key.split("/"):
            raise ValueError("Storage key must be a safe relative path")
        target = (self.root / key).resolve()
        if self.root != target and self.root not in target.parents:
            raise ValueError("Storage key escapes the local media directory")
        return target

    def generate_presigned_put_url(
        self,
        *,
        key: str,
        content_type: str,
        ttl_seconds: int | None = None,
    ) -> str:
        del content_type, ttl_seconds
        self._path(key)
        return f"{self._public_base_url}/uploads/{quote(key, safe='/')}"

    def generate_signed_get_url(self, *, key: str, ttl_seconds: int | None = None) -> str:
        del ttl_seconds
        self._path(key)
        return f"{self._public_base_url}/{quote(key, safe='/')}"

    async def head_object(self, *, key: str) -> StorageObject | None:
        path = self._path(key)

        def _head() -> StorageObject | None:
            if not path.is_file():
                return None
            stat = path.stat()
            return StorageObject(
                key=key,
                byte_size=stat.st_size,
                etag=f"{stat.st_mtime_ns:x}-{stat.st_size:x}",
                content_type=mimetypes.guess_type(path.name)[0],
            )

        return await asyncio.to_thread(_head)

    async def put_object(self, *, key: str, body: bytes, content_type: str) -> StorageObject:
        del content_type
        path = self._path(key)

        def _put() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.part"
            try:
                temporary.write_bytes(body)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)

        await asyncio.to_thread(_put)
        stored = await self.head_object(key=key)
        if stored is None:  # pragma: no cover - defensive
            raise RuntimeError(f"Stored object {key} was not found")
        return stored

    async def read_object(
        self,
        *,
        key: str,
        byte_range: tuple[int, int] | None = None,
    ) -> bytes:
        path = self._path(key)

        def _read() -> bytes:
            with path.open("rb") as handle:
                if byte_range is None:
                    return handle.read()
                start, end = byte_range
                handle.seek(start)
                return handle.read(end - start + 1)

        return await asyncio.to_thread(_read)

    async def download_to_path(self, *, key: str, destination_path: str) -> None:
        source = self._path(key)
        destination = Path(destination_path)

        def _copy() -> None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)

        await asyncio.to_thread(_copy)
        logger.info("local_storage.object_downloaded", key=key)

    async def upload_from_path(
        self,
        *,
        key: str,
        source_path: str,
        content_type: str,
    ) -> StorageObject:
        del content_type
        destination = self._path(key)
        source = Path(source_path)

        def _copy() -> None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.part"
            try:
                shutil.copyfile(source, temporary)
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)

        await asyncio.to_thread(_copy)
        logger.info("local_storage.object_uploaded", key=key)
        stored = await self.head_object(key=key)
        if stored is None:  # pragma: no cover - defensive
            raise RuntimeError(f"Stored object {key} was not found")
        return stored

    async def delete_object(self, *, key: str) -> None:
        path = self._path(key)
        await asyncio.to_thread(path.unlink, True)
        logger.info("local_storage.object_deleted", key=key)

    async def checksum(self, *, key: str) -> str:
        path = self._path(key)

        def _checksum() -> str:
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()

        return await asyncio.to_thread(_checksum)
