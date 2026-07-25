"""Tests for the local media-delivery server.

Member B's API points ``MEDIAFLOW_MEDIA_BASE_URL`` at this server, so the
contract that matters is: a proxy key reported by the worker must be
streamable, seekable, and originals must never be served.
"""

from __future__ import annotations

import httpx
import pytest

from worker.config import WorkerSettings
from worker.media_server import create_app
from worker.storage.r2_client import R2Object

ORG = "demo-org"
ASSET = "asset-1"
PROXY_KEY = f"orgs/{ORG}/assets/{ASSET}/proxy/proxy.mp4"
THUMB_KEY = f"orgs/{ORG}/assets/{ASSET}/thumbnails/main.jpg"
ORIGINAL_KEY = f"orgs/{ORG}/assets/{ASSET}/original/secret.mp4"

CONTENT = bytes(range(256)) * 8  # 2048 deterministic bytes


class FakeStorage:
    """Stands in for R2/MinIO so these tests need no running bucket."""

    def __init__(self, objects: dict[str, bytes]) -> None:
        self._objects = objects

    async def head_object(self, *, key: str) -> R2Object | None:
        data = self._objects.get(key)
        if data is None:
            return None
        return R2Object(key=key, byte_size=len(data), etag="etag", content_type=None)

    async def read_object(self, *, key: str, byte_range: tuple[int, int] | None = None) -> bytes:
        data = self._objects[key]
        if byte_range is None:
            return data
        start, end = byte_range
        return data[start : end + 1]


@pytest.fixture
def client():
    storage = FakeStorage({PROXY_KEY: CONTENT, THUMB_KEY: b"jpegdata", ORIGINAL_KEY: b"original"})
    app = create_app(settings=WorkerSettings(), storage=storage)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://media")


@pytest.mark.asyncio
async def test_health_reports_the_configured_bucket(client):
    async with client as c:
        response = await c.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_proxy_is_served_whole_when_no_range_is_requested(client):
    async with client as c:
        response = await c.get(f"/{PROXY_KEY}")
    assert response.status_code == 200
    assert response.content == CONTENT
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["accept-ranges"] == "bytes"


@pytest.mark.asyncio
async def test_range_request_returns_partial_content_for_seeking(client):
    async with client as c:
        response = await c.get(f"/{PROXY_KEY}", headers={"Range": "bytes=100-199"})
    assert response.status_code == 206
    assert response.content == CONTENT[100:200]
    assert response.headers["content-range"] == f"bytes 100-199/{len(CONTENT)}"


@pytest.mark.asyncio
async def test_open_ended_range_is_clamped_to_the_object_size(client):
    async with client as c:
        response = await c.get(f"/{PROXY_KEY}", headers={"Range": "bytes=2000-"})
    assert response.status_code == 206
    assert response.content == CONTENT[2000:]
    assert response.headers["content-range"] == f"bytes 2000-{len(CONTENT) - 1}/{len(CONTENT)}"


@pytest.mark.asyncio
async def test_thumbnails_are_served(client):
    async with client as c:
        response = await c.get(f"/{THUMB_KEY}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"


@pytest.mark.asyncio
async def test_original_media_is_never_served(client):
    """Originals stay private even though the object exists in the bucket."""
    async with client as c:
        response = await c.get(f"/{ORIGINAL_KEY}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_unknown_key_is_not_found(client):
    async with client as c:
        response = await c.get(f"/orgs/{ORG}/assets/{ASSET}/proxy/missing.mp4")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_head_reports_size_without_a_body(client):
    async with client as c:
        response = await c.head(f"/{PROXY_KEY}")
    assert response.status_code == 200
    assert response.headers["content-length"] == str(len(CONTENT))
    assert response.content == b""
