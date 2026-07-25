"""End-to-end orchestration tests for ``IngestAssetWorkflow``.

These run the real workflow code against fake activities in Temporal's
time-skipping test environment, so they verify orchestration, ordering and
compensation without touching R2, ffmpeg or any AI provider.
"""

from __future__ import annotations

import os
import uuid

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from worker.activities.types import (
    AcquireSourceFileResult,
    AssetContext,
    CandidateMomentPayload,
    ChecksumResult,
    ClassifyMomentsInput,
    ClassifyMomentsResult,
    DeletePartialDerivativesInput,
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
    MarkAssetFailedInput,
    MomentDraftPayload,
    PersistSearchDocumentsInput,
    PersistSearchDocumentsResult,
    SourceRef,
    StoreOriginalInput,
    TranscribeAudioInput,
    TranscribeAudioResult,
    TranscriptSegmentPayload,
)
from worker.workflows.ingest_asset import IngestAssetWorkflow

ORG = "11111111-1111-1111-1111-111111111111"
ASSET = "22222222-2222-2222-2222-222222222222"
JOB = "33333333-3333-3333-3333-333333333333"
ANALYSIS_VERSION = "test-1"

# Temporal's time-skipping environment runs a bundled native test server that
# needs a loopback socket. Sandboxed or network-restricted CI agents block
# that, and the server hangs rather than failing fast, so these tests are
# opt-in: set RUN_TEMPORAL_TESTS=1 to run them.
requires_temporal_server = pytest.mark.skipif(
    os.environ.get("RUN_TEMPORAL_TESTS") != "1",
    reason="Set RUN_TEMPORAL_TESTS=1 to run tests that start the Temporal test server.",
)


class FakeActivities:
    """Records every activity invocation so tests can assert on the flow."""

    def __init__(self, *, duplicate_of: str | None = None, has_audio: bool = True) -> None:
        self.calls: list[str] = []
        self.duplicate_of = duplicate_of
        self.has_audio = has_audio
        self.deleted_storage_keys: list[str] = []
        self.failure: MarkAssetFailedInput | None = None
        self.released_temp_dirs: list[str] = []
        self.cleaned_temp_dirs: list[str] = []

    @activity.defn(name="validate_asset")
    async def validate_asset(self, workflow_input: IngestAssetWorkflowInput) -> None:
        self.calls.append("validate_asset")

    @activity.defn(name="acquire_source_file")
    async def acquire_source_file(
        self, workflow_input: IngestAssetWorkflowInput
    ) -> AcquireSourceFileResult:
        self.calls.append("acquire_source_file")
        return AcquireSourceFileResult(
            local_path=f"/tmp/mediaflow/{ASSET}/{workflow_input.original_filename}",
            byte_size=1024,
        )

    @activity.defn(name="calculate_checksum")
    async def calculate_checksum(
        self, workflow_input: IngestAssetWorkflowInput, local_path: str
    ) -> ChecksumResult:
        self.calls.append("calculate_checksum")
        return ChecksumResult(checksum_sha256="abc123", duplicate_of_asset_id=self.duplicate_of)

    @activity.defn(name="inspect_media")
    async def inspect_media(
        self, workflow_input: IngestAssetWorkflowInput, local_path: str, checksum: str
    ) -> InspectMediaResult:
        self.calls.append("inspect_media")
        return InspectMediaResult(
            duration_ms=60_000,
            width=1920,
            height=1080,
            orientation="horizontal",
            has_audio=self.has_audio,
        )

    @activity.defn(name="store_original_if_required")
    async def store_original_if_required(self, args: StoreOriginalInput) -> str:
        self.calls.append("store_original_if_required")
        return f"orgs/{ORG}/assets/{ASSET}/original/video.mp4"

    @activity.defn(name="generate_video_proxy")
    async def generate_video_proxy(self, args: GenerateProxyInput) -> GenerateProxyResult:
        self.calls.append("generate_video_proxy")
        return GenerateProxyResult(
            storage_key=f"orgs/{ORG}/assets/{ASSET}/proxy/proxy.mp4",
            local_path=f"/tmp/mediaflow/{ASSET}/proxy.mp4",
            width=1280,
            height=720,
        )

    @activity.defn(name="generate_thumbnail")
    async def generate_thumbnail(self, args: GenerateThumbnailInput) -> GenerateThumbnailResult:
        self.calls.append("generate_thumbnail")
        return GenerateThumbnailResult(
            main_storage_key=f"orgs/{ORG}/assets/{ASSET}/thumbnails/main.jpg",
            preview_storage_keys=[f"orgs/{ORG}/assets/{ASSET}/thumbnails/preview-001.jpg"],
        )

    @activity.defn(name="extract_audio")
    async def extract_audio(self, args: ExtractAudioInput) -> ExtractAudioResult:
        self.calls.append("extract_audio")
        return ExtractAudioResult(
            local_path=f"/tmp/mediaflow/{ASSET}/audio.wav",
            storage_key=f"orgs/{ORG}/assets/{ASSET}/audio/audio.wav",
        )

    @activity.defn(name="transcribe_audio")
    async def transcribe_audio(self, args: TranscribeAudioInput) -> TranscribeAudioResult:
        self.calls.append("transcribe_audio")
        return TranscribeAudioResult(
            segments=[
                TranscriptSegmentPayload(
                    start_ms=0,
                    end_ms=12_000,
                    text="Implementation was easier than expected.",
                    speaker_label="customer",
                    language="en",
                    confidence=0.9,
                )
            ]
        )

    @activity.defn(name="index_with_video_provider")
    async def index_with_video_provider(
        self, args: IndexWithVideoProviderInput
    ) -> IndexWithVideoProviderResult:
        self.calls.append("index_with_video_provider")
        return IndexWithVideoProviderResult(provider_asset_id="provider-asset-1")

    @activity.defn(name="detect_candidate_moments")
    async def detect_candidate_moments(
        self, args: DetectCandidateMomentsInput
    ) -> DetectCandidateMomentsResult:
        self.calls.append("detect_candidate_moments")
        return DetectCandidateMomentsResult(
            candidates=[
                CandidateMomentPayload(
                    start_ms=0,
                    end_ms=12_000,
                    transcript_text="Implementation was easier than expected.",
                    speaker_labels=["customer"],
                )
            ]
        )

    @activity.defn(name="classify_moments")
    async def classify_moments(self, args: ClassifyMomentsInput) -> ClassifyMomentsResult:
        self.calls.append("classify_moments")
        return ClassifyMomentsResult(
            moments=[
                MomentDraftPayload(
                    sequence_number=0,
                    start_ms=0,
                    end_ms=12_000,
                    moment_type="speech_segment",
                    title="Easy implementation",
                    transcript_text="Implementation was easier than expected.",
                    visual_description="Customer speaking to camera.",
                    marketing_description="Customer praises a smooth rollout.",
                    content_types=["testimonial"],
                    topics=["onboarding"],
                    pain_points=[],
                    benefits=["fast setup"],
                    funnel_stages=["consideration"],
                    people_labels=["customer"],
                    product_labels=[],
                    keywords=["implementation"],
                    technical_quality_score=0.8,
                )
            ]
        )

    @activity.defn(name="generate_text_embeddings")
    async def generate_text_embeddings(
        self, args: GenerateTextEmbeddingsInput
    ) -> GenerateTextEmbeddingsResult:
        self.calls.append("generate_text_embeddings")
        return GenerateTextEmbeddingsResult(moments=args.moments)

    @activity.defn(name="persist_search_documents")
    async def persist_search_documents(
        self, args: PersistSearchDocumentsInput
    ) -> PersistSearchDocumentsResult:
        self.calls.append("persist_search_documents")
        return PersistSearchDocumentsResult(persisted_count=len(args.moments))

    @activity.defn(name="finalize_asset")
    async def finalize_asset(self, args: FinalizeAssetInput) -> None:
        self.calls.append("finalize_asset")

    @activity.defn(name="cleanup_temporary_files")
    async def cleanup_temporary_files(self, temp_dir: str) -> None:
        self.calls.append("cleanup_temporary_files")
        self.cleaned_temp_dirs.append(temp_dir)

    @activity.defn(name="mark_asset_failed")
    async def mark_asset_failed(self, args: MarkAssetFailedInput) -> None:
        self.calls.append("mark_asset_failed")
        self.failure = args

    @activity.defn(name="delete_partial_derivatives")
    async def delete_partial_derivatives(self, args: DeletePartialDerivativesInput) -> None:
        self.calls.append("delete_partial_derivatives")
        self.deleted_storage_keys.extend(args.storage_keys)

    @activity.defn(name="release_temporary_resources")
    async def release_temporary_resources(self, temp_dir: str) -> None:
        self.calls.append("release_temporary_resources")
        self.released_temp_dirs.append(temp_dir)

    def all(self) -> list:
        return [
            self.validate_asset,
            self.acquire_source_file,
            self.calculate_checksum,
            self.inspect_media,
            self.store_original_if_required,
            self.generate_video_proxy,
            self.generate_thumbnail,
            self.extract_audio,
            self.transcribe_audio,
            self.index_with_video_provider,
            self.detect_candidate_moments,
            self.classify_moments,
            self.generate_text_embeddings,
            self.persist_search_documents,
            self.finalize_asset,
            self.cleanup_temporary_files,
            self.mark_asset_failed,
            self.delete_partial_derivatives,
            self.release_temporary_resources,
        ]


def make_input() -> IngestAssetWorkflowInput:
    return IngestAssetWorkflowInput(
        context=AssetContext(organization_id=ORG, asset_id=ASSET, processing_job_id=JOB),
        source=SourceRef(kind="direct_upload", storage_key=f"uploads/{ASSET}/video.mp4"),
        original_filename="video.mp4",
        mime_type="video/mp4",
        byte_size=1024,
        asset_title="Customer interview",
        analysis_version=ANALYSIS_VERSION,
        language_hint="en",
    )


async def run_workflow(client: Client, fakes: FakeActivities):
    task_queue = f"test-{uuid.uuid4()}"
    async with Worker(
        client,
        task_queue=task_queue,
        workflows=[IngestAssetWorkflow],
        activities=fakes.all(),
    ):
        return await client.execute_workflow(
            IngestAssetWorkflow.run,
            make_input(),
            id=f"ingest-{uuid.uuid4()}",
            task_queue=task_queue,
        )


@requires_temporal_server
@pytest.mark.asyncio
async def test_golden_path_makes_asset_ready_with_moments():
    fakes = FakeActivities()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await run_workflow(env.client, fakes)

    assert result.status == "ready"
    assert result.moment_count == 1
    assert result.error_code is None
    assert "finalize_asset" in fakes.calls
    assert "mark_asset_failed" not in fakes.calls


@requires_temporal_server
@pytest.mark.asyncio
async def test_golden_path_runs_pipeline_stages_in_order():
    fakes = FakeActivities()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        await run_workflow(env.client, fakes)

    expected_order = [
        "validate_asset",
        "acquire_source_file",
        "calculate_checksum",
        "inspect_media",
        "store_original_if_required",
        "generate_video_proxy",
        "generate_thumbnail",
        "extract_audio",
        "transcribe_audio",
        "index_with_video_provider",
        "detect_candidate_moments",
        "classify_moments",
        "generate_text_embeddings",
        "persist_search_documents",
        "finalize_asset",
    ]
    assert fakes.calls[: len(expected_order)] == expected_order


@requires_temporal_server
@pytest.mark.asyncio
async def test_temporary_files_are_cleaned_up_on_success():
    fakes = FakeActivities()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        await run_workflow(env.client, fakes)

    assert fakes.cleaned_temp_dirs == [f"/tmp/mediaflow/{ASSET}"]


@requires_temporal_server
@pytest.mark.asyncio
async def test_silent_video_skips_transcription_and_moment_generation():
    fakes = FakeActivities(has_audio=False)
    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await run_workflow(env.client, fakes)

    assert result.status == "ready"
    assert result.moment_count == 0
    assert "extract_audio" not in fakes.calls
    assert "transcribe_audio" not in fakes.calls
    assert "classify_moments" not in fakes.calls
    # A silent video is still indexed and still becomes a ready, playable asset.
    assert "index_with_video_provider" in fakes.calls
    assert "finalize_asset" in fakes.calls


@requires_temporal_server
@pytest.mark.asyncio
async def test_duplicate_checksum_fails_before_any_expensive_work():
    fakes = FakeActivities(duplicate_of="99999999-9999-9999-9999-999999999999")
    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await run_workflow(env.client, fakes)

    assert result.status == "failed"
    assert result.error_code == "duplicate_asset"
    assert "generate_video_proxy" not in fakes.calls
    assert "transcribe_audio" not in fakes.calls
    assert fakes.failure is not None
    assert fakes.failure.error_code == "duplicate_asset"


@requires_temporal_server
@pytest.mark.asyncio
async def test_failure_deletes_partial_derivatives_and_releases_temp_files():
    class FailingActivities(FakeActivities):
        @activity.defn(name="transcribe_audio")
        async def transcribe_audio(self, args: TranscribeAudioInput) -> TranscribeAudioResult:
            self.calls.append("transcribe_audio")
            raise RuntimeError("transcription provider exploded")

    fakes = FailingActivities()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await run_workflow(env.client, fakes)

    assert result.status == "failed"
    assert result.error_code == "ingestion_failed"
    assert fakes.failure is not None
    # Derivatives written before the failure must not be left orphaned in R2.
    assert any("proxy.mp4" in key for key in fakes.deleted_storage_keys)
    assert any("main.jpg" in key for key in fakes.deleted_storage_keys)
    assert fakes.released_temp_dirs == [f"/tmp/mediaflow/{ASSET}"]
