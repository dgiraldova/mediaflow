"""``IngestAssetWorkflow`` (spec sections 11.3 and 20, Phases 1-3).

Orchestrates: acquire -> validate -> inspect -> derivatives -> transcribe ->
index -> segment -> classify -> embed -> persist -> finalize, with
compensation on any non-retryable failure so a failed ingestion always
leaves the asset in a clean ``failed`` state instead of stuck mid-pipeline.

Workflow code must stay deterministic: no direct I/O, no vendor SDKs, no
``datetime.now()``/``random`` — all of that lives in activities. See
``worker/activities/ingestion.py`` for the actual work.

``heartbeat_timeout`` is only set on activities whose implementation
actually calls ``activity.heartbeat()`` (ffmpeg jobs via their progress
callback, video-provider polling, per-moment classification). Setting it
on an activity that never heartbeats would make Temporal time it out
after that interval regardless of whether the activity is still working.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from worker.activities.types import (
        AcquireSourceFileResult,
        AssetContext,
        ChecksumResult,
        ClassifyMomentsInput,
        DeletePartialDerivativesInput,
        DetectCandidateMomentsInput,
        ExtractAudioInput,
        FinalizeAssetInput,
        GenerateProxyInput,
        GenerateTextEmbeddingsInput,
        GenerateThumbnailInput,
        IndexWithVideoProviderInput,
        IngestAssetWorkflowInput,
        IngestAssetWorkflowResult,
        InspectMediaResult,
        MarkAssetFailedInput,
        PersistSearchDocumentsInput,
        StoreOriginalInput,
        TranscribeAudioInput,
    )

STANDARD_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=5,
)

# AI-provider calls get the same shape of backoff; Temporal's SDKs add
# randomized jitter to each computed interval automatically, satisfying
# "exponential backoff with jitter" (spec 11.5) without extra configuration.
AI_PROVIDER_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=3),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=5),
    maximum_attempts=5,
)

NO_RETRY_POLICY = RetryPolicy(maximum_attempts=1)


@workflow.defn(name="IngestAssetWorkflow")
class IngestAssetWorkflow:
    def __init__(self) -> None:
        self._produced_storage_keys: list[str] = []
        self._temp_dir: str | None = None

    @workflow.run
    async def run(self, workflow_input: IngestAssetWorkflowInput) -> IngestAssetWorkflowResult:
        context = workflow_input.context
        analysis_version = workflow_input.analysis_version

        try:
            await workflow.execute_activity(
                "validate_asset",
                workflow_input,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=NO_RETRY_POLICY,
            )

            acquired: AcquireSourceFileResult = await workflow.execute_activity(
                "acquire_source_file",
                workflow_input,
                start_to_close_timeout=timedelta(minutes=30),
                retry_policy=STANDARD_RETRY_POLICY,
            )
            self._temp_dir = acquired.local_path.rsplit("/", 1)[0]

            checksum: ChecksumResult = await workflow.execute_activity(
                "calculate_checksum",
                args=[workflow_input, acquired.local_path],
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=STANDARD_RETRY_POLICY,
            )
            if checksum.duplicate_of_asset_id is not None:
                await self._fail(
                    context,
                    error_code="duplicate_asset",
                    error_message=(
                        f"Asset content already exists as "
                        f"{checksum.duplicate_of_asset_id} in this organization."
                    ),
                )
                return IngestAssetWorkflowResult(
                    asset_id=context.asset_id,
                    status="failed",
                    moment_count=0,
                    error_code="duplicate_asset",
                )

            probe: InspectMediaResult = await workflow.execute_activity(
                "inspect_media",
                args=[workflow_input, acquired.local_path, checksum.checksum_sha256],
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=NO_RETRY_POLICY,
            )

            original_key = await workflow.execute_activity(
                "store_original_if_required",
                StoreOriginalInput(
                    context=context,
                    local_path=acquired.local_path,
                    filename=workflow_input.original_filename,
                    mime_type=workflow_input.mime_type,
                ),
                start_to_close_timeout=timedelta(minutes=30),
                retry_policy=STANDARD_RETRY_POLICY,
            )
            self._produced_storage_keys.append(original_key)

            proxy_result = await workflow.execute_activity(
                "generate_video_proxy",
                GenerateProxyInput(
                    context=context,
                    local_input_path=acquired.local_path,
                    duration_ms=probe.duration_ms,
                ),
                start_to_close_timeout=timedelta(hours=2),
                heartbeat_timeout=timedelta(seconds=60),
                retry_policy=STANDARD_RETRY_POLICY,
            )
            self._produced_storage_keys.append(proxy_result.storage_key)

            thumbnail_result = await workflow.execute_activity(
                "generate_thumbnail",
                GenerateThumbnailInput(
                    context=context,
                    local_input_path=acquired.local_path,
                    duration_ms=probe.duration_ms,
                ),
                start_to_close_timeout=timedelta(minutes=15),
                retry_policy=STANDARD_RETRY_POLICY,
            )
            self._produced_storage_keys.append(thumbnail_result.main_storage_key)
            self._produced_storage_keys.extend(thumbnail_result.preview_storage_keys)

            provider_asset_id: str | None = None
            transcript_segments = []
            if probe.has_audio:
                audio_result = await workflow.execute_activity(
                    "extract_audio",
                    ExtractAudioInput(context=context, local_input_path=acquired.local_path),
                    start_to_close_timeout=timedelta(minutes=30),
                    heartbeat_timeout=timedelta(seconds=60),
                    retry_policy=STANDARD_RETRY_POLICY,
                )
                self._produced_storage_keys.append(audio_result.storage_key)

                transcription = await workflow.execute_activity(
                    "transcribe_audio",
                    TranscribeAudioInput(
                        context=context,
                        local_audio_path=audio_result.local_path,
                        language_hint=workflow_input.language_hint,
                        analysis_version=analysis_version,
                    ),
                    start_to_close_timeout=timedelta(minutes=30),
                    retry_policy=AI_PROVIDER_RETRY_POLICY,
                )
                transcript_segments = transcription.segments

            video_index = await workflow.execute_activity(
                "index_with_video_provider",
                IndexWithVideoProviderInput(
                    context=context,
                    local_media_path=proxy_result.local_path,
                    duration_ms=probe.duration_ms,
                    width=probe.width,
                    height=probe.height,
                ),
                start_to_close_timeout=timedelta(hours=1),
                heartbeat_timeout=timedelta(minutes=2),
                retry_policy=AI_PROVIDER_RETRY_POLICY,
            )
            provider_asset_id = video_index.provider_asset_id

            moment_count = 0
            if transcript_segments:
                candidates = await workflow.execute_activity(
                    "detect_candidate_moments",
                    DetectCandidateMomentsInput(segments=transcript_segments),
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=STANDARD_RETRY_POLICY,
                )

                classified = await workflow.execute_activity(
                    "classify_moments",
                    ClassifyMomentsInput(
                        context=context,
                        asset_title=workflow_input.asset_title,
                        candidates=candidates.candidates,
                        organization_vocabulary=workflow_input.organization_vocabulary,
                        language=workflow_input.language_hint,
                    ),
                    start_to_close_timeout=timedelta(minutes=30),
                    heartbeat_timeout=timedelta(minutes=2),
                    retry_policy=AI_PROVIDER_RETRY_POLICY,
                )

                embedded = await workflow.execute_activity(
                    "generate_text_embeddings",
                    GenerateTextEmbeddingsInput(moments=classified.moments),
                    start_to_close_timeout=timedelta(minutes=10),
                    retry_policy=AI_PROVIDER_RETRY_POLICY,
                )

                persisted = await workflow.execute_activity(
                    "persist_search_documents",
                    PersistSearchDocumentsInput(
                        context=context,
                        analysis_version=analysis_version,
                        moments=embedded.moments,
                    ),
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=STANDARD_RETRY_POLICY,
                )
                moment_count = persisted.persisted_count

            await workflow.execute_activity(
                "finalize_asset",
                FinalizeAssetInput(
                    context=context,
                    proxy_storage_key=proxy_result.storage_key,
                    thumbnail_storage_key=thumbnail_result.main_storage_key,
                    provider_asset_id=provider_asset_id,
                    analysis_version=analysis_version,
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=STANDARD_RETRY_POLICY,
            )

            if self._temp_dir:
                await workflow.execute_activity(
                    "cleanup_temporary_files",
                    self._temp_dir,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=STANDARD_RETRY_POLICY,
                )

            return IngestAssetWorkflowResult(
                asset_id=context.asset_id, status="ready", moment_count=moment_count
            )

        except Exception as exc:
            await self._fail(context, error_code="ingestion_failed", error_message=str(exc))
            return IngestAssetWorkflowResult(
                asset_id=context.asset_id,
                status="failed",
                moment_count=0,
                error_code="ingestion_failed",
            )

    async def _fail(self, context: AssetContext, *, error_code: str, error_message: str) -> None:
        await workflow.execute_activity(
            "mark_asset_failed",
            MarkAssetFailedInput(
                context=context, error_code=error_code, error_message=error_message
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=STANDARD_RETRY_POLICY,
        )
        if self._produced_storage_keys:
            await workflow.execute_activity(
                "delete_partial_derivatives",
                DeletePartialDerivativesInput(storage_keys=self._produced_storage_keys),
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=STANDARD_RETRY_POLICY,
            )
        if self._temp_dir:
            await workflow.execute_activity(
                "release_temporary_resources",
                self._temp_dir,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=STANDARD_RETRY_POLICY,
            )
