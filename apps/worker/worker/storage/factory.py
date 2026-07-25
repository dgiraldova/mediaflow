"""Select the configured media storage implementation."""

from __future__ import annotations

from worker.config import WorkerSettings
from worker.storage.interfaces import StorageClient
from worker.storage.local import LocalStorage
from worker.storage.r2_client import R2Client


def build_storage(settings: WorkerSettings) -> StorageClient:
    if settings.storage_backend == "local":
        return LocalStorage(
            root=settings.local_storage_path,
            public_base_url=settings.local_media_base_url,
        )
    return R2Client(
        bucket=settings.storage.bucket,
        endpoint_url=settings.storage.resolved_endpoint_url,
        access_key_id=settings.storage.access_key_id,
        secret_access_key=settings.storage.secret_access_key,
        region=settings.storage.region,
        signed_url_ttl_seconds=settings.storage.signed_url_ttl_seconds,
        presigned_upload_ttl_seconds=settings.storage.presigned_upload_ttl_seconds,
    )
