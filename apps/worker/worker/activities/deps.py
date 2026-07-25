"""Dependency container injected into activity classes.

Built once in ``worker/main.py`` from real adapters, or assembled with fakes
in tests. Activities never construct their own providers/repositories —
this is what keeps the domain layer independent of any specific vendor
(spec section 6.4).
"""

from __future__ import annotations

from dataclasses import dataclass

from worker.config import WorkerSettings
from worker.google_drive.oauth import GoogleDriveOAuth
from worker.providers.classification import StructuredClassificationProvider
from worker.providers.embeddings import EmbeddingProvider
from worker.providers.transcription import TranscriptionProvider
from worker.providers.video_intelligence import VideoIntelligenceProvider
from worker.repositories.interfaces import (
    AssetRepository,
    ClipExportRepository,
    MomentRepository,
    ProcessingJobRepository,
    SourceConnectionRepository,
    TranscriptRepository,
)
from worker.storage.interfaces import StorageClient


@dataclass
class ActivityDependencies:
    settings: WorkerSettings
    storage: StorageClient
    asset_repository: AssetRepository
    processing_job_repository: ProcessingJobRepository
    transcript_repository: TranscriptRepository
    moment_repository: MomentRepository
    transcription_provider: TranscriptionProvider
    video_intelligence_provider: VideoIntelligenceProvider
    classification_provider: StructuredClassificationProvider
    embedding_provider: EmbeddingProvider
    # Optional until Member B ships a clip_exports table and status endpoint.
    # Export activities render and upload regardless; only status reporting
    # degrades to a log line when this is absent.
    clip_export_repository: ClipExportRepository | None = None
    # Optional until Member B ships a source_connections table. Without both,
    # Google Drive ingestion cannot resolve a connection's stored credentials.
    source_connection_repository: SourceConnectionRepository | None = None
    google_drive_oauth: GoogleDriveOAuth | None = None
