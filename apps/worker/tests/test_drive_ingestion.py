"""Tests for acquiring an ingestion source from Google Drive."""

from __future__ import annotations

import time

import httpx
import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from worker.activities.deps import ActivityDependencies
from worker.activities.ingestion import IngestionActivities
from worker.activities.types import AssetContext, IngestAssetWorkflowInput, SourceRef
from worker.config import WorkerSettings
from worker.domain.enums import SourceProvider
from worker.domain.models import SourceConnection
from worker.google_drive.oauth import DriveCredentials, GoogleDriveOAuth
from worker.repositories.memory import InMemorySourceConnectionRepository

ORG = "demo-org"
ASSET = "asset-1"
JOB = "job-1"
CONNECTION = "connection-1"
DRIVE_FILE_ID = "drive-file-1"
FILE_CONTENT = b"pretend this is an mp4" * 100


def workflow_input() -> IngestAssetWorkflowInput:
    return IngestAssetWorkflowInput(
        context=AssetContext(organization_id=ORG, asset_id=ASSET, processing_job_id=JOB),
        source=SourceRef(
            kind="google_drive",
            source_connection_id=CONNECTION,
            source_external_id=DRIVE_FILE_ID,
        ),
        original_filename="from_drive.mp4",
        mime_type="video/mp4",
        byte_size=len(FILE_CONTENT),
        asset_title="Drive import",
        analysis_version="test-1",
    )


async def seeded_connections(credentials: dict) -> InMemorySourceConnectionRepository:
    repo = InMemorySourceConnectionRepository()
    await repo.seed(
        SourceConnection(
            id=CONNECTION,
            organization_id=ORG,
            provider=SourceProvider.GOOGLE_DRIVE,
            display_name="Marketing footage",
            status="active",
        ),
        credentials,
    )
    return repo


def oauth_with(handler) -> GoogleDriveOAuth:
    return GoogleDriveOAuth(
        client_id="id",
        client_secret="secret",
        redirect_uri="http://localhost/callback",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def build_activities(tmp_path, *, connections, oauth) -> IngestionActivities:
    deps = ActivityDependencies(
        settings=WorkerSettings(temp_dir=str(tmp_path)),
        storage=None,
        asset_repository=None,
        processing_job_repository=None,
        transcript_repository=None,
        moment_repository=None,
        transcription_provider=None,
        video_intelligence_provider=None,
        classification_provider=None,
        embedding_provider=None,
        source_connection_repository=connections,
        google_drive_oauth=oauth,
    )
    return IngestionActivities(deps)


@pytest.mark.asyncio
async def test_drive_file_is_downloaded_to_local_disk(tmp_path, monkeypatch):
    connections = await seeded_connections(
        DriveCredentials(
            refresh_token="refresh-1",
            access_token="valid-token",
            expires_at_epoch=time.time() + 3600,
        ).to_storage()
    )

    downloaded: dict[str, str] = {}

    class FakeDriveClient:
        def __init__(self, *, access_token: str) -> None:
            downloaded["token"] = access_token

        async def download_to_path(self, *, file_id: str, destination_path: str) -> int:
            downloaded["file_id"] = file_id
            with open(destination_path, "wb") as handle:
                handle.write(FILE_CONTENT)
            return len(FILE_CONTENT)

        async def aclose(self) -> None:
            downloaded["closed"] = "yes"

    monkeypatch.setattr("worker.activities.ingestion.DriveClient", FakeDriveClient)

    activities = build_activities(
        tmp_path, connections=connections, oauth=oauth_with(lambda r: httpx.Response(200))
    )
    result = await ActivityEnvironment().run(activities.acquire_source_file, workflow_input())

    assert result.byte_size == len(FILE_CONTENT)
    assert downloaded["file_id"] == DRIVE_FILE_ID
    assert downloaded["token"] == "valid-token"
    assert downloaded["closed"] == "yes"


@pytest.mark.asyncio
async def test_expired_credentials_are_refreshed_and_restored(tmp_path, monkeypatch):
    connections = await seeded_connections(
        DriveCredentials(
            refresh_token="refresh-1",
            access_token="expired-token",
            expires_at_epoch=time.time() - 10,
        ).to_storage()
    )

    used_tokens: list[str] = []

    class FakeDriveClient:
        def __init__(self, *, access_token: str) -> None:
            used_tokens.append(access_token)

        async def download_to_path(self, *, file_id: str, destination_path: str) -> int:
            with open(destination_path, "wb") as handle:
                handle.write(FILE_CONTENT)
            return len(FILE_CONTENT)

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr("worker.activities.ingestion.DriveClient", FakeDriveClient)

    def token_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "fresh-token", "expires_in": 3600})

    activities = build_activities(
        tmp_path, connections=connections, oauth=oauth_with(token_handler)
    )
    await ActivityEnvironment().run(activities.acquire_source_file, workflow_input())

    assert used_tokens == ["fresh-token"]

    # The refreshed token must be written back, or every sync re-refreshes.
    stored = await connections.get_encrypted_credentials(
        organization_id=ORG, connection_id=CONNECTION
    )
    assert stored["access_token"] == "fresh-token"
    assert stored["refresh_token"] == "refresh-1"


@pytest.mark.asyncio
async def test_revoked_access_marks_the_connection_and_does_not_retry(tmp_path):
    connections = await seeded_connections(
        DriveCredentials(
            refresh_token="revoked",
            access_token="old",
            expires_at_epoch=time.time() - 10,
        ).to_storage()
    )

    def revoked_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    activities = build_activities(
        tmp_path, connections=connections, oauth=oauth_with(revoked_handler)
    )

    with pytest.raises(ApplicationError) as exc:
        await ActivityEnvironment().run(activities.acquire_source_file, workflow_input())

    assert exc.value.non_retryable
    assert exc.value.type == "drive_reauthorization_required"

    # The connection is flagged so the UI can prompt the user to reconnect.
    connection = await connections.get(organization_id=ORG, connection_id=CONNECTION)
    assert connection.status == "error"


@pytest.mark.asyncio
async def test_missing_drive_configuration_fails_clearly(tmp_path):
    activities = build_activities(tmp_path, connections=None, oauth=None)
    with pytest.raises(ApplicationError) as exc:
        await ActivityEnvironment().run(activities.acquire_source_file, workflow_input())
    assert exc.value.type == "drive_not_configured"
    assert exc.value.non_retryable


@pytest.mark.asyncio
async def test_source_without_a_drive_file_id_is_rejected(tmp_path):
    connections = await seeded_connections(
        DriveCredentials(
            refresh_token="r", access_token="a", expires_at_epoch=time.time() + 3600
        ).to_storage()
    )
    activities = build_activities(
        tmp_path, connections=connections, oauth=oauth_with(lambda r: httpx.Response(200))
    )

    incomplete = workflow_input()
    incomplete.source = SourceRef(kind="google_drive", source_connection_id=CONNECTION)

    with pytest.raises(ApplicationError) as exc:
        await ActivityEnvironment().run(activities.acquire_source_file, incomplete)
    assert exc.value.type == "invalid_source"
