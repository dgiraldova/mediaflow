"""FFmpeg-based proxy, thumbnail, audio and clip generation.

Specs: proxy (12.2), thumbnails (12.3), clip export (12.4).

Every long-running ffmpeg invocation accepts an optional async
``on_progress`` callback driven by ffmpeg's ``-progress`` machine-readable
output, so a Temporal activity can call ``activity.heartbeat()`` while
FFmpeg runs (spec 11.3/19: "Worker heartbeats during long FFmpeg jobs").
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from worker.logging import get_logger

logger = get_logger(__name__)

ProgressCallback = Callable[[int], Awaitable[None]]

PROXY_MAX_WIDTH = 1280
PROXY_MAX_FRAME_RATE = 30
CLIP_MAX_DURATION_MS = 5 * 60 * 1000
PREVIEW_THUMBNAIL_COUNT = 5


class FFmpegError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProxyResult:
    output_path: str
    width: int
    height: int


@dataclass(frozen=True)
class ThumbnailResult:
    main_path: str
    preview_paths: list[str]


@dataclass(frozen=True)
class ClipExtractionResult:
    output_path: str
    duration_ms: int


async def _run_ffmpeg(
    args: list[str],
    *,
    total_duration_ms: int | None = None,
    on_progress: ProgressCallback | None = None,
    timeout_seconds: float = 1800.0,
) -> None:
    cmd = ["ffmpeg", "-y", "-nostdin", *args]
    if on_progress is not None:
        cmd = [*cmd[:1], "-progress", "pipe:1", "-loglevel", "error", *cmd[1:]]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE if on_progress else asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )

    async def _drain_progress() -> None:
        assert process.stdout is not None
        async for raw_line in process.stdout:
            line = raw_line.decode(errors="replace").strip()
            if not line.startswith("out_time_ms="):
                continue
            try:
                out_time_ms = int(line.split("=", 1)[1]) // 1000
            except ValueError:
                continue
            if on_progress is not None:
                await on_progress(out_time_ms)

    try:
        if on_progress is not None:
            await asyncio.wait_for(
                asyncio.gather(_drain_progress(), process.wait()), timeout=timeout_seconds
            )
        else:
            await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise FFmpegError(f"ffmpeg timed out running: {' '.join(cmd)}") from exc

    if process.returncode != 0:
        stderr = b""
        if process.stderr is not None:
            stderr = await process.stderr.read()
        raise FFmpegError(
            f"ffmpeg failed (exit {process.returncode}) for command: {' '.join(cmd)}\n"
            f"{stderr.decode(errors='replace')}"
        )


async def generate_proxy(
    *,
    input_path: str,
    output_path: str,
    on_progress: ProgressCallback | None = None,
    total_duration_ms: int | None = None,
) -> ProxyResult:
    """Standard H.264/AAC MP4 playback proxy, max width 1280, up to 30 FPS."""

    scale_filter = f"scale='min({PROXY_MAX_WIDTH},iw)':-2"
    fps_filter = f"fps='min({PROXY_MAX_FRAME_RATE},source_fps)'"

    await _run_ffmpeg(
        [
            "-i",
            input_path,
            "-vf",
            f"{scale_filter},{fps_filter}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            output_path,
        ],
        on_progress=on_progress,
        total_duration_ms=total_duration_ms,
    )

    from worker.media.ffprobe import inspect_media

    probe = await inspect_media(output_path)
    return ProxyResult(
        output_path=output_path,
        width=probe.width or 0,
        height=probe.height or 0,
    )


async def generate_thumbnails(
    *,
    input_path: str,
    output_dir: str,
    duration_ms: int,
    preview_count: int = PREVIEW_THUMBNAIL_COUNT,
) -> ThumbnailResult:
    """Main thumbnail + evenly spaced preview thumbnails (spec 12.3).

    Main-thumbnail selection uses ffmpeg's ``thumbnail`` filter, which
    scores frames within a sampling window and picks the most
    "representative" one — a practical approximation of "exclude dark or
    blurred frames" without a separate vision pass. Preferring a frame
    with a visible person or product requires the video-intelligence
    provider's output and is intentionally deferred; the fallback below
    (~20% of duration) still applies if the filter fails outright.
    """

    os.makedirs(output_dir, exist_ok=True)
    main_path = os.path.join(output_dir, "main.jpg")

    try:
        await _run_ffmpeg(
            [
                "-i",
                input_path,
                "-vf",
                "thumbnail=300,scale=1280:-2",
                "-frames:v",
                "1",
                main_path,
            ]
        )
    except FFmpegError:
        logger.warning("ffmpeg.thumbnail_filter_failed_falling_back", input_path=input_path)
        fallback_ms = max(0, int(duration_ms * 0.2))
        await _run_ffmpeg(
            [
                "-ss",
                f"{fallback_ms / 1000:.3f}",
                "-i",
                input_path,
                "-vf",
                "scale=1280:-2",
                "-frames:v",
                "1",
                main_path,
            ]
        )

    preview_paths: list[str] = []
    if duration_ms > 0 and preview_count > 0:
        step_ms = duration_ms / (preview_count + 1)
        for index in range(1, preview_count + 1):
            timestamp_ms = int(step_ms * index)
            preview_path = os.path.join(output_dir, f"preview-{index:03d}.jpg")
            await _run_ffmpeg(
                [
                    "-ss",
                    f"{timestamp_ms / 1000:.3f}",
                    "-i",
                    input_path,
                    "-vf",
                    "scale=640:-2",
                    "-frames:v",
                    "1",
                    preview_path,
                ]
            )
            preview_paths.append(preview_path)

    return ThumbnailResult(main_path=main_path, preview_paths=preview_paths)


async def extract_audio(
    *,
    input_path: str,
    output_path: str,
    on_progress: ProgressCallback | None = None,
) -> str:
    """Extract mono 16 kHz WAV audio suitable for transcription providers."""

    await _run_ffmpeg(
        [
            "-i",
            input_path,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            output_path,
        ],
        on_progress=on_progress,
    )
    return output_path


async def extract_clip(
    *,
    input_path: str,
    output_path: str,
    start_ms: int,
    end_ms: int,
) -> ClipExtractionResult:
    """Accurate-cut MP4/H.264 clip export (spec 12.4).

    Re-encodes rather than stream-copying so the cut lands exactly on
    ``start_ms``/``end_ms`` instead of the nearest keyframe. No reframing,
    captions or music are applied; the source aspect ratio is preserved.
    """

    duration_ms = end_ms - start_ms
    if duration_ms <= 0:
        raise ValueError("end_ms must be greater than start_ms")
    if duration_ms > CLIP_MAX_DURATION_MS:
        raise ValueError(
            f"Clip duration {duration_ms}ms exceeds the maximum of {CLIP_MAX_DURATION_MS}ms"
        )

    await _run_ffmpeg(
        [
            "-ss",
            f"{start_ms / 1000:.3f}",
            "-i",
            input_path,
            "-t",
            f"{duration_ms / 1000:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            output_path,
        ]
    )

    return ClipExtractionResult(output_path=output_path, duration_ms=duration_ms)
