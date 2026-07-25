"""Dependency container injected into activity classes.

Built once in ``worker/main.py`` from real adapters, or assembled with fakes
in tests. Activities never construct their own providers/repositories —
this is what keeps the domain layer independent of any specific vendor
(spec section 6.4).
"""

from __future__ import annotations

from dataclasses import dataclass

from worker.config import WorkerSettings
from worker.providers.classification import StructuredClassificationProvider
from worker.providers.embeddings import EmbeddingProvider
from worker.providers.transcription import TranscriptionProvider
from worker.providers.video_intelligence import VideoIntelligenceProvider
from worker.repositories.interfaces import (
    AssetRepository,
    MomentRepository,
    ProcessingJobRepository,
    TranscriptRepository,
)
from worker.storage.r2_client import R2Client


@dataclass
class ActivityDependencies:
    settings: WorkerSettings
    storage: R2Client
    asset_repository: AssetRepository
    processing_job_repository: ProcessingJobRepository
    transcript_repository: TranscriptRepository
    moment_repository: MomentRepository
    transcription_provider: TranscriptionProvider
    video_intelligence_provider: VideoIntelligenceProvider
    classification_provider: StructuredClassificationProvider
    embedding_provider: EmbeddingProvider
