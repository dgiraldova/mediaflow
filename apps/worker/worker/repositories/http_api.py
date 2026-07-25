"""Repository implementations backed by Member B's FastAPI service.

These satisfy the Protocols in ``worker.repositories.interfaces`` by calling
the internal worker endpoints in ``app/main.py``:

    PATCH /api/v1/internal/assets/{asset_id}/processing
    PUT   /api/v1/internal/assets/{asset_id}/transcript
    PUT   /api/v1/internal/assets/{asset_id}/moments

Authentication is the shared ``X-Internal-Token`` header. That token grants
unscoped access to every asset, so it must exist only in the worker's
environment and never reach a browser (spec section 6.6).

Note the impedance mismatch this class absorbs: the worker tracks asset
metadata, storage keys and status as separate concerns, but Member B exposes
one merged PATCH. This class therefore keeps the current stage/progress in
memory and always sends a complete, valid PATCH body. Schema translation
lives in ``api_mapping``.
"""

from __future__ import annotations

import httpx

from worker.domain.enums import AssetStatus, MediaType, Orientation, ProcessingStage
from worker.domain.models import Asset, MediaMomentDraft, TranscriptSegmentDraft
from worker.logging import get_logger
from worker.repositories.api_mapping import (
    WORKER_STATUS_TO_API,
    moments_to_wire,
    transcript_segments_to_wire,
)

logger = get_logger(__name__)

# Progress percentages reported for each pipeline stage, so the library UI has
# a monotonically advancing bar rather than jumping 0 -> 100.
STAGE_PROGRESS: dict[ProcessingStage, int] = {
    ProcessingStage.QUEUED: 0,
    ProcessingStage.PREPARING_FILE: 15,
    ProcessingStage.GENERATING_PREVIEW: 35,
    ProcessingStage.TRANSCRIBING_SPEECH: 55,
    ProcessingStage.UNDERSTANDING_VIDEO: 70,
    ProcessingStage.IDENTIFYING_MOMENTS: 85,
    ProcessingStage.PREPARING_SEARCH: 95,
    ProcessingStage.READY: 100,
    ProcessingStage.FAILED: 100,
}


class ApiClient:
    """Thin wrapper over the internal worker endpoints."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    @classmethod
    def from_settings(cls, *, base_url: str, internal_token: str, timeout: float = 30.0):
        client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"X-Internal-Token": internal_token},
            timeout=timeout,
        )
        return cls(client)

    async def patch_processing(self, asset_id: str, payload: dict[str, object]) -> dict:
        response = await self._client.patch(
            f"/api/v1/internal/assets/{asset_id}/processing", json=payload
        )
        response.raise_for_status()
        return response.json()

    async def put_transcript(self, asset_id: str, payload: dict[str, object]) -> dict:
        response = await self._client.put(
            f"/api/v1/internal/assets/{asset_id}/transcript", json=payload
        )
        response.raise_for_status()
        return response.json()

    async def put_moments(self, asset_id: str, payload: dict[str, object]) -> dict:
        response = await self._client.put(
            f"/api/v1/internal/assets/{asset_id}/moments", json=payload
        )
        response.raise_for_status()
        return response.json()

    async def aclose(self) -> None:
        await self._client.aclose()


class _ProcessingState:
    """Tracks the last reported stage/progress per asset.

    Member B's PATCH requires stage, status and progress on every call, but the
    worker updates metadata, storage keys and status independently. Without
    this, a metadata-only update would have to invent a stage and could push
    the progress bar backwards.
    """

    def __init__(self) -> None:
        self._stages: dict[str, ProcessingStage] = {}
        self._statuses: dict[str, str] = {}

    def record(self, asset_id: str, stage: ProcessingStage, status: str) -> None:
        self._stages[asset_id] = stage
        self._statuses[asset_id] = status

    def stage_for(self, asset_id: str) -> ProcessingStage:
        return self._stages.get(asset_id, ProcessingStage.PREPARING_FILE)

    def status_for(self, asset_id: str) -> str:
        return self._statuses.get(asset_id, "processing")


class HttpAssetRepository:
    def __init__(self, api: ApiClient, state: _ProcessingState | None = None) -> None:
        self._api = api
        self._state = state or _ProcessingState()

    def _base_payload(self, asset_id: str) -> dict[str, object]:
        stage = self._state.stage_for(asset_id)
        return {
            "stage": stage.value,
            "status": self._state.status_for(asset_id),
            "progress": STAGE_PROGRESS.get(stage, 50),
        }

    async def get(self, *, organization_id: str, asset_id: str) -> Asset:
        raise NotImplementedError(
            "Member B's API exposes asset reads only to end-user tokens, not the "
            "internal worker token. The workflow carries everything the worker "
            "needs in IngestAssetWorkflowInput instead."
        )

    async def find_by_checksum(
        self, *, organization_id: str, checksum_sha256: str
    ) -> Asset | None:
        # Member B's schema does not store checksums yet, so cross-asset
        # deduplication cannot be answered here. Returning None means the
        # workflow proceeds; the duplicate check re-activates for free once a
        # checksum lookup endpoint exists.
        logger.debug("asset.checksum_lookup_unsupported", checksum=checksum_sha256)
        return None

    async def update_technical_metadata(
        self,
        *,
        organization_id: str,
        asset_id: str,
        duration_ms: int | None,
        width: int | None,
        height: int | None,
        orientation: str,
        checksum_sha256: str,
    ) -> Asset:
        payload = self._base_payload(asset_id)
        payload.update(
            {"duration_ms": duration_ms, "width": width, "height": height}
        )
        await self._api.patch_processing(asset_id, payload)
        return Asset(
            id=asset_id,
            organization_id=organization_id,
            media_type=MediaType.VIDEO,
            original_filename="",
            mime_type="",
            byte_size=0,
            status=AssetStatus.PROCESSING,
            checksum_sha256=checksum_sha256,
            duration_ms=duration_ms,
            width=width,
            height=height,
            orientation=Orientation(orientation),
        )

    async def update_storage_keys(
        self,
        *,
        organization_id: str,
        asset_id: str,
        original_storage_key: str | None = None,
        proxy_storage_key: str | None = None,
        thumbnail_storage_key: str | None = None,
    ) -> Asset:
        # Member B's schema tracks proxy and thumbnail derivatives only; the
        # original's location is already known to the API from upload initiation.
        if proxy_storage_key is None and thumbnail_storage_key is None:
            return _stub_asset(asset_id, organization_id)

        payload = self._base_payload(asset_id)
        if proxy_storage_key is not None:
            payload["proxy_key"] = proxy_storage_key
        if thumbnail_storage_key is not None:
            payload["thumbnail_key"] = thumbnail_storage_key
        await self._api.patch_processing(asset_id, payload)
        return _stub_asset(asset_id, organization_id)

    async def update_provider_asset_id(
        self, *, organization_id: str, asset_id: str, provider_asset_id: str
    ) -> Asset:
        # No column for this in Member B's schema yet. Log it so the value is
        # recoverable from worker logs until one exists.
        logger.info(
            "asset.provider_id_not_persisted",
            asset_id=asset_id,
            provider_asset_id=provider_asset_id,
        )
        return _stub_asset(asset_id, organization_id)

    async def update_status(
        self,
        *,
        organization_id: str,
        asset_id: str,
        status: AssetStatus,
        current_stage: ProcessingStage | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> Asset:
        api_status = WORKER_STATUS_TO_API.get(status.value, "processing")
        stage = current_stage or self._state.stage_for(asset_id)
        self._state.record(asset_id, stage, api_status)

        payload: dict[str, object] = {
            "stage": stage.value,
            "status": api_status,
            "progress": STAGE_PROGRESS.get(stage, 50),
        }
        if error_message:
            # Prefix the code so operators can grep failures by class while the
            # user-facing message stays readable.
            payload["error_message"] = f"[{error_code}] {error_message}"[:500]

        await self._api.patch_processing(asset_id, payload)
        return _stub_asset(asset_id, organization_id, status=status)

    async def report_stage(
        self, *, asset_id: str, stage: ProcessingStage
    ) -> None:
        """Advance the user-visible stage without changing anything else."""
        self._state.record(asset_id, stage, "processing")
        await self._api.patch_processing(asset_id, self._base_payload(asset_id))


class HttpProcessingJobRepository:
    def __init__(self, api: ApiClient, state: _ProcessingState | None = None) -> None:
        self._api = api
        self._state = state or _ProcessingState()

    async def get(self, *, organization_id: str, job_id: str):
        raise NotImplementedError(
            "Processing jobs are addressed by asset id through Member B's API; "
            "the worker never needs to fetch one by job id."
        )

    async def report_progress(
        self,
        *,
        organization_id: str,
        job_id: str,
        stage: ProcessingStage,
        progress_percent: int,
    ) -> None:
        # job_id is the asset id in this deployment: Member B's endpoints are
        # asset-scoped and resolve the newest job for that asset.
        self._state.record(job_id, stage, "processing")
        await self._api.patch_processing(
            job_id,
            {"stage": stage.value, "status": "processing", "progress": progress_percent},
        )

    async def mark_completed(self, *, organization_id: str, job_id: str) -> None:
        self._state.record(job_id, ProcessingStage.READY, "completed")
        await self._api.patch_processing(
            job_id, {"stage": ProcessingStage.READY.value, "status": "completed", "progress": 100}
        )

    async def mark_failed(
        self,
        *,
        organization_id: str,
        job_id: str,
        error_code: str,
        error_details: dict[str, object] | None = None,
    ) -> None:
        message = str((error_details or {}).get("message", error_code))
        self._state.record(job_id, ProcessingStage.FAILED, "failed")
        await self._api.patch_processing(
            job_id,
            {
                "stage": ProcessingStage.FAILED.value,
                "status": "failed",
                "progress": 100,
                "error_message": f"[{error_code}] {message}"[:500],
            },
        )


class HttpTranscriptRepository:
    def __init__(self, api: ApiClient) -> None:
        self._api = api

    async def replace_segments(
        self,
        *,
        organization_id: str,
        asset_id: str,
        analysis_version: str,
        segments: list[TranscriptSegmentDraft],
    ) -> int:
        wire = transcript_segments_to_wire(segments)
        result = await self._api.put_transcript(asset_id, {"segments": wire})
        count = int(result.get("count", len(wire)))
        logger.info("transcript.persisted", asset_id=asset_id, count=count)
        return count


class HttpMomentRepository:
    def __init__(self, api: ApiClient) -> None:
        self._api = api

    async def upsert_moments(
        self,
        *,
        organization_id: str,
        asset_id: str,
        analysis_version: str,
        moments: list[MediaMomentDraft],
    ) -> int:
        wire = moments_to_wire(moments)
        result = await self._api.put_moments(asset_id, {"moments": wire})
        count = int(result.get("count", len(wire)))
        logger.info("moments.persisted", asset_id=asset_id, count=count)
        return count


def _stub_asset(
    asset_id: str, organization_id: str, *, status: AssetStatus = AssetStatus.PROCESSING
) -> Asset:
    """Minimal Asset for Protocol conformance.

    Member B's internal endpoints return the processing job rather than the
    full asset, and no activity consumes these return values, so reconstructing
    a complete Asset would mean an extra round trip for nothing.
    """
    return Asset(
        id=asset_id,
        organization_id=organization_id,
        media_type=MediaType.VIDEO,
        original_filename="",
        mime_type="",
        byte_size=0,
        status=status,
    )


RepositorySet = tuple[
    ApiClient,
    HttpAssetRepository,
    HttpProcessingJobRepository,
    HttpTranscriptRepository,
    HttpMomentRepository,
]


def build_http_repositories(*, base_url: str, internal_token: str) -> RepositorySet:
    """Build the repository set, sharing one client and one progress state."""
    api = ApiClient.from_settings(base_url=base_url, internal_token=internal_token)
    state = _ProcessingState()
    return (
        api,
        HttpAssetRepository(api, state),
        HttpProcessingJobRepository(api, state),
        HttpTranscriptRepository(api),
        HttpMomentRepository(api),
    )
