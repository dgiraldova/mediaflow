#!/usr/bin/env python3
"""End-to-end demo: worker pipeline -> Member B's API -> searchable moments.

Runs Member B's real FastAPI app in-process and drives it with the worker's
real segmentation, mapping and repository code. No Docker, no Temporal server,
no cloud credentials, no API keys — everything that would call an external
service uses the Null provider adapters.

    python scripts/dev/demo_pipeline.py

What it proves:
  1. The browser upload flow creates an asset (uploading -> processing).
  2. The worker reports FFprobe metadata and derivative keys.
  3. Real transcript segmentation produces moments from a transcript.
  4. Transcript and moments persist through the internal worker endpoints.
  5. The frontend can read them back and search them.
  6. Re-running changes nothing (idempotent).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "worker"))

from app.main import create_app  # noqa: E402

from worker.domain.enums import MomentType, ProcessingStage  # noqa: E402
from worker.domain.models import MediaMomentDraft, TranscriptSegmentDraft  # noqa: E402
from worker.moments.segmentation import generate_candidate_moments  # noqa: E402
from worker.providers.classification import (  # noqa: E402
    MomentClassificationInput,
    NullClassificationProvider,
)
from worker.repositories.http_api import (  # noqa: E402
    ApiClient,
    HttpAssetRepository,
    HttpMomentRepository,
    HttpProcessingJobRepository,
    HttpTranscriptRepository,
    _ProcessingState,
)
from worker.storage.keys import StorageKeyKind, build_storage_key  # noqa: E402

TOKEN = "demo-internal-token"
ORG = "demo-org"
ANALYSIS_VERSION = "demo-1"

# Stands in for what the transcription provider would return for a real
# customer-interview recording.
TRANSCRIPT = [
    (0, 5_400, "So tell me how the rollout went on your side.", "interviewer"),
    (5_600, 17_800, "Honestly, implementation was easier than expected.", "customer"),
    (18_000, 29_500, "We had the whole team onboarded in under a week.", "customer"),
    (46_000, 58_000, "The part that sold us was the search across old recordings.", "customer"),
    (58_200, 71_000, "We found a demo from last year in about ten seconds.", "customer"),
]


class Step:
    """Prints a numbered, checkable step."""

    n = 0

    @classmethod
    def start(cls, title: str) -> None:
        cls.n += 1
        print(f"\n\033[1m[{cls.n}] {title}\033[0m")

    @staticmethod
    def ok(message: str) -> None:
        print(f"    \033[32mOK\033[0m  {message}")

    @staticmethod
    def info(message: str) -> None:
        print(f"        {message}")


async def main() -> int:
    demo_media = b"mediaflow-local-upload-demo"
    api_app = create_app(
        database_url="sqlite:///./demo-pipeline.db",
        internal_worker_token=TOKEN,
        jwt_secret="demo-secret",
        media_base_url="http://127.0.0.1:8001",
        media_storage_path="./var/demo-pipeline-media",
    )

    transport = httpx.ASGITransport(app=api_app)
    async with api_app.router.lifespan_context(api_app):
        async with httpx.AsyncClient(
            transport=transport, base_url="http://api", headers={"X-Internal-Token": TOKEN}
        ) as worker_http, httpx.AsyncClient(
            transport=transport, base_url="http://api", headers={"X-User-Id": "demo-user"}
        ) as user_http:
            api = ApiClient(worker_http)
            state = _ProcessingState()
            assets = HttpAssetRepository(api, state)
            jobs = HttpProcessingJobRepository(api, state)
            transcripts = HttpTranscriptRepository(api)
            moments_repo = HttpMomentRepository(api)

            # ---------------------------------------------------------------
            Step.start("Browser uploads a file (Member A -> Member B)")
            initiated = (
                await user_http.post(
                    "/api/v1/uploads/initiate",
                    json={
                        "organization_id": ORG,
                        "original_filename": "customer_interview_q3.mp4",
                        "media_type": "video",
                    },
                )
            ).json()
            asset_id = initiated["asset_id"]
            uploaded = await user_http.put(
                initiated["upload_url"],
                content=demo_media,
                headers={"Content-Type": "video/mp4"},
            )
            uploaded.raise_for_status()
            await user_http.post(
                f"/api/v1/uploads/{initiated['upload_id']}/complete",
                json={"byte_size": len(demo_media)},
            )
            asset = (await user_http.get(f"/api/v1/assets/{asset_id}")).json()
            Step.ok(f"asset {asset_id[:8]}… created, status={asset['status']}")

            # ---------------------------------------------------------------
            Step.start("Worker inspects the media with FFprobe")
            await assets.report_stage(asset_id=asset_id, stage=ProcessingStage.PREPARING_FILE)
            await assets.update_technical_metadata(
                organization_id=ORG,
                asset_id=asset_id,
                duration_ms=71_000,
                width=1920,
                height=1080,
                orientation="horizontal",
                checksum_sha256="9f2c" + "0" * 60,
            )
            asset = (await user_http.get(f"/api/v1/assets/{asset_id}")).json()
            Step.ok(
                f"duration={asset['duration_ms']}ms  {asset['width']}x{asset['height']}  "
                f"status={asset['status']}"
            )

            # ---------------------------------------------------------------
            Step.start("Worker generates proxy and thumbnail derivatives")
            proxy_key = build_storage_key(
                StorageKeyKind.PROXY, organization_id=ORG, asset_id=asset_id
            )
            thumb_key = build_storage_key(
                StorageKeyKind.THUMBNAIL_MAIN, organization_id=ORG, asset_id=asset_id
            )
            await assets.report_stage(asset_id=asset_id, stage=ProcessingStage.GENERATING_PREVIEW)
            await assets.update_storage_keys(
                organization_id=ORG,
                asset_id=asset_id,
                proxy_storage_key=proxy_key,
                thumbnail_storage_key=thumb_key,
            )
            playback = await user_http.get(f"/api/v1/assets/{asset_id}/playback-url")
            Step.ok(f"proxy key   {proxy_key}")
            Step.ok(f"playback URL {playback.json()['url'][:78]}…")

            # ---------------------------------------------------------------
            Step.start("Worker transcribes speech and persists segments")
            await assets.report_stage(asset_id=asset_id, stage=ProcessingStage.TRANSCRIBING_SPEECH)
            segments = [
                TranscriptSegmentDraft(
                    asset_id=asset_id,
                    organization_id=ORG,
                    sequence_number=i,
                    start_ms=start,
                    end_ms=end,
                    text_original=text,
                    text_normalized=text.lower(),
                    provider="null-provider",
                    analysis_version=ANALYSIS_VERSION,
                    speaker_label=speaker,
                )
                for i, (start, end, text, speaker) in enumerate(TRANSCRIPT)
            ]
            count = await transcripts.replace_segments(
                organization_id=ORG,
                asset_id=asset_id,
                analysis_version=ANALYSIS_VERSION,
                segments=segments,
            )
            Step.ok(f"{count} transcript segments persisted")

            # ---------------------------------------------------------------
            Step.start("Worker segments the transcript into candidate moments")
            candidates = generate_candidate_moments(segments)
            for c in candidates:
                Step.info(
                    f"{c.start_ms / 1000:6.1f}s - {c.end_ms / 1000:6.1f}s  "
                    f"({c.duration_ms / 1000:4.1f}s)  {c.transcript_text[:52]}…"
                )
            Step.ok(f"{len(candidates)} candidate moments from {len(segments)} segments")

            # ---------------------------------------------------------------
            Step.start("Worker classifies each moment and persists them")
            await assets.report_stage(asset_id=asset_id, stage=ProcessingStage.IDENTIFYING_MOMENTS)
            classifier = NullClassificationProvider()
            drafts: list[MediaMomentDraft] = []
            for i, candidate in enumerate(candidates):
                classification = await classifier.classify_moment(
                    MomentClassificationInput(
                        transcript_text=candidate.transcript_text,
                        neighboring_context="",
                        visual_description="",
                        start_ms=candidate.start_ms,
                        end_ms=candidate.end_ms,
                        asset_title="Customer interview Q3",
                        language="en",
                    )
                )
                drafts.append(
                    MediaMomentDraft(
                        asset_id=asset_id,
                        organization_id=ORG,
                        sequence_number=i,
                        start_ms=candidate.start_ms,
                        end_ms=candidate.end_ms,
                        moment_type=MomentType.SPEECH_SEGMENT,
                        title=candidate.transcript_text[:60],
                        visual_description=classification.visual_description,
                        marketing_description=classification.marketing_description,
                        analysis_version=ANALYSIS_VERSION,
                        transcript_text=candidate.transcript_text,
                        content_types=["testimonial"],
                        technical_quality_score=0.85,
                    )
                )
            await assets.report_stage(asset_id=asset_id, stage=ProcessingStage.PREPARING_SEARCH)
            persisted = await moments_repo.upsert_moments(
                organization_id=ORG,
                asset_id=asset_id,
                analysis_version=ANALYSIS_VERSION,
                moments=drafts,
            )
            Step.ok(f"{persisted} moments persisted through the internal endpoint")

            # ---------------------------------------------------------------
            Step.start("Worker finalizes the asset")
            await jobs.mark_completed(organization_id=ORG, job_id=asset_id)
            asset = (await user_http.get(f"/api/v1/assets/{asset_id}")).json()
            job = (await user_http.get(f"/api/v1/assets/{asset_id}/processing-job")).json()
            Step.ok(f"asset status={asset['status']}  job={job['status']} @ {job['progress']}%")

            # ---------------------------------------------------------------
            Step.start("Frontend reads transcript and moments back (Member A's views)")
            transcript_out = (
                await user_http.get(f"/api/v1/assets/{asset_id}/transcript")
            ).json()
            moments_out = (await user_http.get(f"/api/v1/assets/{asset_id}/moments")).json()
            Step.ok(f"{len(transcript_out)} transcript rows, {len(moments_out)} moments readable")
            for m in moments_out[:3]:
                Step.info(f"{m['start_ms'] / 1000:6.1f}s  [{m['category']}] {m['title'][:48]}…")

            # ---------------------------------------------------------------
            Step.start("Natural-language search finds the moment")
            query = "implementation"
            results = (
                await user_http.post(
                    "/api/v1/search", json={"query": query, "organization_id": ORG}
                )
            ).json()
            hits = [r for r in results["results"] if r["asset_id"] == asset_id]
            Step.ok(f'query "{query}" -> {len(hits)} hit(s) in this asset')
            for hit in hits[:2]:
                Step.info(
                    f"{hit['start_ms'] / 1000:6.1f}s  score={hit['score']}  "
                    f"{hit['excerpt'][:50]}…"
                )

            # ---------------------------------------------------------------
            Step.start("Reprocessing the same asset is idempotent")
            await transcripts.replace_segments(
                organization_id=ORG,
                asset_id=asset_id,
                analysis_version=ANALYSIS_VERSION,
                segments=segments,
            )
            await moments_repo.upsert_moments(
                organization_id=ORG,
                asset_id=asset_id,
                analysis_version=ANALYSIS_VERSION,
                moments=drafts,
            )
            after_transcript = (
                await user_http.get(f"/api/v1/assets/{asset_id}/transcript")
            ).json()
            after_moments = (await user_http.get(f"/api/v1/assets/{asset_id}/moments")).json()
            same = len(after_transcript) == len(transcript_out) and len(after_moments) == len(
                moments_out
            )
            Step.ok(
                f"after re-run: {len(after_transcript)} segments, {len(after_moments)} moments "
                f"({'no duplicates' if same else 'DUPLICATED'})"
            )
            if not same:
                return 1

    print("\n\033[1;32mPipeline demo completed successfully.\033[0m\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
