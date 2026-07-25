"""Worker-side data-transfer objects.

These are plain dataclasses (not ORM models) because they must cross the
Temporal activity/workflow boundary, which serializes payloads to JSON.
Identifiers are kept as ``str`` (UUID text) and timestamps as millisecond
integers or ISO-8601 strings so the default Temporal data converter can
(de)serialize them without custom converters.

They intentionally mirror the canonical Postgres schema (spec section 9)
but only carry the fields the worker actually reads or writes. Member B's
FastAPI/SQLAlchemy models remain the source of truth for the full schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from worker.domain.enums import (
    AssetStatus,
    MediaType,
    MomentType,
    Orientation,
    ProcessingStage,
    SourceProvider,
)


@dataclass(slots=True)
class SourceConnection:
    id: str
    organization_id: str
    provider: SourceProvider
    display_name: str
    status: str
    sync_cursor: str | None = None
    configuration: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class Asset:
    id: str
    organization_id: str
    media_type: MediaType
    original_filename: str
    mime_type: str
    byte_size: int
    status: AssetStatus
    checksum_sha256: str | None = None
    source_connection_id: str | None = None
    source_external_id: str | None = None
    duration_ms: int | None = None
    width: int | None = None
    height: int | None = None
    orientation: Orientation = Orientation.UNKNOWN
    current_stage: ProcessingStage | None = None
    error_code: str | None = None
    error_message: str | None = None
    original_storage_key: str | None = None
    proxy_storage_key: str | None = None
    thumbnail_storage_key: str | None = None
    provider_asset_id: str | None = None
    analysis_version: str | None = None


@dataclass(slots=True)
class ProcessingStageUpdate:
    asset_id: str
    organization_id: str
    processing_job_id: str
    stage: ProcessingStage
    progress_percent: int = 0
    error_code: str | None = None
    error_message: str | None = None


@dataclass(slots=True)
class ProcessingJob:
    id: str
    organization_id: str
    asset_id: str
    workflow_id: str
    status: str
    current_stage: str
    progress_percent: int = 0
    attempt: int = 1
    workflow_run_id: str | None = None
    error_code: str | None = None
    error_details: dict[str, object] | None = None


@dataclass(slots=True)
class TranscriptSegmentDraft:
    asset_id: str
    organization_id: str
    sequence_number: int
    start_ms: int
    end_ms: int
    text_original: str
    text_normalized: str
    provider: str
    analysis_version: str
    speaker_label: str | None = None
    language: str | None = None
    confidence: float | None = None
    provider_segment_id: str | None = None
    embedding: list[float] | None = None


@dataclass(slots=True)
class MediaMomentDraft:
    asset_id: str
    organization_id: str
    sequence_number: int
    start_ms: int
    end_ms: int
    moment_type: MomentType
    title: str
    visual_description: str
    marketing_description: str
    analysis_version: str
    transcript_text: str | None = None
    content_types: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    pain_points: list[str] = field(default_factory=list)
    benefits: list[str] = field(default_factory=list)
    funnel_stages: list[str] = field(default_factory=list)
    people_labels: list[str] = field(default_factory=list)
    product_labels: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    technical_quality_score: float | None = None
    embedding: list[float] | None = None
    provider_data: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class ClipExportRequest:
    id: str
    organization_id: str
    asset_id: str
    start_ms: int
    end_ms: int
    source_moment_id: str | None = None
