"""Ingestion activities backing ``IngestAssetWorkflow`` (spec section 11.3).

Each public method corresponds 1:1 to an activity name in the spec. All
I/O, subprocess execution and vendor SDK calls live here — workflows only
orchestrate. Activities raise ``temporalio.exceptions.ApplicationError``
with ``non_retryable=True`` for failures the spec marks as "no automatic
retry" (invalid media, corrupted source); everything else uses the
workflow-level retry policy (spec section 11.5).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
from dataclasses import replace

from temporalio import activity
from temporalio.exceptions import ApplicationError

from worker.activities.deps import ActivityDependencies
from worker.activities.types import (
    AcquireSourceFileResult,
    CandidateMomentPayload,
    ChecksumResult,
    ClassifyMomentsInput,
    ClassifyMomentsResult,
    DetectCandidateMomentsInput,
    DetectCandidateMomentsResult,
    ExtractAudioInput,
    ExtractAudioResult,
    FinalizeAssetInput,
    GenerateProxyInput,
    GenerateProxyResult,
    GenerateTextEmbeddingsInput,
    GenerateTextEmbeddingsResult,
    GenerateThumbnailInput,
    GenerateThumbnailResult,
    IndexWithVideoProviderInput,
    IndexWithVideoProviderResult,
    IngestAssetWorkflowInput,
    InspectMediaResult,
    MomentDraftPayload,
    PersistSearchDocumentsInput,
    PersistSearchDocumentsResult,
    StoreOriginalInput,
    TranscribeAudioInput,
    TranscribeAudioResult,
    TranscriptSegmentPayload,
)
from worker.domain.enums import AssetStatus, MomentType, ProcessingStage
from worker.domain.models import MediaMomentDraft, TranscriptSegmentDraft
from worker.google_drive.client import DriveClient
from worker.google_drive.oauth import DriveCredentials, TokenRefreshRequired
from worker.logging import get_logger
from worker.media.ffmpeg import extract_audio as ffmpeg_extract_audio
from worker.media.ffmpeg import generate_proxy as ffmpeg_generate_proxy
from worker.media.ffmpeg import generate_thumbnails as ffmpeg_generate_thumbnails
from worker.media.ffprobe import inspect_media as ffprobe_inspect_media
from worker.media.validation import UnsupportedMediaError, validate_media_file
from worker.moments.segmentation import generate_candidate_moments
from worker.providers.classification import (
    MomentClassificationInput,
    NullClassificationProvider,
)
from worker.storage.keys import StorageKeyKind, build_storage_key

logger = get_logger(__name__)


def _non_retryable(error_code: str, message: str) -> ApplicationError:
    return ApplicationError(message, type=error_code, non_retryable=True)


class IngestionActivities:
    def __init__(self, deps: ActivityDependencies) -> None:
        self._deps = deps

    def _temp_dir(self, asset_id: str) -> str:
        path = os.path.join(self._deps.settings.temp_dir, asset_id)
        os.makedirs(path, exist_ok=True)
        return path

    # -- 1. validate_asset ---------------------------------------------------

    @activity.defn(name="validate_asset")
    async def validate_asset(self, workflow_input: IngestAssetWorkflowInput) -> None:
        try:
            validate_media_file(
                filename=workflow_input.original_filename,
                mime_type=workflow_input.mime_type,
                byte_size=workflow_input.byte_size,
                max_upload_bytes=self._deps.settings.max_upload_bytes,
            )
        except UnsupportedMediaError as exc:
            raise _non_retryable("unsupported_media", str(exc)) from exc

    # -- 2. acquire_source_file ----------------------------------------------

    @activity.defn(name="acquire_source_file")
    async def acquire_source_file(
        self, workflow_input: IngestAssetWorkflowInput
    ) -> AcquireSourceFileResult:
        context = workflow_input.context
        temp_dir = self._temp_dir(context.asset_id)
        local_path = os.path.join(temp_dir, workflow_input.original_filename)

        if workflow_input.source.kind == "direct_upload":
            if not workflow_input.source.storage_key:
                raise _non_retryable(
                    "invalid_source", "direct_upload source is missing storage_key"
                )
            await self._deps.storage.download_to_path(
                key=workflow_input.source.storage_key, destination_path=local_path
            )
        elif workflow_input.source.kind == "google_drive":
            await self._download_from_drive(workflow_input, local_path)
        else:
            raise _non_retryable(
                "invalid_source", f"Unknown source kind: {workflow_input.source.kind}"
            )

        byte_size = os.path.getsize(local_path)
        logger.info("acquire_source_file.completed", asset_id=context.asset_id, byte_size=byte_size)
        return AcquireSourceFileResult(local_path=local_path, byte_size=byte_size)

    async def _download_from_drive(
        self, workflow_input: IngestAssetWorkflowInput, local_path: str
    ) -> None:
        """Fetch the source from Google Drive, refreshing credentials if needed."""
        source = workflow_input.source
        connections = self._deps.source_connection_repository
        oauth = self._deps.google_drive_oauth

        if connections is None or oauth is None:
            raise _non_retryable(
                "drive_not_configured",
                "Google Drive ingestion requires a connection repository and OAuth client.",
            )
        if not source.source_connection_id or not source.source_external_id:
            raise _non_retryable(
                "invalid_source",
                "google_drive source is missing a connection id or Drive file id.",
            )

        organization_id = workflow_input.context.organization_id
        stored = await connections.get_encrypted_credentials(
            organization_id=organization_id, connection_id=source.source_connection_id
        )

        try:
            credentials = DriveCredentials.from_storage(stored)
            refreshed = await oauth.ensure_access_token(credentials)
        except TokenRefreshRequired as exc:
            # Authentication expiry pauses the connection rather than retrying
            # (spec 11.5); the user must reconnect it.
            await connections.update_status(
                organization_id=organization_id,
                connection_id=source.source_connection_id,
                status="error",
                error_message=str(exc),
            )
            raise _non_retryable("drive_reauthorization_required", str(exc)) from exc

        if refreshed != credentials:
            await connections.update_encrypted_credentials(
                organization_id=organization_id,
                connection_id=source.source_connection_id,
                encrypted_credentials=refreshed.to_storage(),
            )

        client = DriveClient(access_token=refreshed.access_token or "")
        try:
            await client.download_to_path(
                file_id=source.source_external_id, destination_path=local_path
            )
        finally:
            await client.aclose()

    # -- 3. calculate_checksum ------------------------------------------------

    @activity.defn(name="calculate_checksum")
    async def calculate_checksum(
        self, workflow_input: IngestAssetWorkflowInput, local_path: str
    ) -> ChecksumResult:
        context = workflow_input.context

        def _hash_file() -> str:
            digest = hashlib.sha256()
            with open(local_path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()

        # Hashing a multi-GB file would block the event loop (and starve
        # activity heartbeats) if run inline.
        checksum = await asyncio.to_thread(_hash_file)

        existing = await self._deps.asset_repository.find_by_checksum(
            organization_id=context.organization_id, checksum_sha256=checksum
        )
        duplicate_of = (
            existing.id if existing is not None and existing.id != context.asset_id else None
        )
        return ChecksumResult(checksum_sha256=checksum, duplicate_of_asset_id=duplicate_of)

    # -- 4. inspect_media ------------------------------------------------------

    @activity.defn(name="inspect_media")
    async def inspect_media(
        self, workflow_input: IngestAssetWorkflowInput, local_path: str, checksum: str
    ) -> InspectMediaResult:
        context = workflow_input.context

        try:
            probe = await ffprobe_inspect_media(local_path)
        except Exception as exc:  # ffprobe failure means an unusable/corrupted file
            raise _non_retryable("corrupted_source", f"Media inspection failed: {exc}") from exc

        await self._deps.asset_repository.update_technical_metadata(
            organization_id=context.organization_id,
            asset_id=context.asset_id,
            duration_ms=probe.duration_ms,
            width=probe.width,
            height=probe.height,
            orientation=probe.orientation.value,
            checksum_sha256=checksum,
        )
        return InspectMediaResult(
            duration_ms=probe.duration_ms,
            width=probe.width,
            height=probe.height,
            orientation=probe.orientation.value,
            has_audio=probe.has_audio,
        )

    # -- 5. store_original_if_required ----------------------------------------

    @activity.defn(name="store_original_if_required")
    async def store_original_if_required(self, args: StoreOriginalInput) -> str:
        key = build_storage_key(
            StorageKeyKind.ORIGINAL,
            organization_id=args.context.organization_id,
            asset_id=args.context.asset_id,
            filename=args.filename,
        )
        existing = await self._deps.storage.head_object(key=key)
        if existing is None:
            await self._deps.storage.upload_from_path(
                key=key, source_path=args.local_path, content_type=args.mime_type
            )
        await self._deps.asset_repository.update_storage_keys(
            organization_id=args.context.organization_id,
            asset_id=args.context.asset_id,
            original_storage_key=key,
        )
        return key

    # -- 6. generate_video_proxy -----------------------------------------------

    @activity.defn(name="generate_video_proxy")
    async def generate_video_proxy(self, args: GenerateProxyInput) -> GenerateProxyResult:
        temp_dir = self._temp_dir(args.context.asset_id)
        output_path = os.path.join(temp_dir, "proxy.mp4")

        async def on_progress(out_time_ms: int) -> None:
            activity.heartbeat(out_time_ms)

        result = await ffmpeg_generate_proxy(
            input_path=args.local_input_path,
            output_path=output_path,
            on_progress=on_progress,
            total_duration_ms=args.duration_ms,
        )

        key = build_storage_key(
            StorageKeyKind.PROXY,
            organization_id=args.context.organization_id,
            asset_id=args.context.asset_id,
        )
        await self._deps.storage.upload_from_path(
            key=key, source_path=result.output_path, content_type="video/mp4"
        )
        await self._deps.asset_repository.update_storage_keys(
            organization_id=args.context.organization_id,
            asset_id=args.context.asset_id,
            proxy_storage_key=key,
        )
        return GenerateProxyResult(
            storage_key=key, local_path=result.output_path, width=result.width, height=result.height
        )

    # -- 7. generate_thumbnail --------------------------------------------------

    @activity.defn(name="generate_thumbnail")
    async def generate_thumbnail(self, args: GenerateThumbnailInput) -> GenerateThumbnailResult:
        temp_dir = os.path.join(self._temp_dir(args.context.asset_id), "thumbnails")
        result = await ffmpeg_generate_thumbnails(
            input_path=args.local_input_path,
            output_dir=temp_dir,
            duration_ms=args.duration_ms or 0,
        )

        main_key = build_storage_key(
            StorageKeyKind.THUMBNAIL_MAIN,
            organization_id=args.context.organization_id,
            asset_id=args.context.asset_id,
        )
        await self._deps.storage.upload_from_path(
            key=main_key, source_path=result.main_path, content_type="image/jpeg"
        )

        preview_keys: list[str] = []
        for index, preview_path in enumerate(result.preview_paths, start=1):
            preview_key = build_storage_key(
                StorageKeyKind.THUMBNAIL_PREVIEW,
                organization_id=args.context.organization_id,
                asset_id=args.context.asset_id,
                preview_index=index,
            )
            await self._deps.storage.upload_from_path(
                key=preview_key, source_path=preview_path, content_type="image/jpeg"
            )
            preview_keys.append(preview_key)

        await self._deps.asset_repository.update_storage_keys(
            organization_id=args.context.organization_id,
            asset_id=args.context.asset_id,
            thumbnail_storage_key=main_key,
        )
        return GenerateThumbnailResult(main_storage_key=main_key, preview_storage_keys=preview_keys)

    # -- 8. extract_audio ---------------------------------------------------------

    @activity.defn(name="extract_audio")
    async def extract_audio(self, args: ExtractAudioInput) -> ExtractAudioResult:
        temp_dir = self._temp_dir(args.context.asset_id)
        output_path = os.path.join(temp_dir, "audio.wav")

        async def on_progress(out_time_ms: int) -> None:
            activity.heartbeat(out_time_ms)

        await ffmpeg_extract_audio(
            input_path=args.local_input_path, output_path=output_path, on_progress=on_progress
        )

        key = build_storage_key(
            StorageKeyKind.AUDIO,
            organization_id=args.context.organization_id,
            asset_id=args.context.asset_id,
        )
        await self._deps.storage.upload_from_path(
            key=key, source_path=output_path, content_type="audio/wav"
        )
        return ExtractAudioResult(local_path=output_path, storage_key=key)

    # -- 9. transcribe_audio -------------------------------------------------------

    @activity.defn(name="transcribe_audio")
    async def transcribe_audio(self, args: TranscribeAudioInput) -> TranscribeAudioResult:
        try:
            segments = await self._deps.transcription_provider.transcribe(
                audio_path=args.local_audio_path, language_hint=args.language_hint
            )
        except Exception as exc:
            logger.warning(
                "provider.transcription_failed",
                asset_id=args.context.asset_id,
                error_type=type(exc).__name__,
                detail=str(exc)[:300],
            )
            segments = []

        payloads = [
            TranscriptSegmentPayload(
                start_ms=s.start_ms,
                end_ms=s.end_ms,
                text=s.text,
                speaker_label=s.speaker_label,
                language=s.language,
                confidence=s.confidence,
            )
            for s in segments
        ]

        drafts = [
            _segment_payload_to_draft(
                p,
                organization_id=args.context.organization_id,
                asset_id=args.context.asset_id,
                sequence_number=i,
                analysis_version=args.analysis_version,
            )
            for i, p in enumerate(payloads)
        ]
        await self._deps.transcript_repository.replace_segments(
            organization_id=args.context.organization_id,
            asset_id=args.context.asset_id,
            analysis_version=args.analysis_version,
            segments=drafts,
        )
        return TranscribeAudioResult(segments=payloads)

    # -- 10. index_with_video_provider ---------------------------------------------

    @activity.defn(name="index_with_video_provider")
    async def index_with_video_provider(
        self, args: IndexWithVideoProviderInput
    ) -> IndexWithVideoProviderResult:
        provider = self._deps.video_intelligence_provider
        provider_asset_id = await provider.index_asset(
            asset_id=args.context.asset_id,
            media_path=args.local_media_path,
            metadata={
                "organization_id": args.context.organization_id,
                "duration_ms": args.duration_ms,
                "width": args.width,
                "height": args.height,
            },
        )

        # Poll here (rather than delegating to a provider-side wait helper)
        # so the activity can heartbeat between polls; a multi-minute
        # indexing job would otherwise exceed heartbeat_timeout with no
        # signal back to Temporal (spec 11.3/19: heartbeat long jobs).
        poll_interval_seconds = 10.0
        max_polls = 360  # ~1 hour ceiling; start_to_close_timeout enforces the rest
        for _ in range(max_polls):
            status = await provider.get_indexing_status(provider_asset_id=provider_asset_id)
            activity.heartbeat(status)
            if status == "ready":
                break
            if status == "failed":
                raise _non_retryable(
                    "video_indexing_failed",
                    f"Video provider indexing failed for {provider_asset_id}",
                )
            await asyncio.sleep(poll_interval_seconds)
        else:
            raise ApplicationError(
                f"Video provider indexing timed out for {provider_asset_id}",
                type="video_indexing_timeout",
            )

        await self._deps.asset_repository.update_provider_asset_id(
            organization_id=args.context.organization_id,
            asset_id=args.context.asset_id,
            provider_asset_id=provider_asset_id,
        )
        return IndexWithVideoProviderResult(provider_asset_id=provider_asset_id)

    # -- 11. detect_candidate_moments ------------------------------------------------

    @activity.defn(name="detect_candidate_moments")
    async def detect_candidate_moments(
        self, args: DetectCandidateMomentsInput
    ) -> DetectCandidateMomentsResult:
        candidates = generate_candidate_moments(args.segments)

        return DetectCandidateMomentsResult(
            candidates=[
                CandidateMomentPayload(
                    start_ms=c.start_ms,
                    end_ms=c.end_ms,
                    transcript_text=c.transcript_text,
                    speaker_labels=c.speaker_labels,
                )
                for c in candidates
            ]
        )

    # -- 12. classify_moments ----------------------------------------------------------

    @activity.defn(name="classify_moments")
    async def classify_moments(self, args: ClassifyMomentsInput) -> ClassifyMomentsResult:
        drafts: list[MomentDraftPayload] = []
        fallback_classifier = NullClassificationProvider()
        for sequence_number, candidate in enumerate(args.candidates):
            neighbor_texts = [
                c.transcript_text
                for c in args.candidates
                if abs(c.start_ms - candidate.start_ms) <= 60_000 and c is not candidate
            ]
            classification_input = MomentClassificationInput(
                transcript_text=candidate.transcript_text,
                neighboring_context=" ".join(neighbor_texts)[:2000],
                visual_description="",
                start_ms=candidate.start_ms,
                end_ms=candidate.end_ms,
                asset_title=args.asset_title,
                organization_vocabulary=args.organization_vocabulary,
                language=args.language,
            )
            try:
                classification = await self._deps.classification_provider.classify_moment(
                    classification_input
                )
            except Exception as exc:
                logger.warning(
                    "provider.classification_failed",
                    asset_id=args.context.asset_id,
                    error_type=type(exc).__name__,
                    detail=str(exc)[:300],
                )
                classification = await fallback_classifier.classify_moment(
                    classification_input
                )
            drafts.append(
                MomentDraftPayload(
                    sequence_number=sequence_number,
                    start_ms=candidate.start_ms,
                    end_ms=candidate.end_ms,
                    moment_type=MomentType.SPEECH_SEGMENT.value,
                    title=classification.title,
                    transcript_text=candidate.transcript_text or None,
                    visual_description=classification.visual_description,
                    marketing_description=classification.marketing_description,
                    content_types=classification.content_types,
                    topics=classification.topics,
                    pain_points=classification.pain_points,
                    benefits=classification.benefits,
                    funnel_stages=classification.funnel_stages,
                    people_labels=classification.people_labels,
                    product_labels=classification.product_labels,
                    keywords=classification.keywords,
                    technical_quality_score=classification.technical_quality_score,
                )
            )
            activity.heartbeat(sequence_number)

        return ClassifyMomentsResult(moments=drafts)

    # -- 13. generate_text_embeddings --------------------------------------------------

    @activity.defn(name="generate_text_embeddings")
    async def generate_text_embeddings(
        self, args: GenerateTextEmbeddingsInput
    ) -> GenerateTextEmbeddingsResult:
        texts = [m.marketing_description or m.transcript_text or m.title for m in args.moments]
        embeddings = await self._deps.embedding_provider.embed_texts(texts)

        updated = [
            replace(m, embedding=embedding)
            for m, embedding in zip(args.moments, embeddings, strict=True)
        ]
        return GenerateTextEmbeddingsResult(moments=updated)

    # -- 14. persist_search_documents ----------------------------------------------------

    @activity.defn(name="persist_search_documents")
    async def persist_search_documents(
        self, args: PersistSearchDocumentsInput
    ) -> PersistSearchDocumentsResult:
        drafts = [
            _moment_payload_to_draft(
                m,
                organization_id=args.context.organization_id,
                asset_id=args.context.asset_id,
                analysis_version=args.analysis_version,
            )
            for m in args.moments
        ]
        persisted = await self._deps.moment_repository.upsert_moments(
            organization_id=args.context.organization_id,
            asset_id=args.context.asset_id,
            analysis_version=args.analysis_version,
            moments=drafts,
        )
        return PersistSearchDocumentsResult(persisted_count=persisted)

    # -- 15. finalize_asset ------------------------------------------------------------------

    @activity.defn(name="finalize_asset")
    async def finalize_asset(self, args: FinalizeAssetInput) -> None:
        await self._deps.asset_repository.update_status(
            organization_id=args.context.organization_id,
            asset_id=args.context.asset_id,
            status=AssetStatus.READY,
            current_stage=ProcessingStage.READY,
        )
        await self._deps.processing_job_repository.mark_completed(
            organization_id=args.context.organization_id, job_id=args.context.processing_job_id
        )

    # -- 16. cleanup_temporary_files -----------------------------------------------------------

    @activity.defn(name="cleanup_temporary_files")
    async def cleanup_temporary_files(self, temp_dir: str) -> None:
        shutil.rmtree(temp_dir, ignore_errors=True)

def _segment_payload_to_draft(
    payload: TranscriptSegmentPayload,
    *,
    organization_id: str,
    asset_id: str,
    sequence_number: int,
    analysis_version: str,
) -> TranscriptSegmentDraft:
    return TranscriptSegmentDraft(
        asset_id=asset_id,
        organization_id=organization_id,
        sequence_number=sequence_number,
        start_ms=payload.start_ms,
        end_ms=payload.end_ms,
        text_original=payload.text,
        text_normalized=payload.text.strip().lower(),
        provider="openai-compatible",
        analysis_version=analysis_version,
        speaker_label=payload.speaker_label,
        language=payload.language,
        confidence=payload.confidence,
    )


def _moment_payload_to_draft(
    payload: MomentDraftPayload,
    *,
    organization_id: str,
    asset_id: str,
    analysis_version: str,
) -> MediaMomentDraft:
    return MediaMomentDraft(
        asset_id=asset_id,
        organization_id=organization_id,
        sequence_number=payload.sequence_number,
        start_ms=payload.start_ms,
        end_ms=payload.end_ms,
        moment_type=MomentType(payload.moment_type),
        title=payload.title,
        visual_description=payload.visual_description,
        marketing_description=payload.marketing_description,
        analysis_version=analysis_version,
        transcript_text=payload.transcript_text,
        content_types=payload.content_types,
        topics=payload.topics,
        pain_points=payload.pain_points,
        benefits=payload.benefits,
        funnel_stages=payload.funnel_stages,
        people_labels=payload.people_labels,
        product_labels=payload.product_labels,
        keywords=payload.keywords,
        technical_quality_score=payload.technical_quality_score,
        embedding=payload.embedding,
    )
