"""In-memory implementations of the repository Protocols.

Used for local development and unit/integration tests before Member B's
real Postgres-backed implementations exist. They enforce the same
uniqueness/idempotency semantics the real database is expected to enforce
(spec section 6.5) so activities can be tested against realistic behavior.

Do not use these in production: state is process-local and lost on
restart.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

from worker.domain.enums import AssetStatus, ProcessingStage
from worker.domain.models import (
    Asset,
    ClipExportRequest,
    MediaMomentDraft,
    ProcessingJob,
    SourceConnection,
    TranscriptSegmentDraft,
)


class InMemoryAssetRepository:
    def __init__(self, assets: dict[str, Asset] | None = None) -> None:
        self._assets = dict(assets or {})
        self._lock = asyncio.Lock()

    async def seed(self, asset: Asset) -> None:
        async with self._lock:
            self._assets[asset.id] = asset

    async def get(self, *, organization_id: str, asset_id: str) -> Asset:
        asset = self._assets.get(asset_id)
        if asset is None or asset.organization_id != organization_id:
            raise KeyError(f"asset {asset_id} not found for organization {organization_id}")
        return asset

    async def find_by_checksum(self, *, organization_id: str, checksum_sha256: str) -> Asset | None:
        for asset in self._assets.values():
            if (
                asset.organization_id == organization_id
                and asset.checksum_sha256 == checksum_sha256
            ):
                return asset
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
        async with self._lock:
            asset = await self.get(organization_id=organization_id, asset_id=asset_id)
            updated = replace(
                asset,
                duration_ms=duration_ms,
                width=width,
                height=height,
                orientation=orientation,
                checksum_sha256=checksum_sha256,
            )
            self._assets[asset_id] = updated
            return updated

    async def update_storage_keys(
        self,
        *,
        organization_id: str,
        asset_id: str,
        original_storage_key: str | None = None,
        proxy_storage_key: str | None = None,
        thumbnail_storage_key: str | None = None,
    ) -> Asset:
        async with self._lock:
            asset = await self.get(organization_id=organization_id, asset_id=asset_id)
            updated = replace(
                asset,
                original_storage_key=original_storage_key or asset.original_storage_key,
                proxy_storage_key=proxy_storage_key or asset.proxy_storage_key,
                thumbnail_storage_key=thumbnail_storage_key or asset.thumbnail_storage_key,
            )
            self._assets[asset_id] = updated
            return updated

    async def update_provider_asset_id(
        self, *, organization_id: str, asset_id: str, provider_asset_id: str
    ) -> Asset:
        async with self._lock:
            asset = await self.get(organization_id=organization_id, asset_id=asset_id)
            updated = replace(asset, provider_asset_id=provider_asset_id)
            self._assets[asset_id] = updated
            return updated

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
        async with self._lock:
            asset = await self.get(organization_id=organization_id, asset_id=asset_id)
            updated = replace(
                asset,
                status=status,
                current_stage=current_stage or asset.current_stage,
                error_code=error_code,
                error_message=error_message,
            )
            self._assets[asset_id] = updated
            return updated


class InMemoryProcessingJobRepository:
    def __init__(self, jobs: dict[str, ProcessingJob] | None = None) -> None:
        self._jobs = dict(jobs or {})
        self._lock = asyncio.Lock()

    async def seed(self, job: ProcessingJob) -> None:
        async with self._lock:
            self._jobs[job.id] = job

    async def get(self, *, organization_id: str, job_id: str) -> ProcessingJob:
        job = self._jobs.get(job_id)
        if job is None or job.organization_id != organization_id:
            raise KeyError(f"processing job {job_id} not found")
        return job

    async def report_progress(
        self,
        *,
        organization_id: str,
        job_id: str,
        stage: ProcessingStage,
        progress_percent: int,
    ) -> None:
        async with self._lock:
            job = await self.get(organization_id=organization_id, job_id=job_id)
            self._jobs[job_id] = replace(
                job,
                status="running",
                current_stage=stage.value,
                progress_percent=progress_percent,
            )

    async def mark_completed(self, *, organization_id: str, job_id: str) -> None:
        async with self._lock:
            job = await self.get(organization_id=organization_id, job_id=job_id)
            self._jobs[job_id] = replace(job, status="completed", progress_percent=100)

    async def mark_failed(
        self,
        *,
        organization_id: str,
        job_id: str,
        error_code: str,
        error_details: dict[str, object] | None = None,
    ) -> None:
        async with self._lock:
            job = await self.get(organization_id=organization_id, job_id=job_id)
            self._jobs[job_id] = replace(
                job,
                status="failed",
                error_code=error_code,
                error_details=error_details,
            )


class InMemoryTranscriptRepository:
    def __init__(self) -> None:
        # keyed by (asset_id, analysis_version) -> segments
        self._segments: dict[tuple[str, str], list[TranscriptSegmentDraft]] = {}
        self._lock = asyncio.Lock()

    async def replace_segments(
        self,
        *,
        organization_id: str,
        asset_id: str,
        analysis_version: str,
        segments: list[TranscriptSegmentDraft],
    ) -> int:
        async with self._lock:
            self._segments[(asset_id, analysis_version)] = list(segments)
            return len(segments)

    def get_segments(self, asset_id: str, analysis_version: str) -> list[TranscriptSegmentDraft]:
        return self._segments.get((asset_id, analysis_version), [])


class InMemoryMomentRepository:
    def __init__(self) -> None:
        # keyed by uniqueness constraint (asset_id, start_ms, end_ms, moment_type, analysis_version)
        self._moments: dict[tuple[str, int, int, str, str], MediaMomentDraft] = {}
        self._lock = asyncio.Lock()

    async def upsert_moments(
        self,
        *,
        organization_id: str,
        asset_id: str,
        analysis_version: str,
        moments: list[MediaMomentDraft],
    ) -> int:
        async with self._lock:
            count = 0
            for moment in moments:
                key = (
                    moment.asset_id,
                    moment.start_ms,
                    moment.end_ms,
                    moment.moment_type.value,
                    moment.analysis_version,
                )
                if key not in self._moments:
                    count += 1
                self._moments[key] = moment
            return count

    def get_moments(self, asset_id: str) -> list[MediaMomentDraft]:
        return [m for m in self._moments.values() if m.asset_id == asset_id]


class InMemorySourceConnectionRepository:
    def __init__(self, connections: dict[str, SourceConnection] | None = None) -> None:
        self._connections = dict(connections or {})
        self._credentials: dict[str, dict[str, object]] = {}
        self._lock = asyncio.Lock()

    async def seed(
        self, connection: SourceConnection, credentials: dict[str, object] | None = None
    ) -> None:
        async with self._lock:
            self._connections[connection.id] = connection
            self._credentials[connection.id] = credentials or {}

    async def get(self, *, organization_id: str, connection_id: str) -> SourceConnection:
        connection = self._connections.get(connection_id)
        if connection is None or connection.organization_id != organization_id:
            raise KeyError(f"connection {connection_id} not found")
        return connection

    async def update_sync_cursor(
        self, *, organization_id: str, connection_id: str, sync_cursor: str
    ) -> None:
        async with self._lock:
            connection = await self.get(
                organization_id=organization_id, connection_id=connection_id
            )
            self._connections[connection_id] = replace(connection, sync_cursor=sync_cursor)

    async def update_status(
        self,
        *,
        organization_id: str,
        connection_id: str,
        status: str,
        error_message: str | None = None,
    ) -> None:
        async with self._lock:
            connection = await self.get(
                organization_id=organization_id, connection_id=connection_id
            )
            self._connections[connection_id] = replace(connection, status=status)

    async def get_encrypted_credentials(
        self, *, organization_id: str, connection_id: str
    ) -> dict[str, object]:
        await self.get(organization_id=organization_id, connection_id=connection_id)
        return self._credentials.get(connection_id, {})

    async def update_encrypted_credentials(
        self,
        *,
        organization_id: str,
        connection_id: str,
        encrypted_credentials: dict[str, object],
    ) -> None:
        async with self._lock:
            await self.get(organization_id=organization_id, connection_id=connection_id)
            self._credentials[connection_id] = encrypted_credentials


class InMemoryClipExportRepository:
    def __init__(self, exports: dict[str, ClipExportRequest] | None = None) -> None:
        self._exports = dict(exports or {})
        self._statuses: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def seed(self, export: ClipExportRequest) -> None:
        async with self._lock:
            self._exports[export.id] = export
            self._statuses[export.id] = "queued"

    async def get(self, *, organization_id: str, export_id: str) -> ClipExportRequest:
        export = self._exports.get(export_id)
        if export is None or export.organization_id != organization_id:
            raise KeyError(f"clip export {export_id} not found")
        return export

    async def mark_ready(
        self,
        *,
        organization_id: str,
        export_id: str,
        output_storage_key: str,
        output_mime_type: str,
    ) -> None:
        async with self._lock:
            await self.get(organization_id=organization_id, export_id=export_id)
            self._statuses[export_id] = "ready"

    async def mark_failed(
        self, *, organization_id: str, export_id: str, error_message: str
    ) -> None:
        async with self._lock:
            await self.get(organization_id=organization_id, export_id=export_id)
            self._statuses[export_id] = "failed"

    def status_of(self, export_id: str) -> str:
        return self._statuses[export_id]
