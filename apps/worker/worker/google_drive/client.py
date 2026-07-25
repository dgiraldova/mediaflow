"""Google Drive API client (spec MVP1-062, MVP1-063).

Covers folder discovery, media listing and download. Uses the REST API over
httpx rather than the google-api-python-client, because that library is
synchronous and would block the worker's event loop during multi-GB downloads.

Only supported video formats are listed (spec 12.1), so unsupported files in a
customer's folder are ignored rather than imported and then failed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

from worker.logging import get_logger
from worker.media.validation import SUPPORTED_VIDEO_MIME_TYPES

logger = get_logger(__name__)

DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"

PAGE_SIZE = 100
DOWNLOAD_CHUNK_BYTES = 8 * 1024 * 1024

# Fields worth requesting; Drive returns a minimal projection otherwise.
FILE_FIELDS = "id,name,mimeType,size,md5Checksum,modifiedTime,createdTime,videoMediaMetadata"


@dataclass(frozen=True)
class DriveFolder:
    id: str
    name: str


@dataclass(frozen=True)
class DriveFile:
    id: str
    name: str
    mime_type: str
    size_bytes: int
    modified_time: str
    md5_checksum: str | None = None
    duration_ms: int | None = None


class DriveApiError(RuntimeError):
    pass


class DriveClient:
    def __init__(self, *, access_token: str, http_client: httpx.AsyncClient | None = None) -> None:
        self._http = http_client or httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=300.0))
        self._headers = {"Authorization": f"Bearer {access_token}"}

    async def list_folders(self, *, parent_id: str = "root") -> list[DriveFolder]:
        """List sub-folders so the UI can render a folder picker."""
        query = f"'{parent_id}' in parents and mimeType = '{FOLDER_MIME_TYPE}' and trashed = false"
        folders: list[DriveFolder] = []
        async for item in self._paginate(query=query, fields="id,name"):
            folders.append(DriveFolder(id=item["id"], name=item["name"]))
        return folders

    async def list_media_files(
        self, *, folder_id: str, modified_after: str | None = None
    ) -> list[DriveFile]:
        """List supported media in a folder.

        ``modified_after`` (an RFC 3339 timestamp) makes the sync incremental:
        Drive filters server-side, so a scheduled sync over a large folder does
        not re-list every file each run.
        """
        mime_filter = " or ".join(
            f"mimeType = '{mime}'" for mime in sorted(SUPPORTED_VIDEO_MIME_TYPES)
        )
        query = f"'{folder_id}' in parents and trashed = false and ({mime_filter})"
        if modified_after:
            query += f" and modifiedTime > '{modified_after}'"

        files: list[DriveFile] = []
        async for item in self._paginate(query=query, fields=FILE_FIELDS):
            files.append(_to_drive_file(item))

        logger.info(
            "google_drive.listed_media",
            folder_id=folder_id,
            count=len(files),
            incremental=bool(modified_after),
        )
        return files

    async def download_to_path(self, *, file_id: str, destination_path: str) -> int:
        """Stream a file to local disk, returning the byte count.

        Streamed in chunks so a multi-GB source never has to fit in memory.
        """
        written = 0
        async with self._http.stream(
            "GET",
            f"{DRIVE_API_BASE}/files/{file_id}",
            params={"alt": "media"},
            headers=self._headers,
        ) as response:
            if response.status_code >= 400:
                await response.aread()
                raise DriveApiError(
                    f"Drive download failed for {file_id}: HTTP {response.status_code}"
                )
            with open(destination_path, "wb") as handle:
                async for chunk in response.aiter_bytes(DOWNLOAD_CHUNK_BYTES):
                    handle.write(chunk)
                    written += len(chunk)

        logger.info("google_drive.downloaded", file_id=file_id, byte_size=written)
        return written

    async def _paginate(self, *, query: str, fields: str) -> AsyncIterator[dict]:
        page_token: str | None = None
        while True:
            params = {
                "q": query,
                "pageSize": PAGE_SIZE,
                "fields": f"nextPageToken,files({fields})",
                # Required for files in shared drives to appear at all.
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if page_token:
                params["pageToken"] = page_token

            response = await self._http.get(
                f"{DRIVE_API_BASE}/files", params=params, headers=self._headers
            )
            if response.status_code >= 400:
                raise DriveApiError(
                    f"Drive list failed: HTTP {response.status_code} {response.text[:200]}"
                )

            payload = response.json()
            for item in payload.get("files", []):
                yield item

            page_token = payload.get("nextPageToken")
            if not page_token:
                return

    async def aclose(self) -> None:
        await self._http.aclose()


def _to_drive_file(item: dict) -> DriveFile:
    metadata = item.get("videoMediaMetadata") or {}
    duration = metadata.get("durationMillis")
    return DriveFile(
        id=item["id"],
        name=item["name"],
        mime_type=item["mimeType"],
        size_bytes=int(item.get("size", 0)),
        modified_time=item.get("modifiedTime", ""),
        md5_checksum=item.get("md5Checksum"),
        duration_ms=int(duration) if duration is not None else None,
    )
