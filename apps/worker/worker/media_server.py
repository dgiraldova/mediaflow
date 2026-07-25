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

from worker.config import WorkerSettings, get_settings
from worker.logging import configure_logging, get_logger
from worker.storage.r2_client import R2Client

logger = get_logger(__name__)

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


def create_app(settings: WorkerSettings | None = None, storage: R2Client | None = None) -> FastAPI:
    settings = settings or get_settings()
    storage = storage or R2Client(
        bucket=settings.storage.bucket,
        endpoint_url=settings.storage.resolved_endpoint_url,
        access_key_id=settings.storage.access_key_id,
        secret_access_key=settings.storage.secret_access_key,
        region=settings.storage.region,
    )

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
        return {"status": "ok", "bucket": settings.storage.bucket}

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


async def _lookup(storage: R2Client, key: str):
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
    logger.info("media_server.starting", bucket=settings.storage.bucket)
    uvicorn.run(create_app(settings), host="127.0.0.1", port=8001)


if __name__ == "__main__":
    main()
