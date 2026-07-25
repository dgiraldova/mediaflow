"""Cloudflare R2 client (S3-compatible API).

Wraps boto3 (sync) with ``asyncio.to_thread`` so it composes with async
Temporal activities without blocking the event loop. Buckets are private;
every read/write outside the worker process must go through a signed URL
with a short TTL (spec section 5.4 and 6.6 — never expose long-lived or
unsigned access).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from worker.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class R2Object:
    key: str
    byte_size: int
    etag: str
    content_type: str | None


@dataclass(frozen=True)
class MultipartUploadPart:
    part_number: int
    upload_url: str


@dataclass(frozen=True)
class MultipartUpload:
    upload_id: str
    key: str
    parts: list[MultipartUploadPart]


class R2Client:
    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
        region: str = "auto",
        signed_url_ttl_seconds: int = 3600,
        presigned_upload_ttl_seconds: int = 3600,
    ) -> None:
        self._bucket = bucket
        self._signed_url_ttl_seconds = signed_url_ttl_seconds
        self._presigned_upload_ttl_seconds = presigned_upload_ttl_seconds
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
            config=BotoConfig(signature_version="s3v4"),
        )

    # -- Direct-upload flow (spec section 11.1) ---------------------------

    def generate_presigned_put_url(
        self, *, key: str, content_type: str, ttl_seconds: int | None = None
    ) -> str:
        """Single-shot presigned PUT for smaller files."""
        return self._client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self._bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=ttl_seconds or self._presigned_upload_ttl_seconds,
        )

    async def create_multipart_upload(
        self, *, key: str, content_type: str, part_count: int, ttl_seconds: int | None = None
    ) -> MultipartUpload:
        """Presigned multipart upload for large browser-to-R2 uploads.

        The original media must never pass through Next.js or FastAPI
        (spec section 11.1), so every part URL is presigned individually
        and handed to the browser, which uploads directly to R2.
        """

        def _create() -> str:
            response = self._client.create_multipart_upload(
                Bucket=self._bucket, Key=key, ContentType=content_type
            )
            return response["UploadId"]

        upload_id = await asyncio.to_thread(_create)

        def _presign_part(part_number: int) -> str:
            return self._client.generate_presigned_url(
                "upload_part",
                Params={
                    "Bucket": self._bucket,
                    "Key": key,
                    "UploadId": upload_id,
                    "PartNumber": part_number,
                },
                ExpiresIn=ttl_seconds or self._presigned_upload_ttl_seconds,
            )

        parts = [
            MultipartUploadPart(part_number=n, upload_url=await asyncio.to_thread(_presign_part, n))
            for n in range(1, part_count + 1)
        ]
        return MultipartUpload(upload_id=upload_id, key=key, parts=parts)

    async def complete_multipart_upload(
        self, *, key: str, upload_id: str, parts: list[dict[str, Any]]
    ) -> None:
        """``parts`` is [{"PartNumber": int, "ETag": str}, ...] from the browser."""

        def _complete() -> None:
            self._client.complete_multipart_upload(
                Bucket=self._bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )

        await asyncio.to_thread(_complete)
        logger.info("r2.multipart_upload_completed", key=key, part_count=len(parts))

    async def abort_multipart_upload(self, *, key: str, upload_id: str) -> None:
        def _abort() -> None:
            self._client.abort_multipart_upload(Bucket=self._bucket, Key=key, UploadId=upload_id)

        await asyncio.to_thread(_abort)
        logger.info("r2.multipart_upload_aborted", key=key)

    # -- Signed access ------------------------------------------------------

    def generate_signed_get_url(self, *, key: str, ttl_seconds: int | None = None) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=ttl_seconds or self._signed_url_ttl_seconds,
        )

    # -- Worker-side object access -------------------------------------------

    async def head_object(self, *, key: str) -> R2Object | None:
        def _head() -> R2Object | None:
            try:
                response = self._client.head_object(Bucket=self._bucket, Key=key)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey"}:
                    return None
                raise
            return R2Object(
                key=key,
                byte_size=response["ContentLength"],
                etag=response["ETag"].strip('"'),
                content_type=response.get("ContentType"),
            )

        return await asyncio.to_thread(_head)

    async def read_object(
        self, *, key: str, byte_range: tuple[int, int] | None = None
    ) -> bytes:
        """Read an object into memory, optionally a byte range.

        Used by the media-delivery server to satisfy browser Range requests
        for video seeking. Not for whole large files in the pipeline — those
        stream to local disk via ``download_to_path``.
        """

        def _read() -> bytes:
            kwargs: dict[str, Any] = {"Bucket": self._bucket, "Key": key}
            if byte_range is not None:
                kwargs["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"
            response = self._client.get_object(**kwargs)
            body: bytes = response["Body"].read()
            return body

        return await asyncio.to_thread(_read)

    async def download_to_path(self, *, key: str, destination_path: str) -> None:
        def _download() -> None:
            self._client.download_file(self._bucket, key, destination_path)

        await asyncio.to_thread(_download)
        logger.info("r2.object_downloaded", key=key)

    async def upload_from_path(self, *, key: str, source_path: str, content_type: str) -> R2Object:
        def _upload() -> None:
            self._client.upload_file(
                source_path,
                self._bucket,
                key,
                ExtraArgs={"ContentType": content_type},
            )

        await asyncio.to_thread(_upload)
        logger.info("r2.object_uploaded", key=key)
        head = await self.head_object(key=key)
        if head is None:  # pragma: no cover - defensive, upload just succeeded
            raise RuntimeError(f"Uploaded object {key} not found after upload")
        return head

    async def delete_object(self, *, key: str) -> None:
        def _delete() -> None:
            self._client.delete_object(Bucket=self._bucket, Key=key)

        await asyncio.to_thread(_delete)
        logger.info("r2.object_deleted", key=key)
