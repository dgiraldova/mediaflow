"""Integration tests against Member B's real FastAPI application.

These run ``app.main.create_app`` in-process over an ASGI transport, so they
exercise the actual endpoints, validation rules and database writes that the
worker will hit in production — no mocks of the API, and no network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

# Member B's app lives at the repository root, outside the worker package.
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.main import create_app  # noqa: E402

from worker.domain.enums import AssetStatus, MomentType, ProcessingStage  # noqa: E402
from worker.domain.models import MediaMomentDraft, TranscriptSegmentDraft  # noqa: E402
from worker.repositories.api_mapping import moment_wire_id  # noqa: E402
from worker.repositories.http_api import (  # noqa: E402
    ApiClient,
    HttpAssetRepository,
    HttpMomentRepository,
    HttpProcessingJobRepository,
    HttpTranscriptRepository,
    _ProcessingState,
)

INTERNAL_TOKEN = "test-internal-token"
ORG = "demo-org"
USER_HEADERS = {"X-User-Id": "demo-user"}
ANALYSIS_VERSION = "test-1"


@pytest.fixture
def api_app(tmp_path):
    return create_app(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        internal_worker_token=INTERNAL_TOKEN,
        jwt_secret="test-secret",
        media_base_url="http://127.0.0.1:8001",
    )


@pytest.fixture
async def clients(api_app):
    """A worker-token client and an end-user client against the same app.

    The lifespan context is entered explicitly because httpx's ASGITransport
    does not fire startup events, and Member B's app creates its tables and
    seeds the demo organization there.
    """
    transport = httpx.ASGITransport(app=api_app)
    async with api_app.router.lifespan_context(api_app):
        async with (
            httpx.AsyncClient(
                transport=transport,
                base_url="http://api",
                headers={"X-Internal-Token": INTERNAL_TOKEN},
            ) as worker_client,
            httpx.AsyncClient(
                transport=transport, base_url="http://api", headers=USER_HEADERS
            ) as user_client,
        ):
            yield worker_client, user_client


@pytest.fixture
def repos(clients):
    worker_client, _ = clients
    api = ApiClient(worker_client)
    state = _ProcessingState()
    return {
        "api": api,
        "asset": HttpAssetRepository(api, state),
        "job": HttpProcessingJobRepository(api, state),
        "transcript": HttpTranscriptRepository(api),
        "moment": HttpMomentRepository(api),
    }


async def create_asset(user_client: httpx.AsyncClient) -> str:
    """Run the upload flow the browser performs, returning the asset id."""
    initiated = await user_client.post(
        "/api/v1/uploads/initiate",
        json={
            "organization_id": ORG,
            "original_filename": "founder_interview.mp4",
            "media_type": "video",
        },
    )
    initiated.raise_for_status()
    body = initiated.json()
    completed = await user_client.post(
        f"/api/v1/uploads/{body['upload_id']}/complete", json={"byte_size": 5_242_880}
    )
    completed.raise_for_status()
    return body["asset_id"]


def make_segments(asset_id: str) -> list[TranscriptSegmentDraft]:
    raw = [
        (0, 6_000, "Tell me about the rollout.", "interviewer"),
        (6_200, 18_000, "Implementation was easier than expected.", "customer"),
        (18_500, 30_000, "We were live in under a week.", "customer"),
    ]
    return [
        TranscriptSegmentDraft(
            asset_id=asset_id,
            organization_id=ORG,
            sequence_number=i,
            start_ms=start,
            end_ms=end,
            text_original=text,
            text_normalized=text.lower(),
            provider="test",
            analysis_version=ANALYSIS_VERSION,
            speaker_label=speaker,
        )
        for i, (start, end, text, speaker) in enumerate(raw)
    ]


def make_moments(asset_id: str) -> list[MediaMomentDraft]:
    return [
        MediaMomentDraft(
            asset_id=asset_id,
            organization_id=ORG,
            sequence_number=0,
            start_ms=6_200,
            end_ms=18_000,
            moment_type=MomentType.SPEECH_SEGMENT,
            title="Implementation was easy",
            visual_description="Customer speaking to camera.",
            marketing_description="Customer says onboarding beat expectations.",
            analysis_version=ANALYSIS_VERSION,
            transcript_text="Implementation was easier than expected.",
            content_types=["testimonial"],
            funnel_stages=["consideration"],
            technical_quality_score=0.82,
        ),
        MediaMomentDraft(
            asset_id=asset_id,
            organization_id=ORG,
            sequence_number=1,
            start_ms=18_500,
            end_ms=30_000,
            moment_type=MomentType.SPEECH_SEGMENT,
            title="Live in under a week",
            visual_description="Customer gesturing.",
            marketing_description="Fast time to value.",
            analysis_version=ANALYSIS_VERSION,
            transcript_text="We were live in under a week.",
            content_types=["social_proof"],
            technical_quality_score=0.9,
        ),
    ]


@pytest.mark.asyncio
async def test_processing_updates_move_asset_to_ready(clients, repos):
    _, user_client = clients
    asset_id = await create_asset(user_client)

    await repos["asset"].update_technical_metadata(
        organization_id=ORG,
        asset_id=asset_id,
        duration_ms=30_000,
        width=1920,
        height=1080,
        orientation="horizontal",
        checksum_sha256="b" * 64,  # the API requires 64 hex chars
    )
    await repos["asset"].update_storage_keys(
        organization_id=ORG,
        asset_id=asset_id,
        proxy_storage_key=f"orgs/{ORG}/assets/{asset_id}/proxy/proxy.mp4",
        thumbnail_storage_key=f"orgs/{ORG}/assets/{asset_id}/thumbnails/main.jpg",
    )
    await repos["job"].mark_completed(organization_id=ORG, job_id=asset_id)

    asset = (await user_client.get(f"/api/v1/assets/{asset_id}")).json()
    assert asset["status"] == "ready"
    assert asset["duration_ms"] == 30_000
    assert asset["width"] == 1920
    assert asset["height"] == 1080


@pytest.mark.asyncio
async def test_proxy_key_makes_playback_url_available(clients, repos):
    _, user_client = clients
    asset_id = await create_asset(user_client)

    # Before the worker reports a proxy, playback is unavailable.
    early = await user_client.get(f"/api/v1/assets/{asset_id}/playback-url")
    assert early.status_code == 409

    await repos["asset"].update_storage_keys(
        organization_id=ORG,
        asset_id=asset_id,
        proxy_storage_key=f"orgs/{ORG}/assets/{asset_id}/proxy/proxy.mp4",
    )

    playback = await user_client.get(f"/api/v1/assets/{asset_id}/playback-url")
    assert playback.status_code == 200
    assert playback.json()["url"].endswith("proxy/proxy.mp4")


@pytest.mark.asyncio
async def test_storage_keys_pass_member_b_validation(clients, repos):
    """Our key convention must satisfy the API's relative-key rule."""
    _, user_client = clients
    asset_id = await create_asset(user_client)

    from worker.storage.keys import StorageKeyKind, build_storage_key

    proxy_key = build_storage_key(StorageKeyKind.PROXY, organization_id=ORG, asset_id=asset_id)
    response = await repos["api"].patch_processing(
        asset_id,
        {
            "stage": "generating_preview",
            "status": "processing",
            "progress": 35,
            "proxy_key": proxy_key,
        },
    )
    assert response["stage"] == "generating_preview"


@pytest.mark.asyncio
async def test_transcript_persists_and_is_readable_by_the_frontend(clients, repos):
    _, user_client = clients
    asset_id = await create_asset(user_client)

    count = await repos["transcript"].replace_segments(
        organization_id=ORG,
        asset_id=asset_id,
        analysis_version=ANALYSIS_VERSION,
        segments=make_segments(asset_id),
    )
    assert count == 3

    transcript = (await user_client.get(f"/api/v1/assets/{asset_id}/transcript")).json()
    assert len(transcript) == 3
    assert transcript[0]["speaker"] == "interviewer"
    assert "easier than expected" in transcript[1]["text"]


@pytest.mark.asyncio
async def test_moments_persist_and_are_readable_by_the_frontend(clients, repos):
    _, user_client = clients
    asset_id = await create_asset(user_client)

    count = await repos["moment"].upsert_moments(
        organization_id=ORG,
        asset_id=asset_id,
        analysis_version=ANALYSIS_VERSION,
        moments=make_moments(asset_id),
    )
    assert count == 2

    moments = (await user_client.get(f"/api/v1/assets/{asset_id}/moments")).json()
    assert len(moments) == 2
    categories = {m["category"] for m in moments}
    assert categories == {"testimonial", "social_proof"}
    # 0.82 quality -> 82 on the API's 0-100 scale.
    assert any(m["score"] == 82 for m in moments)


@pytest.mark.asyncio
async def test_reprocessing_is_idempotent(clients, repos):
    """Re-running the pipeline must not duplicate transcripts or moments."""
    _, user_client = clients
    asset_id = await create_asset(user_client)

    for _ in range(3):
        await repos["transcript"].replace_segments(
            organization_id=ORG,
            asset_id=asset_id,
            analysis_version=ANALYSIS_VERSION,
            segments=make_segments(asset_id),
        )
        await repos["moment"].upsert_moments(
            organization_id=ORG,
            asset_id=asset_id,
            analysis_version=ANALYSIS_VERSION,
            moments=make_moments(asset_id),
        )

    transcript = (await user_client.get(f"/api/v1/assets/{asset_id}/transcript")).json()
    moments = (await user_client.get(f"/api/v1/assets/{asset_id}/moments")).json()
    assert len(transcript) == 3
    assert len(moments) == 2

    # Moment ids are derived from their boundaries, so they are stable across runs.
    expected_id = moment_wire_id(
        asset_id=asset_id, start_ms=6_200, end_ms=18_000, moment_type="speech_segment"
    )
    assert expected_id in {m["id"] for m in moments}


@pytest.mark.asyncio
async def test_persisted_moments_are_searchable(clients, repos):
    """The end-to-end promise: a worker-persisted moment is findable by text."""
    _, user_client = clients
    asset_id = await create_asset(user_client)

    await repos["transcript"].replace_segments(
        organization_id=ORG,
        asset_id=asset_id,
        analysis_version=ANALYSIS_VERSION,
        segments=make_segments(asset_id),
    )
    await repos["moment"].upsert_moments(
        organization_id=ORG,
        asset_id=asset_id,
        analysis_version=ANALYSIS_VERSION,
        moments=make_moments(asset_id),
    )
    await repos["job"].mark_completed(organization_id=ORG, job_id=asset_id)

    results = (
        await user_client.post(
            "/api/v1/search", json={"query": "implementation", "organization_id": ORG}
        )
    ).json()

    hits = [r for r in results["results"] if r["asset_id"] == asset_id]
    assert hits, "the moment the worker persisted should be searchable"
    assert hits[0]["start_ms"] == 6_200


@pytest.mark.asyncio
async def test_failure_reports_a_user_visible_error(clients, repos):
    _, user_client = clients
    asset_id = await create_asset(user_client)

    await repos["asset"].update_status(
        organization_id=ORG,
        asset_id=asset_id,
        status=AssetStatus.FAILED,
        current_stage=ProcessingStage.FAILED,
        error_code="unsupported_media",
        error_message="Unsupported file type '.mkv'.",
    )

    asset = (await user_client.get(f"/api/v1/assets/{asset_id}")).json()
    assert asset["status"] == "failed"
    assert "unsupported_media" in asset["error_message"]

    # A failed asset can be requeued by the user, per Member B's retry endpoint.
    retry = await user_client.post(f"/api/v1/assets/{asset_id}/retry")
    assert retry.status_code == 200
    assert retry.json()["status"] == "queued"


@pytest.mark.asyncio
async def test_internal_endpoints_reject_a_missing_or_wrong_token(api_app):
    transport = httpx.ASGITransport(app=api_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as anon:
        no_token = await anon.patch(
            "/api/v1/internal/assets/whatever/processing",
            json={"stage": "queued", "status": "queued", "progress": 0},
        )
        wrong_token = await anon.patch(
            "/api/v1/internal/assets/whatever/processing",
            headers={"X-Internal-Token": "not-the-token"},
            json={"stage": "queued", "status": "queued", "progress": 0},
        )
    assert no_token.status_code == 401
    assert wrong_token.status_code == 401
