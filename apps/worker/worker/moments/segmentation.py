"""Candidate moment generation (spec section 14).

Do not depend on one segmentation method. This module builds candidates
primarily from transcript boundaries and speaker changes, with silence
gaps treated as an additional boundary signal and an optional hook for
visual scene-change timestamps (from ffmpeg's ``scdet``/``select`` filters
or the video-intelligence provider) when available. Video-provider search
segments are a separate, query-time signal (spec section 15) and are not
produced here.

The output is a list of *candidates* — the marketing-classification step
(spec 14.2) still decides what each one is about; this module only decides
*where the boundaries are*.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

MIN_MOMENT_MS = 3_000
PREFERRED_MIN_MS = 10_000
PREFERRED_MAX_MS = 45_000
MAX_SEMANTIC_MOMENT_MS = 90_000
SILENCE_BOUNDARY_GAP_MS = 1_200


class TranscriptSegmentLike(Protocol):
    start_ms: int
    end_ms: int
    speaker_label: str | None


def segment_text(segment: TranscriptSegmentLike) -> str:
    """Read a segment's text regardless of which representation it is.

    Two shapes reach this module: ``TranscriptSegmentPayload`` (``text``),
    which crosses the Temporal activity boundary, and
    ``TranscriptSegmentDraft`` (``text_original``), the domain model used when
    calling segmentation directly. Accepting both keeps callers from having to
    convert just to find moment boundaries.
    """
    text = getattr(segment, "text", None)
    if text is None:
        text = getattr(segment, "text_original", "")
    return text or ""


@dataclass
class CandidateMoment:
    start_ms: int
    end_ms: int
    transcript_text: str
    speaker_labels: list[str] = field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


def generate_candidate_moments(
    transcript_segments: Sequence[TranscriptSegmentLike],
    *,
    min_moment_ms: int = MIN_MOMENT_MS,
    preferred_max_ms: int = PREFERRED_MAX_MS,
    max_semantic_moment_ms: int = MAX_SEMANTIC_MOMENT_MS,
    silence_boundary_gap_ms: int = SILENCE_BOUNDARY_GAP_MS,
    scene_change_ms: list[int] | None = None,
) -> list[CandidateMoment]:
    if not transcript_segments:
        return []

    ordered = sorted(transcript_segments, key=lambda s: s.start_ms)
    scene_changes = sorted(scene_change_ms or [])

    groups: list[list[TranscriptSegmentLike]] = []
    current: list[TranscriptSegmentLike] = [ordered[0]]

    for segment in ordered[1:]:
        previous = current[-1]
        gap_ms = segment.start_ms - previous.end_ms
        prospective_duration_ms = segment.end_ms - current[0].start_ms
        speaker_changed = (
            previous.speaker_label is not None
            and segment.speaker_label is not None
            and previous.speaker_label != segment.speaker_label
        )
        crosses_scene_change = any(previous.end_ms <= t < segment.start_ms for t in scene_changes)

        boundary = (
            gap_ms >= silence_boundary_gap_ms
            or speaker_changed
            or crosses_scene_change
            or prospective_duration_ms > max_semantic_moment_ms
        )

        if boundary:
            groups.append(current)
            current = [segment]
        else:
            current.append(segment)

    groups.append(current)

    candidates = [_split_oversized(group, max_semantic_moment_ms) for group in groups]
    flattened = [c for group in candidates for c in group]
    return _merge_undersized(flattened, min_moment_ms=min_moment_ms)


def _to_candidate(group: list[TranscriptSegmentLike]) -> CandidateMoment:
    text = " ".join(t for t in (segment_text(s).strip() for s in group) if t)
    speakers = sorted({s.speaker_label for s in group if s.speaker_label})
    return CandidateMoment(
        start_ms=group[0].start_ms,
        end_ms=group[-1].end_ms,
        transcript_text=text,
        speaker_labels=speakers,
    )


def _split_oversized(
    group: list[TranscriptSegmentLike], max_semantic_moment_ms: int
) -> list[CandidateMoment]:
    """Split a group that grew past the max semantic length into chunks."""
    chunks: list[CandidateMoment] = []
    chunk: list[TranscriptSegmentLike] = []
    chunk_start = group[0].start_ms

    for segment in group:
        if chunk and (segment.end_ms - chunk_start) > max_semantic_moment_ms:
            chunks.append(_to_candidate(chunk))
            chunk = []
            chunk_start = segment.start_ms
        chunk.append(segment)

    if chunk:
        chunks.append(_to_candidate(chunk))
    return chunks


def _merge_undersized(
    candidates: list[CandidateMoment], *, min_moment_ms: int
) -> list[CandidateMoment]:
    """Merge adjacent transcript segments when they express one idea.

    In practice this means: any candidate shorter than the minimum moment
    length gets absorbed into its nearest neighbor rather than surfaced as
    a standalone, too-short moment.
    """
    if len(candidates) <= 1:
        return candidates

    merged: list[CandidateMoment] = []
    for candidate in candidates:
        if merged and candidate.duration_ms < min_moment_ms:
            previous = merged[-1]
            merged[-1] = CandidateMoment(
                start_ms=previous.start_ms,
                end_ms=candidate.end_ms,
                transcript_text=f"{previous.transcript_text} {candidate.transcript_text}".strip(),
                speaker_labels=sorted(set(previous.speaker_labels) | set(candidate.speaker_labels)),
            )
        else:
            merged.append(candidate)

    # A final undersized candidate has no later neighbor to merge into;
    # fold it backward instead.
    if len(merged) > 1 and merged[-1].duration_ms < min_moment_ms:
        last = merged.pop()
        previous = merged[-1]
        merged[-1] = CandidateMoment(
            start_ms=previous.start_ms,
            end_ms=last.end_ms,
            transcript_text=f"{previous.transcript_text} {last.transcript_text}".strip(),
            speaker_labels=sorted(set(previous.speaker_labels) | set(last.speaker_labels)),
        )

    return merged
