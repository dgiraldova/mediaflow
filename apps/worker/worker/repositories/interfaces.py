"""Repository contracts the worker needs from Member B's domain layer.

These Protocols are the "interface-first pull request" referenced in spec
section 22: Member C codes against this contract immediately, Member B
implements it against the real Postgres schema (either as direct
repository classes if the worker and API share a database connection, or
as an internal authenticated HTTP client if they are deployed separately —
see spec section 21, "Worker-to-API communication"). Swapping the concrete
implementation must never require changes to activities/workflows.

Every method must be idempotent: re-running an ingestion step with the
same inputs must not create duplicate rows (spec section 6.5). Concrete
implementations are expected to use checksums, idempotency keys and
database uniqueness constraints to guarantee this; the in-memory
implementation in ``worker.repositories.memory`` demonstrates the expected
semantics for local development and tests.
"""

from __future__ import annotations

from typing import Protocol

from worker.domain.enums import AssetStatus, ProcessingStage
from worker.domain.models import (
    Asset,
    ClipExportRequest,
    MediaMomentDraft,
    ProcessingJob,
    SourceConnection,
    TranscriptSegmentDraft,
)


class AssetRepository(Protocol):
    async def get(self, *, organization_id: str, asset_id: str) -> Asset: ...

    async def find_by_checksum(
        self, *, organization_id: str, checksum_sha256: str
    ) -> Asset | None: ...

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
    ) -> Asset: ...

    async def update_storage_keys(
        self,
        *,
        organization_id: str,
        asset_id: str,
        original_storage_key: str | None = None,
        proxy_storage_key: str | None = None,
        thumbnail_storage_key: str | None = None,
    ) -> Asset: ...

    async def update_provider_asset_id(
        self, *, organization_id: str, asset_id: str, provider_asset_id: str
    ) -> Asset: ...

    async def update_status(
        self,
        *,
        organization_id: str,
        asset_id: str,
        status: AssetStatus,
        current_stage: ProcessingStage | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> Asset: ...


class ProcessingJobRepository(Protocol):
    async def get(self, *, organization_id: str, job_id: str) -> ProcessingJob: ...

    async def report_progress(
        self,
        *,
        organization_id: str,
        job_id: str,
        stage: ProcessingStage,
        progress_percent: int,
    ) -> None: ...

    async def mark_completed(self, *, organization_id: str, job_id: str) -> None: ...

    async def mark_failed(
        self,
        *,
        organization_id: str,
        job_id: str,
        error_code: str,
        error_details: dict[str, object] | None = None,
    ) -> None: ...


class TranscriptRepository(Protocol):
    async def replace_segments(
        self,
        *,
        organization_id: str,
        asset_id: str,
        analysis_version: str,
        segments: list[TranscriptSegmentDraft],
    ) -> int:
        """Idempotently persist segments for one (asset, analysis_version).

        Re-running with the same analysis_version must not duplicate rows.
        Returns the number of segments persisted.
        """
        ...


class MomentRepository(Protocol):
    async def upsert_moments(
        self,
        *,
        organization_id: str,
        asset_id: str,
        analysis_version: str,
        moments: list[MediaMomentDraft],
    ) -> int:
        """Idempotent on (asset_id, start_ms, end_ms, moment_type, analysis_version)."""
        ...


class SourceConnectionRepository(Protocol):
    async def get(self, *, organization_id: str, connection_id: str) -> SourceConnection: ...

    async def update_sync_cursor(
        self, *, organization_id: str, connection_id: str, sync_cursor: str
    ) -> None: ...

    async def update_status(
        self,
        *,
        organization_id: str,
        connection_id: str,
        status: str,
        error_message: str | None = None,
    ) -> None: ...

    async def get_encrypted_credentials(
        self, *, organization_id: str, connection_id: str
    ) -> dict[str, object]: ...

    async def update_encrypted_credentials(
        self,
        *,
        organization_id: str,
        connection_id: str,
        encrypted_credentials: dict[str, object],
    ) -> None: ...


class ClipExportRepository(Protocol):
    async def get(self, *, organization_id: str, export_id: str) -> ClipExportRequest: ...

    async def mark_ready(
        self,
        *,
        organization_id: str,
        export_id: str,
        output_storage_key: str,
        output_mime_type: str,
    ) -> None: ...

    async def mark_failed(
        self, *, organization_id: str, export_id: str, error_message: str
    ) -> None: ...
