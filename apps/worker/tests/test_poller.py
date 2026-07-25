"""Poller integration tests against Member B's real API.

Covers the handoff Member B was waiting on: claiming queued work, reporting
checksum and provider id, and surfacing duplicate uploads.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.main import create_app  # noqa: E402

from worker.config import WorkerSettings  # noqa: E402
from worker.poller import IngestionPoller  # noqa: E402
from worker.providers.classification import NullClassificationProvider  # noqa: E402
from worker.repositories.http_api import (  # noqa: E402
    ApiClient,
    HttpAssetRepository,
    HttpMomentRepository,
    HttpProcessingJobRepository,
    HttpTranscriptRepository,
    _ProcessingState,
)

TOKEN = "test-internal-token"
ORG = "demo-org"
USER = {"X-User-Id": "demo-user"}


@pytest.fixture
def api_app(tmp_path):
    return create_app(
        database_url=f"sqlite:///{tmp_path / 'poller.db'}",
        internal_worker_token=TOKEN,
        jwt_secret="test-secret",
        media_base_url="http://127.0.0.1:8001",
        media_storage_path=str(tmp_path / "media"),
    )


@pytest.fixture
async def clients(api_app):
    transport = httpx.ASGITransport(app=api_app)
    async with api_app.router.lifespan_context(api_app):
        async with (
            httpx.AsyncClient(
                transport=transport,
                base_url="http://api",
                headers={"X-Internal-Token": TOKEN},
            ) as worker_client,
            httpx.AsyncClient(transport=transport, base_url="http://api", headers=USER) as user,
        ):
            yield worker_client, user


@pytest.fixture
def poller(clients, tmp_path):
    worker_client, _ = clients
    api = ApiClient(worker_client)
    state = _ProcessingState()
    return IngestionPoller(
        settings=WorkerSettings(temp_dir=str(tmp_path / "work")),
        api=api,
        assets=HttpAssetRepository(api, state),
        jobs=HttpProcessingJobRepository(api, state),
        transcripts=HttpTranscriptRepository(api),
        moments=HttpMomentRepository(api),
        providers={"classification": NullClassificationProvider()},
        simulate=True,
        worker_id="test-worker",
    )


async def upload(user: httpx.AsyncClient, filename: str = "interview.mp4") -> str:
    initiated = (
        await user.post(
            "/api/v1/uploads/initiate",
            json={
                "organization_id": ORG,
                "original_filename": filename,
                "media_type": "video",
            },
        )
    ).json()
    content = filename.encode()
    stored = await user.put(
        f"/api/v1/uploads/{initiated['upload_id']}/content",
        content=content,
        headers={"Content-Type": "video/mp4"},
    )
    stored.raise_for_status()
    await user.post(
        f"/api/v1/uploads/{initiated['upload_id']}/complete", json={"byte_size": len(content)}
    )
    return initiated["asset_id"]


@pytest.mark.asyncio
async def test_queued_upload_is_claimed_and_becomes_ready(clients, poller):
    _, user = clients
    asset_id = await upload(user)

    processed = await poller.drain_once()

    assert processed == 1
    asset = (await user.get(f"/api/v1/assets/{asset_id}")).json()
    assert asset["status"] == "ready"
    assert asset["duration_ms"] == 71_000
    assert asset["checksum_sha256"] is not None


@pytest.mark.asyncio
async def test_processing_produces_transcript_moments_and_playback(clients, poller):
    _, user = clients
    asset_id = await upload(user)
    await poller.drain_once()

    transcript = (await user.get(f"/api/v1/assets/{asset_id}/transcript")).json()
    moments = (await user.get(f"/api/v1/assets/{asset_id}/moments")).json()
    playback = await user.get(f"/api/v1/assets/{asset_id}/playback-url")

    assert len(transcript) == 5
    assert len(moments) >= 1
    assert playback.status_code == 200


@pytest.mark.asyncio
async def test_processed_asset_is_searchable(clients, poller):
    _, user = clients
    asset_id = await upload(user)
    await poller.drain_once()

    results = (
        await user.post("/api/v1/search", json={"query": "implementation", "organization_id": ORG})
    ).json()
    assert [r for r in results["results"] if r["asset_id"] == asset_id]


@pytest.mark.asyncio
async def test_claiming_twice_does_not_reprocess(clients, poller):
    """The API flips claimed jobs to processing, so a second poll finds nothing."""
    _, user = clients
    await upload(user)

    assert await poller.drain_once() == 1
    assert await poller.drain_once() == 0


@pytest.mark.asyncio
async def test_empty_queue_is_a_no_op(poller):
    assert await poller.drain_once() == 0


@pytest.mark.asyncio
async def test_duplicate_upload_fails_with_a_user_visible_reason(clients, poller, monkeypatch):
    """Two files with the same content must not both become ready."""
    _, user = clients
    first = await upload(user, "original.mp4")
    second = await upload(user, "copy.mp4")

    # Force both assets to hash identically, as two uploads of one file would.
    monkeypatch.setattr(
        "worker.poller.hashlib.sha256", lambda data=b"": _FixedDigest()
    )

    await poller.drain_once(limit=5)

    first_asset = (await user.get(f"/api/v1/assets/{first}")).json()
    second_asset = (await user.get(f"/api/v1/assets/{second}")).json()
    statuses = {first_asset["status"], second_asset["status"]}

    assert statuses == {"ready", "failed"}
    failed = first_asset if first_asset["status"] == "failed" else second_asset
    assert "duplicate" in failed["error_message"].lower()


@pytest.mark.asyncio
async def test_unsupported_format_fails_cleanly(clients, poller):
    _, user = clients
    asset_id = await upload(user, "notes.mkv")

    await poller.drain_once()

    asset = (await user.get(f"/api/v1/assets/{asset_id}")).json()
    assert asset["status"] == "failed"
    assert "unsupported_media" in asset["error_message"]
    # A failed asset stays retryable.
    assert (await user.post(f"/api/v1/assets/{asset_id}/retry")).status_code == 200


class _FixedDigest:
    """sha256 stand-in that always yields the same digest."""

    def update(self, data: bytes) -> None:  # noqa: D102
        pass

    def hexdigest(self) -> str:  # noqa: D102
        return "a" * 64
