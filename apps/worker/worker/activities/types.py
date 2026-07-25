"""Dataclasses passed across the workflow/activity boundary.

Temporal serializes activity arguments and return values to JSON, so every
type here is a plain dataclass of JSON-friendly fields (str/int/float/bool/
list/dict), matching the convention set in ``worker.domain.models``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class AssetContext:
    """Identifies the asset/job/organization an activity call is scoped to."""

    organization_id: str
    asset_id: str
    processing_job_id: str


@dataclass(slots=True)
class SourceRef:
    """Where the original media currently lives."""

    kind: str  # "direct_upload" | "google_drive"
    storage_key: str | None = None  # direct_upload: R2 key the browser uploaded to
    source_connection_id: str | None = None  # google_drive
    source_external_id: str | None = None  # google_drive: Drive file id


@dataclass(slots=True)
class IngestAssetWorkflowInput:
    context: AssetContext
    source: SourceRef
    original_filename: str
    mime_type: str
    byte_size: int
    asset_title: str
    analysis_version: str
    language_hint: str | None = None
    organization_vocabulary: list[str] = field(default_factory=list)


@dataclass(slots=True)
class IngestAssetWorkflowResult:
    asset_id: str
    status: str
    moment_count: int
    error_code: str | None = None


@dataclass(slots=True)
class AcquireSourceFileResult:
    local_path: str
    byte_size: int


@dataclass(slots=True)
class ChecksumResult:
    checksum_sha256: str
    duplicate_of_asset_id: str | None


@dataclass(slots=True)
class InspectMediaResult:
    duration_ms: int | None
    width: int | None
    height: int | None
    orientation: str
    has_audio: bool


@dataclass(slots=True)
class StoreOriginalInput:
    context: AssetContext
    local_path: str
    filename: str
    mime_type: str


@dataclass(slots=True)
class GenerateProxyInput:
    context: AssetContext
    local_input_path: str
    duration_ms: int | None


@dataclass(slots=True)
class GenerateProxyResult:
    storage_key: str
    local_path: str
    width: int
    height: int


@dataclass(slots=True)
class GenerateThumbnailInput:
    context: AssetContext
    local_input_path: str
    duration_ms: int | None


@dataclass(slots=True)
class GenerateThumbnailResult:
    main_storage_key: str
    preview_storage_keys: list[str]


@dataclass(slots=True)
class ExtractAudioInput:
    context: AssetContext
    local_input_path: str


@dataclass(slots=True)
class ExtractAudioResult:
    local_path: str
    storage_key: str


@dataclass(slots=True)
class TranscribeAudioInput:
    context: AssetContext
    local_audio_path: str
    language_hint: str | None
    analysis_version: str


@dataclass(slots=True)
class TranscriptSegmentPayload:
    start_ms: int
    end_ms: int
    text: str
    speaker_label: str | None
    language: str | None
    confidence: float | None


@dataclass(slots=True)
class TranscribeAudioResult:
    segments: list[TranscriptSegmentPayload]


@dataclass(slots=True)
class IndexWithVideoProviderInput:
    context: AssetContext
    local_media_path: str
    duration_ms: int | None
    width: int | None
    height: int | None


@dataclass(slots=True)
class IndexWithVideoProviderResult:
    provider_asset_id: str


@dataclass(slots=True)
class DetectCandidateMomentsInput:
    segments: list[TranscriptSegmentPayload]


@dataclass(slots=True)
class CandidateMomentPayload:
    start_ms: int
    end_ms: int
    transcript_text: str
    speaker_labels: list[str]


@dataclass(slots=True)
class DetectCandidateMomentsResult:
    candidates: list[CandidateMomentPayload]


@dataclass(slots=True)
class ClassifyMomentsInput:
    context: AssetContext
    asset_title: str
    candidates: list[CandidateMomentPayload]
    organization_vocabulary: list[str]
    language: str | None


@dataclass(slots=True)
class MomentDraftPayload:
    sequence_number: int
    start_ms: int
    end_ms: int
    moment_type: str
    title: str
    transcript_text: str | None
    visual_description: str
    marketing_description: str
    content_types: list[str]
    topics: list[str]
    pain_points: list[str]
    benefits: list[str]
    funnel_stages: list[str]
    people_labels: list[str]
    product_labels: list[str]
    keywords: list[str]
    technical_quality_score: float | None
    embedding: list[float] | None = None


@dataclass(slots=True)
class ClassifyMomentsResult:
    moments: list[MomentDraftPayload]


@dataclass(slots=True)
class GenerateTextEmbeddingsInput:
    moments: list[MomentDraftPayload]


@dataclass(slots=True)
class GenerateTextEmbeddingsResult:
    moments: list[MomentDraftPayload]


@dataclass(slots=True)
class PersistSearchDocumentsInput:
    context: AssetContext
    analysis_version: str
    moments: list[MomentDraftPayload]


@dataclass(slots=True)
class PersistSearchDocumentsResult:
    persisted_count: int


@dataclass(slots=True)
class FinalizeAssetInput:
    context: AssetContext
    proxy_storage_key: str
    thumbnail_storage_key: str
    provider_asset_id: str | None
    analysis_version: str


@dataclass(slots=True)
class MarkAssetFailedInput:
    context: AssetContext
    error_code: str
    error_message: str


@dataclass(slots=True)
class DeletePartialDerivativesInput:
    storage_keys: list[str]


@dataclass(slots=True)
class ExportClipWorkflowInput:
    organization_id: str
    clip_export_id: str
    asset_id: str
    start_ms: int
    end_ms: int
    source_storage_key: str
    source_moment_id: str | None = None


@dataclass(slots=True)
class ExportClipWorkflowResult:
    clip_export_id: str
    status: str
    output_storage_key: str | None = None
    duration_ms: int = 0
    error_code: str | None = None


@dataclass(slots=True)
class RenderClipInput:
    organization_id: str
    clip_export_id: str
    asset_id: str
    start_ms: int
    end_ms: int
    source_storage_key: str


@dataclass(slots=True)
class RenderClipResult:
    output_storage_key: str
    duration_ms: int
    byte_size: int


@dataclass(slots=True)
class MarkClipExportReadyInput:
    organization_id: str
    clip_export_id: str
    output_storage_key: str
    output_mime_type: str


@dataclass(slots=True)
class MarkClipExportFailedInput:
    organization_id: str
    clip_export_id: str
    error_message: str


@dataclass(slots=True)
class ReleaseTemporaryResourcesInput:
    temp_dir: str
