"""Enumerations mirroring the canonical Postgres schema (spec section 9).

These are the worker's view of database enums owned by Member B. They must
stay in sync with the Supabase migrations in ``supabase/migrations``; any
drift is a cross-team contract change and requires an interface-first PR
(spec section 22).
"""

from __future__ import annotations

import enum


class MediaType(enum.StrEnum):
    VIDEO = "video"
    IMAGE = "image"
    AUDIO = "audio"


class Orientation(enum.StrEnum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    SQUARE = "square"
    UNKNOWN = "unknown"


class AssetStatus(enum.StrEnum):
    DISCOVERED = "discovered"
    UPLOADING = "uploading"
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    DELETED = "deleted"


class SourceProvider(enum.StrEnum):
    DIRECT_UPLOAD = "direct_upload"
    GOOGLE_DRIVE = "google_drive"


class ProcessingJobStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MomentType(enum.StrEnum):
    SCENE = "scene"
    SPEECH_SEGMENT = "speech_segment"
    SEMANTIC_MOMENT = "semantic_moment"
    WHOLE_IMAGE = "whole_image"


class ClipExportStatus(enum.StrEnum):
    QUEUED = "queued"
    RENDERING = "rendering"
    READY = "ready"
    FAILED = "failed"


class ProcessingStage(enum.StrEnum):
    """Internal pipeline stage identifiers.

    Never expose these raw values to end users; use ``FRIENDLY_STAGE_LABELS``
    (spec section 11.4) so vendor-specific stage names stay internal.
    """

    QUEUED = "queued"
    PREPARING_FILE = "preparing_file"
    GENERATING_PREVIEW = "generating_preview"
    TRANSCRIBING_SPEECH = "transcribing_speech"
    UNDERSTANDING_VIDEO = "understanding_video"
    IDENTIFYING_MOMENTS = "identifying_moments"
    PREPARING_SEARCH = "preparing_search"
    READY = "ready"
    FAILED = "failed"


FRIENDLY_STAGE_LABELS: dict[ProcessingStage, str] = {
    ProcessingStage.QUEUED: "Queued",
    ProcessingStage.PREPARING_FILE: "Preparing file",
    ProcessingStage.GENERATING_PREVIEW: "Generating preview",
    ProcessingStage.TRANSCRIBING_SPEECH: "Transcribing speech",
    ProcessingStage.UNDERSTANDING_VIDEO: "Understanding video",
    ProcessingStage.IDENTIFYING_MOMENTS: "Identifying useful moments",
    ProcessingStage.PREPARING_SEARCH: "Preparing search",
    ProcessingStage.READY: "Ready",
    ProcessingStage.FAILED: "Failed",
}
