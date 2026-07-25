"""FFprobe-based media inspection (spec section 11.3, ``inspect_media``)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from worker.domain.enums import Orientation
from worker.logging import get_logger

logger = get_logger(__name__)


class FFprobeError(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaProbe:
    duration_ms: int | None
    width: int | None
    height: int | None
    orientation: Orientation
    has_video: bool
    has_audio: bool
    video_codec: str | None
    audio_codec: str | None
    frame_rate: float | None
    container_format: str | None


def _orientation_for(width: int | None, height: int | None) -> Orientation:
    if not width or not height:
        return Orientation.UNKNOWN
    if width == height:
        return Orientation.SQUARE
    return Orientation.HORIZONTAL if width > height else Orientation.VERTICAL


def _parse_frame_rate(raw: str | None) -> float | None:
    if not raw or raw == "0/0":
        return None
    if "/" in raw:
        numerator, _, denominator = raw.partition("/")
        try:
            denom = float(denominator)
            return float(numerator) / denom if denom else None
        except ValueError:
            return None
    try:
        return float(raw)
    except ValueError:
        return None


async def inspect_media(media_path: str, *, timeout_seconds: float = 60.0) -> MediaProbe:
    """Run ffprobe against a local file and return structured metadata."""

    process = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        media_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise FFprobeError(f"ffprobe timed out inspecting {media_path}") from exc

    if process.returncode != 0:
        raise FFprobeError(
            f"ffprobe failed for {media_path} (exit {process.returncode}): "
            f"{stderr.decode(errors='replace')}"
        )

    payload = json.loads(stdout.decode())
    streams = payload.get("streams", [])
    fmt = payload.get("format", {})

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration_raw = fmt.get("duration") or (video_stream or {}).get("duration")
    duration_ms = int(round(float(duration_raw) * 1000)) if duration_raw else None

    width = video_stream.get("width") if video_stream else None
    height = video_stream.get("height") if video_stream else None

    probe = MediaProbe(
        duration_ms=duration_ms,
        width=width,
        height=height,
        orientation=_orientation_for(width, height),
        has_video=video_stream is not None,
        has_audio=audio_stream is not None,
        video_codec=video_stream.get("codec_name") if video_stream else None,
        audio_codec=audio_stream.get("codec_name") if audio_stream else None,
        frame_rate=_parse_frame_rate(video_stream.get("r_frame_rate")) if video_stream else None,
        container_format=fmt.get("format_name"),
    )

    logger.info(
        "ffprobe.inspected",
        media_path=media_path,
        duration_ms=probe.duration_ms,
        width=probe.width,
        height=probe.height,
        has_audio=probe.has_audio,
    )
    return probe
