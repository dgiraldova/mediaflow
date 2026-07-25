"""Transcription provider interface (spec section 13.1).

The domain/activity layer depends only on ``TranscriptionProvider``. Never
import a concrete vendor SDK outside this module (spec section 6.4 and
AGENTS.md rule "never call AI providers directly").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from openai import AsyncOpenAI

from worker.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class TranscriptSegmentResult:
    start_ms: int
    end_ms: int
    text: str
    speaker_label: str | None
    language: str | None
    confidence: float | None


class TranscriptionProvider(Protocol):
    async def transcribe(
        self,
        *,
        audio_path: str,
        language_hint: str | None = None,
    ) -> list[TranscriptSegmentResult]: ...


class OpenAICompatibleTranscriptionProvider:
    """Adapter for any OpenAI-compatible timestamped transcription API.

    Works with OpenAI's own API and OpenAI-compatible endpoints that
    implement ``audio.transcriptions`` with ``verbose_json`` segment
    timestamps, matching the "OpenAI-compatible transcription service"
    choice in spec section 5.7.
    """

    def __init__(self, *, api_key: str, model: str, base_url: str | None = None) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    async def transcribe(
        self,
        *,
        audio_path: str,
        language_hint: str | None = None,
    ) -> list[TranscriptSegmentResult]:
        with open(audio_path, "rb") as audio_file:
            request: dict[str, Any] = {
                "model": self._model,
                "file": audio_file,
                "response_format": "verbose_json",
                "timestamp_granularities": ["segment"],
            }
            if language_hint:
                request["language"] = language_hint
            response: Any = await self._client.audio.transcriptions.create(**request)

        segments = getattr(response, "segments", None) or []
        results: list[TranscriptSegmentResult] = []
        for segment in segments:
            start_ms = int(round(float(_get(segment, "start", 0.0)) * 1000))
            end_ms = int(round(float(_get(segment, "end", 0.0)) * 1000))
            text = str(_get(segment, "text", "")).strip()
            if not text:
                continue
            avg_logprob = _get(segment, "avg_logprob", None)
            confidence = (
                _confidence_from_logprob(float(avg_logprob))
                if avg_logprob is not None
                else None
            )
            response_language = getattr(response, "language", None)
            results.append(
                TranscriptSegmentResult(
                    start_ms=start_ms,
                    end_ms=end_ms,
                    text=text,
                    speaker_label=None,
                    language=language_hint
                    or (str(response_language) if response_language else None),
                    confidence=confidence,
                )
            )

        logger.info(
            "transcription.completed",
            audio_path=audio_path,
            segment_count=len(results),
            model=self._model,
        )
        return results


class NullTranscriptionProvider:
    """No-op provider for local development and tests without an API key."""

    async def transcribe(
        self,
        *,
        audio_path: str,
        language_hint: str | None = None,
    ) -> list[TranscriptSegmentResult]:
        logger.warning("transcription.null_provider_used", audio_path=audio_path)
        return []


def _get(obj: object, key: str, default: Any) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _confidence_from_logprob(avg_logprob: float) -> float:
    """Map an average log-probability to an approximate [0, 1] confidence."""
    import math

    return max(0.0, min(1.0, math.exp(avg_logprob)))
