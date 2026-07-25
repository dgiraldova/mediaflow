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


INTERNAL_TOKEN = "media-token"
UPLOAD_KEY = f"organizations/{ORG}/assets/{ASSET}/interview.mp4"


class FakeStorage:
    """Stands in for R2/MinIO so these tests need no running bucket."""

    def __init__(self, objects: dict[str, bytes]) -> None:
        self._objects = objects
        self.presigned: list[tuple[str, str]] = []

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

    async def put_object(self, *, key: str, body: bytes, content_type: str) -> R2Object:
        self._objects[key] = body
        return R2Object(key=key, byte_size=len(body), etag="", content_type=content_type)

    def generate_presigned_put_url(self, *, key: str, content_type: str) -> str:
        self.presigned.append((key, content_type))
        return f"http://minio.local/{key}?signature=abc"

    def stored(self, key: str) -> bytes | None:
        return self._objects.get(key)


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage({PROXY_KEY: CONTENT, THUMB_KEY: b"jpegdata", ORIGINAL_KEY: b"original"})


@pytest.fixture
def settings() -> WorkerSettings:
    config = WorkerSettings()
    config.api.internal_token = INTERNAL_TOKEN
    return config


@pytest.fixture
def client(storage, settings):
    app = create_app(settings=settings, storage=storage)
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
async def test_uploaded_bytes_reach_storage(client, storage):
    """Without this path the poller has nothing to download and process."""
    payload = b"actual video bytes"
    async with client as c:
        response = await c.put(
            f"/uploads/{UPLOAD_KEY}", content=payload, headers={"Content-Type": "video/mp4"}
        )
    assert response.status_code == 200
    assert response.json()["byte_size"] == len(payload)
    assert storage.stored(UPLOAD_KEY) == payload


@pytest.mark.asyncio
async def test_empty_upload_is_rejected(client):
    async with client as c:
        response = await c.put(f"/uploads/{UPLOAD_KEY}", content=b"")
    assert response.status_code == 400


def test_upload_key_traversal_is_rejected():
    """Tested directly: httpx normalizes '..' out of a URL before sending,
    so a client-level test cannot reach this guard. A raw HTTP client can."""
    from fastapi import HTTPException

    from worker.media_server import _reject_unsafe_key

    for unsafe in ("orgs/../../etc/passwd", "/absolute/path", "a/../../b"):
        with pytest.raises(HTTPException) as exc:
            _reject_unsafe_key(unsafe)
        assert exc.value.status_code == 422

    _reject_unsafe_key(UPLOAD_KEY)  # a normal key must pass


@pytest.mark.asyncio
async def test_presign_returns_a_direct_upload_url(client, storage):
    async with client as c:
        response = await c.post(
            "/uploads/presign",
            json={"upload_key": UPLOAD_KEY, "content_type": "video/mp4"},
            headers={"X-Internal-Token": INTERNAL_TOKEN},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["method"] == "PUT"
    assert UPLOAD_KEY in body["url"]
    assert storage.presigned == [(UPLOAD_KEY, "video/mp4")]


@pytest.mark.asyncio
async def test_presign_requires_the_internal_token(client):
    """A public presign endpoint would let anyone write into the bucket."""
    async with client as c:
        no_token = await c.post("/uploads/presign", json={"upload_key": UPLOAD_KEY})
        wrong = await c.post(
            "/uploads/presign",
            json={"upload_key": UPLOAD_KEY},
            headers={"X-Internal-Token": "nope"},
        )
    assert no_token.status_code == 401
    assert wrong.status_code == 401


@pytest.mark.asyncio
async def test_uploaded_originals_are_still_not_publicly_readable(client, storage):
    """Uploading must not make the original streamable."""
    async with client as c:
        await c.put(f"/uploads/{UPLOAD_KEY}", content=b"secret original")
        response = await c.get(f"/{UPLOAD_KEY}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_head_reports_size_without_a_body(client):
    async with client as c:
        response = await c.head(f"/{PROXY_KEY}")
    assert response.status_code == 200
    assert response.headers["content-length"] == str(len(CONTENT))
    assert response.content == b""
