"""Local media-delivery server (Member C).

Member B's API builds playback URLs as ``MEDIAFLOW_MEDIA_BASE_URL/{proxy_key}``
and returns them from ``GET /api/v1/assets/{id}/playback-url``. In production
that base URL points at a CDN in front of R2. For local development this
server stands in for it, streaming objects out of the configured bucket
(MinIO locally, R2 otherwise).

It serves derivatives only — proxies and thumbnails — never original media,
and it supports HTTP Range requests so the browser can seek in the player
without downloading the whole file.

Run it with:

    python -m worker.media_server
"""

from __future__ import annotations

import re

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from worker.config import WorkerSettings, get_settings
from worker.logging import configure_logging, get_logger
from worker.storage.factory import build_storage
from worker.storage.interfaces import StorageClient, StorageObject

logger = get_logger(__name__)


class PresignRequest(BaseModel):
    upload_key: str = Field(min_length=1, max_length=500)
    content_type: str = Field(default="video/mp4", max_length=120)


class PresignResponse(BaseModel):
    url: str
    method: str
    headers: dict[str, str]
    expires_in: int


def _require_internal_token(request: Request, settings: WorkerSettings) -> None:
    expected = settings.api.internal_token
    if not expected:
        raise HTTPException(status_code=503, detail="Internal token is not configured")
    if request.headers.get("x-internal-token") != expected:
        raise HTTPException(status_code=401, detail="Invalid internal worker token")


def _reject_unsafe_key(key: str) -> None:
    """Keys are attacker-influenced (they embed a user-supplied filename)."""
    if key.startswith("/") or ".." in key.split("/"):
        raise HTTPException(status_code=422, detail="Storage key must be a relative object key")

# Only derivative objects are publicly streamable. Original uploads stay
# private and are reachable exclusively through short-lived signed URLs.
SERVABLE_KEY_PATTERN = re.compile(r"^orgs/[^/]+/(assets/[^/]+/(proxy|thumbnails)|clips/[^/]+)/.+$")

CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webm": "video/webm",
}

RANGE_HEADER = re.compile(r"bytes=(\d*)-(\d*)")


DEFAULT_CONTENT_TYPE = "application/octet-stream"


def content_type_for(key: str) -> str:
    dot = key.rfind(".")
    if dot == -1:
        return DEFAULT_CONTENT_TYPE
    return CONTENT_TYPES.get(key[dot:].lower(), DEFAULT_CONTENT_TYPE)


def create_app(
    settings: WorkerSettings | None = None,
    storage: StorageClient | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    storage = storage or build_storage(settings)

    app = FastAPI(title="MediaFlow media delivery", version="0.1.0")

    # The player runs on the frontend's origin, so it needs cross-origin reads.
    # Range must be exposed for seeking to work.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "HEAD"],
        allow_headers=["Range"],
        expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"],
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "storage": settings.storage_backend}

    @app.post("/uploads/presign")
    async def presign_upload(payload: PresignRequest, request: Request) -> PresignResponse:
        """Issue a presigned PUT URL so the browser uploads straight to storage.

        This is the production-shaped flow: the original media never passes
        through an application server (spec 11.1). Member B's
        ``/uploads/initiate`` should call this server-to-server with the
        internal token and hand the URL to the browser.
        """
        _require_internal_token(request, settings)
        _reject_unsafe_key(payload.upload_key)
        url = storage.generate_presigned_put_url(
            key=payload.upload_key, content_type=payload.content_type
        )
        return PresignResponse(
            url=url,
            method="PUT",
            headers={"Content-Type": payload.content_type},
            expires_in=settings.storage.presigned_upload_ttl_seconds,
        )

    @app.put("/uploads/{upload_key:path}")
    async def upload_object(upload_key: str, request: Request) -> dict[str, object]:
        """Accept an upload and relay it into storage.

        A local-development convenience: it avoids configuring CORS on MinIO
        and works when the bucket is not reachable from the browser. Bytes do
        pass through this process, so production should use the presigned URL
        from ``/uploads/presign`` instead.
        """
        _reject_unsafe_key(upload_key)
        body = await request.body()
        if not body:
            raise HTTPException(status_code=400, detail="Empty upload")

        await storage.put_object(
            key=upload_key,
            body=body,
            content_type=request.headers.get("content-type") or content_type_for(upload_key),
        )
        logger.info("media_server.upload_stored", key=upload_key, byte_size=len(body))
        return {"upload_key": upload_key, "byte_size": len(body)}

    @app.head("/{key:path}")
    async def head_object(key: str) -> Response:
        obj = await _lookup(storage, key)
        return Response(
            status_code=200,
            headers={
                "Content-Length": str(obj.byte_size),
                "Content-Type": content_type_for(key),
                "Accept-Ranges": "bytes",
            },
        )

    @app.get("/{key:path}")
    async def get_object(key: str, request: Request) -> Response:
        obj = await _lookup(storage, key)
        range_header = request.headers.get("range")

        if not range_header:
            body = await storage.read_object(key=key)
            return Response(
                content=body,
                media_type=content_type_for(key),
                headers={"Accept-Ranges": "bytes", "Content-Length": str(obj.byte_size)},
            )

        match = RANGE_HEADER.match(range_header)
        if not match:
            raise HTTPException(status_code=416, detail="Malformed Range header")

        start_raw, end_raw = match.groups()
        start = int(start_raw) if start_raw else 0
        end = int(end_raw) if end_raw else obj.byte_size - 1
        end = min(end, obj.byte_size - 1)
        if start > end:
            raise HTTPException(status_code=416, detail="Requested range is not satisfiable")

        chunk = await storage.read_object(key=key, byte_range=(start, end))
        return StreamingResponse(
            iter([chunk]),
            status_code=206,
            media_type=content_type_for(key),
            headers={
                "Content-Range": f"bytes {start}-{end}/{obj.byte_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(end - start + 1),
            },
        )

    return app


async def _lookup(storage: StorageClient, key: str) -> StorageObject:
    if not SERVABLE_KEY_PATTERN.match(key):
        # Do not reveal whether a non-servable key exists.
        raise HTTPException(status_code=404, detail="Not found")
    obj = await storage.head_object(key=key)
    if obj is None:
        raise HTTPException(status_code=404, detail="Not found")
    return obj


def main() -> None:
    import uvicorn

    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("media_server.starting", storage=settings.storage_backend)
    uvicorn.run(create_app(settings), host="127.0.0.1", port=8001)


if __name__ == "__main__":
    main()
