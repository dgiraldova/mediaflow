"""Clip-export activities (spec section 12.4, MVP1-072).

Renders a user-selected time range from an already-processed asset into a
standalone MP4. Cuts are accurate rather than keyframe-aligned, so the clip
starts exactly where the user asked (spec 12.4: "Use accurate cuts, even when
re-encoding is necessary").

The clip is rendered from the *proxy*, not the original: the proxy is already
H.264/AAC at a sane resolution, so re-encoding it is fast and the output is
immediately web-playable. Exporting from the original would mean pulling a
multi-GB source for a 30-second clip.
"""

from __future__ import annotations

import os
import shutil

from temporalio import activity
from temporalio.exceptions import ApplicationError

from worker.activities.deps import ActivityDependencies
from worker.activities.types import (
    MarkClipExportFailedInput,
    MarkClipExportReadyInput,
    RenderClipInput,
    RenderClipResult,
)
from worker.logging import get_logger
from worker.media.ffmpeg import CLIP_MAX_DURATION_MS, extract_clip
from worker.storage.keys import StorageKeyKind, build_storage_key

logger = get_logger(__name__)

CLIP_MIME_TYPE = "video/mp4"


class ExportActivities:
    def __init__(self, deps: ActivityDependencies) -> None:
        self._deps = deps

    def _temp_dir(self, clip_export_id: str) -> str:
        path = os.path.join(self._deps.settings.temp_dir, "exports", clip_export_id)
        os.makedirs(path, exist_ok=True)
        return path

    @activity.defn(name="validate_clip_range")
    async def validate_clip_range(self, args: RenderClipInput) -> None:
        duration_ms = args.end_ms - args.start_ms
        if duration_ms <= 0:
            raise ApplicationError(
                "Clip end time must be after its start time.",
                type="invalid_clip_range",
                non_retryable=True,
            )
        if duration_ms > CLIP_MAX_DURATION_MS:
            raise ApplicationError(
                f"Clips are limited to {CLIP_MAX_DURATION_MS // 60_000} minutes.",
                type="clip_too_long",
                non_retryable=True,
            )

    @activity.defn(name="render_clip")
    async def render_clip(self, args: RenderClipInput) -> RenderClipResult:
        temp_dir = self._temp_dir(args.clip_export_id)
        source_path = os.path.join(temp_dir, "source.mp4")
        output_path = os.path.join(temp_dir, "clip.mp4")

        source = await self._deps.storage.head_object(key=args.source_storage_key)
        if source is None:
            raise ApplicationError(
                "The source video is no longer available in storage.",
                type="source_missing",
                non_retryable=True,
            )

        await self._deps.storage.download_to_path(
            key=args.source_storage_key, destination_path=source_path
        )
        activity.heartbeat("downloaded")

        result = await extract_clip(
            input_path=source_path,
            output_path=output_path,
            start_ms=args.start_ms,
            end_ms=args.end_ms,
        )
        activity.heartbeat("rendered")

        output_key = build_storage_key(
            StorageKeyKind.CLIP_EXPORT,
            organization_id=args.organization_id,
            clip_export_id=args.clip_export_id,
        )
        uploaded = await self._deps.storage.upload_from_path(
            key=output_key, source_path=result.output_path, content_type=CLIP_MIME_TYPE
        )

        logger.info(
            "clip.rendered",
            clip_export_id=args.clip_export_id,
            duration_ms=result.duration_ms,
            byte_size=uploaded.byte_size,
        )
        return RenderClipResult(
            output_storage_key=output_key,
            duration_ms=result.duration_ms,
            byte_size=uploaded.byte_size,
        )

    @activity.defn(name="mark_clip_export_ready")
    async def mark_clip_export_ready(self, args: MarkClipExportReadyInput) -> None:
        repository = self._deps.clip_export_repository
        if repository is None:
            logger.warning(
                "clip_export.repository_unavailable",
                clip_export_id=args.clip_export_id,
                output_storage_key=args.output_storage_key,
            )
            return
        await repository.mark_ready(
            organization_id=args.organization_id,
            export_id=args.clip_export_id,
            output_storage_key=args.output_storage_key,
            output_mime_type=args.output_mime_type,
        )

    @activity.defn(name="mark_clip_export_failed")
    async def mark_clip_export_failed(self, args: MarkClipExportFailedInput) -> None:
        repository = self._deps.clip_export_repository
        if repository is None:
            logger.warning(
                "clip_export.repository_unavailable",
                clip_export_id=args.clip_export_id,
                error_message=args.error_message,
            )
            return
        await repository.mark_failed(
            organization_id=args.organization_id,
            export_id=args.clip_export_id,
            error_message=args.error_message,
        )

    @activity.defn(name="cleanup_export_files")
    async def cleanup_export_files(self, clip_export_id: str) -> None:
        shutil.rmtree(self._temp_dir(clip_export_id), ignore_errors=True)
