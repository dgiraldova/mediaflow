"""Temporal worker entrypoint.

Wires real (or Null, for local dev) provider/storage adapters and
in-memory repository fakes into ``ActivityDependencies``, then runs the
Temporal worker polling the media-ingestion task queue.

The in-memory repositories are a deliberate placeholder: they satisfy the
Protocols in ``worker.repositories.interfaces`` so the pipeline is fully
runnable end-to-end today, and get swapped for Member B's real
Postgres-backed implementations (or an internal HTTP client, per spec
section 21) without touching any activity or workflow code.
"""

from __future__ import annotations

import asyncio
import os

from temporalio.client import Client
from temporalio.worker import Worker

from worker.activities.compensation import CompensationActivities
from worker.activities.deps import ActivityDependencies
from worker.activities.ingestion import IngestionActivities
from worker.config import WorkerSettings, get_settings
from worker.logging import configure_logging, get_logger
from worker.providers.classification import (
    NullClassificationProvider,
    OpenAIClassificationProvider,
)
from worker.providers.embeddings import NullEmbeddingProvider, OpenAIEmbeddingProvider
from worker.providers.transcription import (
    NullTranscriptionProvider,
    OpenAICompatibleTranscriptionProvider,
)
from worker.providers.video_intelligence import NullVideoIntelligenceProvider, TwelveLabsProvider
from worker.repositories.http_api import build_http_repositories
from worker.repositories.memory import (
    InMemoryAssetRepository,
    InMemoryMomentRepository,
    InMemoryProcessingJobRepository,
    InMemoryTranscriptRepository,
)
from worker.storage.r2_client import R2Client
from worker.workflows.ingest_asset import IngestAssetWorkflow

logger = get_logger(__name__)


def build_dependencies(settings: WorkerSettings) -> ActivityDependencies:
    storage = R2Client(
        bucket=settings.storage.bucket,
        endpoint_url=settings.storage.resolved_endpoint_url,
        access_key_id=settings.storage.access_key_id,
        secret_access_key=settings.storage.secret_access_key,
        region=settings.storage.region,
        signed_url_ttl_seconds=settings.storage.signed_url_ttl_seconds,
        presigned_upload_ttl_seconds=settings.storage.presigned_upload_ttl_seconds,
    )

    transcription_provider = (
        OpenAICompatibleTranscriptionProvider(
            api_key=settings.transcription.api_key,
            model=settings.transcription.model,
            base_url=settings.transcription.base_url,
        )
        if settings.transcription.api_key
        else NullTranscriptionProvider()
    )

    video_intelligence_provider = (
        TwelveLabsProvider(
            api_key=settings.twelve_labs.api_key,
            index_id=settings.twelve_labs.index_id,
            base_url=settings.twelve_labs.base_url,
        )
        if settings.twelve_labs.api_key
        else NullVideoIntelligenceProvider()
    )

    classification_provider = (
        OpenAIClassificationProvider(
            api_key=settings.openai.api_key,
            model=settings.openai.classification_model,
            base_url=settings.openai.base_url,
        )
        if settings.openai.api_key
        else NullClassificationProvider()
    )

    embedding_provider = (
        OpenAIEmbeddingProvider(
            api_key=settings.openai.api_key,
            model=settings.openai.embedding_model,
            dimensions=settings.openai.embedding_dimensions,
            base_url=settings.openai.base_url,
            batch_size=settings.embedding_batch_size,
        )
        if settings.openai.api_key
        else NullEmbeddingProvider(dimensions=settings.openai.embedding_dimensions)
    )

    # Persist through Member B's API when an internal token is configured;
    # otherwise fall back to in-memory repositories so the pipeline still runs
    # standalone for local experimentation.
    if settings.api.internal_token:
        _, asset_repo, job_repo, transcript_repo, moment_repo = build_http_repositories(
            base_url=settings.api.base_url,
            internal_token=settings.api.internal_token,
        )
        logger.info("repositories.using_api", base_url=settings.api.base_url)
    else:
        asset_repo = InMemoryAssetRepository()
        job_repo = InMemoryProcessingJobRepository()
        transcript_repo = InMemoryTranscriptRepository()
        moment_repo = InMemoryMomentRepository()
        logger.warning("repositories.using_in_memory_fallback")

    return ActivityDependencies(
        settings=settings,
        storage=storage,
        asset_repository=asset_repo,
        processing_job_repository=job_repo,
        transcript_repository=transcript_repo,
        moment_repository=moment_repo,
        transcription_provider=transcription_provider,
        video_intelligence_provider=video_intelligence_provider,
        classification_provider=classification_provider,
        embedding_provider=embedding_provider,
    )


async def run_worker() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    os.makedirs(settings.temp_dir, exist_ok=True)

    deps = build_dependencies(settings)
    ingestion_activities = IngestionActivities(deps)
    compensation_activities = CompensationActivities(deps)

    client = await Client.connect(
        settings.temporal.host,
        namespace=settings.temporal.namespace,
        tls=settings.temporal.tls,
        api_key=settings.temporal.api_key or None,
    )

    worker = Worker(
        client,
        task_queue=settings.temporal.task_queue,
        workflows=[IngestAssetWorkflow],
        activities=[
            ingestion_activities.validate_asset,
            ingestion_activities.acquire_source_file,
            ingestion_activities.calculate_checksum,
            ingestion_activities.inspect_media,
            ingestion_activities.store_original_if_required,
            ingestion_activities.generate_video_proxy,
            ingestion_activities.generate_thumbnail,
            ingestion_activities.extract_audio,
            ingestion_activities.transcribe_audio,
            ingestion_activities.index_with_video_provider,
            ingestion_activities.detect_candidate_moments,
            ingestion_activities.classify_moments,
            ingestion_activities.generate_text_embeddings,
            ingestion_activities.persist_search_documents,
            ingestion_activities.finalize_asset,
            ingestion_activities.cleanup_temporary_files,
            compensation_activities.mark_asset_failed,
            compensation_activities.delete_partial_derivatives,
            compensation_activities.release_temporary_resources,
        ],
    )

    logger.info(
        "worker.starting",
        task_queue=settings.temporal.task_queue,
        temporal_host=settings.temporal.host,
        environment=settings.environment,
    )
    await worker.run()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
