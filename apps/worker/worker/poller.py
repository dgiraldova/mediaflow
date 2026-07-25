"""Ingestion poller — claims queued jobs from the API and processes them.

This is the piece that connects Member C's pipeline to Member B's backend.
It polls ``POST /api/v1/internal/workflows/ingest``, and for each claimed job
runs the media pipeline inline, reporting progress, checksum, provider id,
derivative keys, transcript and moments back through the internal endpoints.

Running the pipeline inline rather than through Temporal keeps the demo
dependency-free. The Temporal workflow in ``worker/workflows`` remains the
production path; the stage sequence and failure semantics here are the same,
so behavior does not diverge between the two.

Usage:

    python -m worker.poller                 # process real media from R2/MinIO
    python -m worker.poller --simulate      # UI demo without R2 or FFmpeg
    python -m worker.poller --once          # drain the queue and exit
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import shutil
import uuid

import httpx

from worker.config import WorkerSettings, get_settings
from worker.domain.enums import MomentType, ProcessingStage
from worker.domain.models import MediaMomentDraft, TranscriptSegmentDraft
from worker.logging import configure_logging, get_logger
from worker.media.validation import UnsupportedMediaError, validate_media_file
from worker.moments.segmentation import generate_candidate_moments
from worker.providers.classification import MomentClassificationInput
from worker.repositories.http_api import ApiClient, build_http_repositories
from worker.storage.keys import StorageKeyKind, build_storage_key

logger = get_logger(__name__)

POLL_INTERVAL_SECONDS = 5.0


class JobFailed(Exception):
    """A job failed in a way the user should see."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


class IngestionPoller:
    def __init__(
        self,
        *,
        settings: WorkerSettings,
        api: ApiClient,
        assets,
        jobs,
        transcripts,
        moments,
        providers,
        simulate: bool = False,
        worker_id: str | None = None,
    ) -> None:
        self._settings = settings
        self._api = api
        self._assets = assets
        self._jobs = jobs
        self._transcripts = transcripts
        self._moments = moments
        self._providers = providers
        self._simulate = simulate
        self._worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"

    async def run_forever(self, *, poll_interval: float = POLL_INTERVAL_SECONDS) -> None:
        logger.info("poller.started", worker_id=self._worker_id, simulate=self._simulate)
        while True:
            processed = await self.drain_once()
            if processed == 0:
                await asyncio.sleep(poll_interval)

    async def drain_once(self, *, limit: int = 5) -> int:
        """Claim and process whatever is queued. Returns how many were handled."""
        try:
            claimed = await self._api.claim_ingestion_work(worker_id=self._worker_id, limit=limit)
        except httpx.HTTPError as exc:
            logger.warning("poller.claim_failed", error=str(exc))
            return 0

        for job in claimed:
            await self._process(job)
        return len(claimed)

    async def _process(self, job: dict) -> None:
        asset_id = str(job["asset_id"])
        organization_id = str(job["organization_id"])
        filename = str(job["original_filename"])

        logger.info("job.claimed", asset_id=asset_id, filename=filename)
        temp_dir = os.path.join(self._settings.temp_dir, asset_id)

        try:
            await self._run_pipeline(job, temp_dir=temp_dir)
            await self._jobs.mark_completed(organization_id=organization_id, job_id=asset_id)
            logger.info("job.completed", asset_id=asset_id)

        except JobFailed as exc:
            logger.warning("job.failed", asset_id=asset_id, error_code=exc.error_code)
            await self._jobs.mark_failed(
                organization_id=organization_id,
                job_id=asset_id,
                error_code=exc.error_code,
                error_details={"message": exc.message},
            )
        except Exception as exc:
            logger.exception("job.crashed", asset_id=asset_id)
            await self._jobs.mark_failed(
                organization_id=organization_id,
                job_id=asset_id,
                error_code="ingestion_failed",
                error_details={"message": str(exc)[:400]},
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    async def _run_pipeline(self, job: dict, *, temp_dir: str) -> None:
        asset_id = str(job["asset_id"])
        organization_id = str(job["organization_id"])
        filename = str(job["original_filename"])
        upload_key = str(job["upload_key"])
        byte_size = int(job.get("byte_size") or 0)

        # -- validate --------------------------------------------------------
        try:
            validate_media_file(
                filename=filename,
                mime_type=_mime_for(filename),
                byte_size=byte_size or 1,
                max_upload_bytes=self._settings.max_upload_bytes,
            )
        except UnsupportedMediaError as exc:
            raise JobFailed("unsupported_media", str(exc)) from exc

        await self._assets.report_stage(asset_id=asset_id, stage=ProcessingStage.PREPARING_FILE)

        # -- acquire + inspect ------------------------------------------------
        if self._simulate:
            probe = _simulated_probe()
            checksum = hashlib.sha256(asset_id.encode()).hexdigest()
            local_path = None
        else:
            os.makedirs(temp_dir, exist_ok=True)
            local_path = os.path.join(temp_dir, filename)
            await self._download(upload_key, local_path)
            checksum = await asyncio.to_thread(_sha256_of, local_path)
            probe = await self._inspect(local_path)

        # The API rejects a checksum already used by another asset in this
        # organization, which is how duplicate uploads are detected.
        try:
            await self._assets.update_technical_metadata(
                organization_id=organization_id,
                asset_id=asset_id,
                duration_ms=probe["duration_ms"],
                width=probe["width"],
                height=probe["height"],
                orientation=probe["orientation"],
                checksum_sha256=checksum,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 409:
                raise JobFailed(
                    "duplicate_asset",
                    "An identical file already exists in this organization.",
                ) from exc
            raise

        # -- derivatives -------------------------------------------------------
        await self._assets.report_stage(asset_id=asset_id, stage=ProcessingStage.GENERATING_PREVIEW)
        proxy_key = build_storage_key(
            StorageKeyKind.PROXY, organization_id=organization_id, asset_id=asset_id
        )
        thumb_key = build_storage_key(
            StorageKeyKind.THUMBNAIL_MAIN, organization_id=organization_id, asset_id=asset_id
        )
        if not self._simulate:
            await self._generate_derivatives(
                local_path=local_path,
                temp_dir=temp_dir,
                proxy_key=proxy_key,
                thumb_key=thumb_key,
                duration_ms=probe["duration_ms"],
            )

        await self._assets.update_storage_keys(
            organization_id=organization_id,
            asset_id=asset_id,
            proxy_storage_key=proxy_key,
            thumbnail_storage_key=thumb_key,
        )

        # -- transcript ---------------------------------------------------------
        await self._assets.report_stage(
            asset_id=asset_id, stage=ProcessingStage.TRANSCRIBING_SPEECH
        )
        segments = await self._transcribe(
            asset_id=asset_id,
            organization_id=organization_id,
            local_path=local_path,
            temp_dir=temp_dir,
            has_audio=probe["has_audio"],
        )
        if segments:
            await self._transcripts.replace_segments(
                organization_id=organization_id,
                asset_id=asset_id,
                analysis_version=self._settings.analysis_version,
                segments=segments,
            )

        # -- provider indexing ---------------------------------------------------
        await self._assets.report_stage(
            asset_id=asset_id, stage=ProcessingStage.UNDERSTANDING_VIDEO
        )
        provider_asset_id = await self._index_with_provider(
            asset_id=asset_id, local_path=local_path, probe=probe
        )
        if provider_asset_id:
            await self._assets.update_provider_asset_id(
                organization_id=organization_id,
                asset_id=asset_id,
                provider_asset_id=provider_asset_id,
            )

        # -- moments --------------------------------------------------------------
        await self._assets.report_stage(
            asset_id=asset_id, stage=ProcessingStage.IDENTIFYING_MOMENTS
        )
        drafts = await self._build_moments(
            asset_id=asset_id,
            organization_id=organization_id,
            asset_title=filename,
            segments=segments,
        )

        await self._assets.report_stage(asset_id=asset_id, stage=ProcessingStage.PREPARING_SEARCH)
        if drafts:
            await self._moments.upsert_moments(
                organization_id=organization_id,
                asset_id=asset_id,
                analysis_version=self._settings.analysis_version,
                moments=drafts,
            )

    # -- steps that touch infrastructure -------------------------------------------

    async def _download(self, upload_key: str, destination: str) -> None:
        storage = self._providers["storage"]
        obj = await storage.head_object(key=upload_key)
        if obj is None:
            raise JobFailed(
                "source_missing",
                f"The uploaded file was not found in storage ({upload_key}).",
            )
        await storage.download_to_path(key=upload_key, destination_path=destination)

    async def _inspect(self, local_path: str) -> dict:
        from worker.media.ffprobe import inspect_media

        try:
            probe = await inspect_media(local_path)
        except Exception as exc:
            raise JobFailed("corrupted_source", f"The file could not be read: {exc}") from exc
        return {
            "duration_ms": probe.duration_ms,
            "width": probe.width,
            "height": probe.height,
            "orientation": probe.orientation.value,
            "has_audio": probe.has_audio,
        }

    async def _generate_derivatives(
        self, *, local_path: str, temp_dir: str, proxy_key: str, thumb_key: str, duration_ms
    ) -> None:
        from worker.media.ffmpeg import generate_proxy, generate_thumbnails

        storage = self._providers["storage"]
        proxy_path = os.path.join(temp_dir, "proxy.mp4")
        await generate_proxy(input_path=local_path, output_path=proxy_path)
        await storage.upload_from_path(
            key=proxy_key, source_path=proxy_path, content_type="video/mp4"
        )

        thumbs = await generate_thumbnails(
            input_path=local_path,
            output_dir=os.path.join(temp_dir, "thumbnails"),
            duration_ms=duration_ms or 0,
        )
        await storage.upload_from_path(
            key=thumb_key, source_path=thumbs.main_path, content_type="image/jpeg"
        )

    async def _transcribe(
        self, *, asset_id: str, organization_id: str, local_path, temp_dir: str, has_audio: bool
    ) -> list[TranscriptSegmentDraft]:
        if not has_audio:
            return []

        if self._simulate:
            raw = _SIMULATED_TRANSCRIPT
        else:
            from worker.media.ffmpeg import extract_audio

            audio_path = os.path.join(temp_dir, "audio.wav")
            await extract_audio(input_path=local_path, output_path=audio_path)
            results = await self._providers["transcription"].transcribe(audio_path=audio_path)
            raw = [(r.start_ms, r.end_ms, r.text, r.speaker_label) for r in results]

        return [
            TranscriptSegmentDraft(
                asset_id=asset_id,
                organization_id=organization_id,
                sequence_number=i,
                start_ms=start,
                end_ms=end,
                text_original=text,
                text_normalized=text.lower(),
                provider="simulated" if self._simulate else "openai-compatible",
                analysis_version=self._settings.analysis_version,
                speaker_label=speaker,
            )
            for i, (start, end, text, speaker) in enumerate(raw)
        ]

    async def _index_with_provider(self, *, asset_id: str, local_path, probe: dict) -> str | None:
        if self._simulate or local_path is None:
            return None
        provider = self._providers["video_intelligence"]
        try:
            return await provider.index_asset(
                asset_id=asset_id, media_path=local_path, metadata=probe
            )
        except Exception as exc:
            # Provider indexing enriches search but is not required for an
            # asset to be usable, so a failure here must not fail the job.
            logger.warning("provider.indexing_failed", asset_id=asset_id, error=str(exc))
            return None

    async def _build_moments(
        self,
        *,
        asset_id: str,
        organization_id: str,
        asset_title: str,
        segments: list[TranscriptSegmentDraft],
    ) -> list[MediaMomentDraft]:
        if not segments:
            return []

        candidates = generate_candidate_moments(segments)
        classifier = self._providers["classification"]
        drafts: list[MediaMomentDraft] = []

        for i, candidate in enumerate(candidates):
            classification = await classifier.classify_moment(
                MomentClassificationInput(
                    transcript_text=candidate.transcript_text,
                    neighboring_context="",
                    visual_description="",
                    start_ms=candidate.start_ms,
                    end_ms=candidate.end_ms,
                    asset_title=asset_title,
                    language=None,
                )
            )
            drafts.append(
                MediaMomentDraft(
                    asset_id=asset_id,
                    organization_id=organization_id,
                    sequence_number=i,
                    start_ms=candidate.start_ms,
                    end_ms=candidate.end_ms,
                    moment_type=MomentType.SPEECH_SEGMENT,
                    title=(classification.title or candidate.transcript_text)[:120],
                    visual_description=classification.visual_description,
                    marketing_description=(
                        classification.marketing_description or candidate.transcript_text
                    ),
                    analysis_version=self._settings.analysis_version,
                    transcript_text=candidate.transcript_text,
                    content_types=classification.content_types or ["other"],
                    technical_quality_score=classification.technical_quality_score or 0.75,
                )
            )
        return drafts


# -- simulated inputs, used only with --simulate -----------------------------------

_SIMULATED_TRANSCRIPT = [
    (0, 5_400, "So tell me how the rollout went on your side.", "interviewer"),
    (5_600, 17_800, "Honestly, implementation was easier than expected.", "customer"),
    (18_000, 29_500, "We had the whole team onboarded in under a week.", "customer"),
    (46_000, 58_000, "The part that sold us was the search across old recordings.", "customer"),
    (58_200, 71_000, "We found a demo from last year in about ten seconds.", "customer"),
]


def _simulated_probe() -> dict:
    return {
        "duration_ms": 71_000,
        "width": 1920,
        "height": 1080,
        "orientation": "horizontal",
        "has_audio": True,
    }


def _sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mime_for(filename: str) -> str:
    extension = filename[filename.rfind(".") :].lower() if "." in filename else ""
    return {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".webm": "video/webm",
        ".m4v": "video/x-m4v",
    }.get(extension, "application/octet-stream")


def build_poller(settings: WorkerSettings, *, simulate: bool) -> IngestionPoller:
    from worker.main import build_dependencies

    if not settings.api.internal_token:
        raise SystemExit(
            "MEDIAFLOW_API_INTERNAL_TOKEN is not set. The poller needs it to claim work."
        )

    deps = build_dependencies(settings)
    api, assets, jobs, transcripts, moments = build_http_repositories(
        base_url=settings.api.base_url, internal_token=settings.api.internal_token
    )
    return IngestionPoller(
        settings=settings,
        api=api,
        assets=assets,
        jobs=jobs,
        transcripts=transcripts,
        moments=moments,
        providers={
            "storage": deps.storage,
            "transcription": deps.transcription_provider,
            "video_intelligence": deps.video_intelligence_provider,
            "classification": deps.classification_provider,
        },
        simulate=simulate,
    )


async def _main(args: argparse.Namespace) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)

    if args.simulate:
        logger.warning(
            "poller.simulate_mode",
            detail=(
                "Media is NOT being processed. Metadata, transcripts and moments are "
                "synthetic placeholders for UI demonstration only."
            ),
        )

    poller = build_poller(settings, simulate=args.simulate)

    if args.once:
        count = await poller.drain_once()
        logger.info("poller.drained", processed=count)
        return 0

    await poller.run_forever(poll_interval=args.interval)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Skip real media processing and report synthetic results (UI demos only).",
    )
    parser.add_argument("--once", action="store_true", help="Drain the queue once and exit.")
    parser.add_argument("--interval", type=float, default=POLL_INTERVAL_SECONDS)
    raise SystemExit(asyncio.run(_main(parser.parse_args())))


if __name__ == "__main__":
    main()
