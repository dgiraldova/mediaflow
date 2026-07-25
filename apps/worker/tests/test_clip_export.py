"""Tests for clip-export activities.

FFmpeg itself is exercised by ``worker.media.ffmpeg`` against real binaries in
the worker container; here the concern is the activity logic around it —
range validation, storage keys, status reporting and cleanup.
"""

from __future__ import annotations

import os

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from worker.activities.deps import ActivityDependencies
from worker.activities.export import ExportActivities
from worker.activities.types import (
    MarkClipExportFailedInput,
    MarkClipExportReadyInput,
    RenderClipInput,
)
from worker.config import WorkerSettings
from worker.domain.models import ClipExportRequest
from worker.media.ffmpeg import ClipExtractionResult
from worker.repositories.memory import InMemoryClipExportRepository
from worker.storage.r2_client import R2Object

ORG = "demo-org"
ASSET = "asset-1"
EXPORT = "export-1"
PROXY_KEY = f"orgs/{ORG}/assets/{ASSET}/proxy/proxy.mp4"


class FakeStorage:
    def __init__(self, *, existing_keys: set[str] | None = None) -> None:
        self.existing = existing_keys if existing_keys is not None else {PROXY_KEY}
        self.uploaded: dict[str, str] = {}
        self.downloaded: list[str] = []

    async def head_object(self, *, key: str) -> R2Object | None:
        if key not in self.existing:
            return None
        return R2Object(key=key, byte_size=2048, etag="etag", content_type="video/mp4")

    async def download_to_path(self, *, key: str, destination_path: str) -> None:
        self.downloaded.append(key)
        os.makedirs(os.path.dirname(destination_path), exist_ok=True)
        with open(destination_path, "wb") as handle:
            handle.write(b"fake source video")

    async def upload_from_path(self, *, key: str, source_path: str, content_type: str) -> R2Object:
        self.uploaded[key] = source_path
        self.existing.add(key)
        return R2Object(key=key, byte_size=1024, etag="etag", content_type=content_type)

    async def delete_object(self, *, key: str) -> None:
        self.existing.discard(key)


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def clip_repo() -> InMemoryClipExportRepository:
    return InMemoryClipExportRepository()


@pytest.fixture
def activities(tmp_path, storage, clip_repo) -> ExportActivities:
    settings = WorkerSettings(temp_dir=str(tmp_path))
    deps = ActivityDependencies(
        settings=settings,
        storage=storage,
        asset_repository=None,
        processing_job_repository=None,
        transcript_repository=None,
        moment_repository=None,
        transcription_provider=None,
        video_intelligence_provider=None,
        classification_provider=None,
        embedding_provider=None,
        clip_export_repository=clip_repo,
    )
    return ExportActivities(deps)


def render_input(*, start_ms: int = 5_000, end_ms: int = 35_000) -> RenderClipInput:
    return RenderClipInput(
        organization_id=ORG,
        clip_export_id=EXPORT,
        asset_id=ASSET,
        start_ms=start_ms,
        end_ms=end_ms,
        source_storage_key=PROXY_KEY,
    )


@pytest.mark.asyncio
async def test_valid_range_passes_validation(activities):
    await activities.validate_clip_range(render_input())


@pytest.mark.asyncio
async def test_inverted_range_is_rejected_without_retry(activities):
    with pytest.raises(ApplicationError) as exc:
        await activities.validate_clip_range(render_input(start_ms=30_000, end_ms=10_000))
    assert exc.value.non_retryable
    assert exc.value.type == "invalid_clip_range"


@pytest.mark.asyncio
async def test_clip_longer_than_five_minutes_is_rejected(activities):
    with pytest.raises(ApplicationError) as exc:
        await activities.validate_clip_range(render_input(start_ms=0, end_ms=5 * 60_000 + 1))
    assert exc.value.non_retryable
    assert exc.value.type == "clip_too_long"


@pytest.mark.asyncio
async def test_render_uploads_to_the_clip_key_and_reports_size(activities, storage, monkeypatch):
    async def fake_extract(*, input_path, output_path, start_ms, end_ms):
        with open(output_path, "wb") as handle:
            handle.write(b"rendered clip")
        return ClipExtractionResult(output_path=output_path, duration_ms=end_ms - start_ms)

    monkeypatch.setattr("worker.activities.export.extract_clip", fake_extract)

    # ActivityEnvironment supplies the activity context that heartbeats need,
    # without requiring a Temporal server.
    heartbeats: list = []
    env = ActivityEnvironment()
    env.on_heartbeat = lambda *args: heartbeats.append(args)
    result = await env.run(activities.render_clip, render_input())

    assert result.output_storage_key == f"orgs/{ORG}/clips/{EXPORT}/clip.mp4"
    assert result.duration_ms == 30_000
    assert result.byte_size == 1024
    # The clip is rendered from the proxy, not the original.
    assert storage.downloaded == [PROXY_KEY]
    assert result.output_storage_key in storage.uploaded
    # Long renders must heartbeat so Temporal does not consider them stalled.
    assert heartbeats


@pytest.mark.asyncio
async def test_missing_source_fails_without_retry(activities, storage):
    storage.existing.clear()
    env = ActivityEnvironment()
    with pytest.raises(ApplicationError) as exc:
        await env.run(activities.render_clip, render_input())
    assert exc.value.non_retryable
    assert exc.value.type == "source_missing"


@pytest.mark.asyncio
async def test_ready_and_failed_statuses_reach_the_repository(activities, clip_repo):
    await clip_repo.seed(
        ClipExportRequest(
            id=EXPORT, organization_id=ORG, asset_id=ASSET, start_ms=5_000, end_ms=35_000
        )
    )

    await activities.mark_clip_export_ready(
        MarkClipExportReadyInput(
            organization_id=ORG,
            clip_export_id=EXPORT,
            output_storage_key=f"orgs/{ORG}/clips/{EXPORT}/clip.mp4",
            output_mime_type="video/mp4",
        )
    )
    assert clip_repo.status_of(EXPORT) == "ready"

    await activities.mark_clip_export_failed(
        MarkClipExportFailedInput(
            organization_id=ORG, clip_export_id=EXPORT, error_message="ffmpeg exploded"
        )
    )
    assert clip_repo.status_of(EXPORT) == "failed"


@pytest.mark.asyncio
async def test_status_reporting_degrades_gracefully_without_a_repository(tmp_path, storage):
    """Member B has no clip_exports table yet; rendering must still work."""
    deps = ActivityDependencies(
        settings=WorkerSettings(temp_dir=str(tmp_path)),
        storage=storage,
        asset_repository=None,
        processing_job_repository=None,
        transcript_repository=None,
        moment_repository=None,
        transcription_provider=None,
        video_intelligence_provider=None,
        classification_provider=None,
        embedding_provider=None,
        clip_export_repository=None,
    )
    activities = ExportActivities(deps)

    # Must not raise — the export is logged instead of persisted.
    await activities.mark_clip_export_ready(
        MarkClipExportReadyInput(
            organization_id=ORG,
            clip_export_id=EXPORT,
            output_storage_key="key",
            output_mime_type="video/mp4",
        )
    )


@pytest.mark.asyncio
async def test_cleanup_removes_the_export_temp_directory(activities, tmp_path):
    export_dir = tmp_path / "exports" / EXPORT
    export_dir.mkdir(parents=True)
    (export_dir / "clip.mp4").write_bytes(b"data")

    await activities.cleanup_export_files(EXPORT)

    assert not (export_dir / "clip.mp4").exists()
