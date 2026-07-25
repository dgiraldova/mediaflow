"""Translation between the worker's domain model and Member B's API schema.

The API that Member B shipped (``app/main.py``) is flatter than the schema in
the spec: moments carry a single ``category`` and an integer ``score`` rather
than the full marketing taxonomy, transcripts carry ``speaker``/``text`` only,
and there is no ``analysis_version`` or ``organization_id`` on the internal
worker endpoints.

Rather than degrade the worker's internal model to match, this module is the
one place that lossily maps down to the wire format. The richer fields stay
available in ``worker.domain.models`` for when the API schema catches up.
"""

from __future__ import annotations

import uuid

from worker.domain.models import MediaMomentDraft, TranscriptSegmentDraft

# Limits enforced by Member B's Pydantic models; exceeding them is a 422.
MAX_TRANSCRIPT_SEGMENTS = 2_000
MAX_TRANSCRIPT_TEXT_CHARS = 2_000
MAX_MOMENTS = 500
MAX_MOMENT_TITLE_CHARS = 255
MAX_CATEGORY_CHARS = 100
MAX_SPEAKER_CHARS = 120

# Stable namespace for deriving deterministic moment ids. Changing this value
# would orphan every previously persisted moment, so treat it as frozen.
MOMENT_ID_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")

# Worker asset statuses -> the four statuses Member B's API accepts.
WORKER_STATUS_TO_API = {
    "discovered": "queued",
    "uploading": "queued",
    "queued": "queued",
    "processing": "processing",
    "ready": "completed",
    "failed": "failed",
    "deleted": "failed",
}


def moment_wire_id(
    *, asset_id: str, start_ms: int, end_ms: int, moment_type: str
) -> str:
    """Derive a stable moment id from its natural key.

    Member B's endpoint upserts on ``id``, so deriving the id deterministically
    from the moment's boundaries is what makes reprocessing idempotent: the
    same moment resolves to the same row instead of accumulating duplicates
    (spec section 6.5). The result is a 36-character UUID string, matching the
    API's length limit.
    """
    natural_key = f"{asset_id}:{start_ms}:{end_ms}:{moment_type}"
    return str(uuid.uuid5(MOMENT_ID_NAMESPACE, natural_key))


def moment_category(moment: MediaMomentDraft) -> str:
    """Collapse the content-type list into the API's single category field."""
    category = moment.content_types[0] if moment.content_types else "other"
    return category[:MAX_CATEGORY_CHARS]


def moment_score(moment: MediaMomentDraft) -> int:
    """Map the 0.0-1.0 quality score onto the API's 0-100 integer scale."""
    if moment.technical_quality_score is None:
        return 50
    clamped = max(0.0, min(1.0, moment.technical_quality_score))
    return int(round(clamped * 100))


def transcript_segment_to_wire(segment: TranscriptSegmentDraft) -> dict[str, object] | None:
    """Convert one transcript segment, or None if the API would reject it."""
    text = segment.text_original.strip()[:MAX_TRANSCRIPT_TEXT_CHARS]
    if not text:
        return None  # the API requires min_length=1
    if segment.end_ms <= segment.start_ms:
        return None  # the API rejects non-positive durations
    return {
        "start_ms": segment.start_ms,
        "end_ms": segment.end_ms,
        "speaker": segment.speaker_label[:MAX_SPEAKER_CHARS] if segment.speaker_label else None,
        "text": text,
    }


def transcript_segments_to_wire(
    segments: list[TranscriptSegmentDraft],
) -> list[dict[str, object]]:
    wire = [w for w in (transcript_segment_to_wire(s) for s in segments) if w is not None]
    return wire[:MAX_TRANSCRIPT_SEGMENTS]


def moment_to_wire(moment: MediaMomentDraft) -> dict[str, object] | None:
    """Convert one moment, or None if the API would reject it."""
    if moment.end_ms <= moment.start_ms:
        return None
    title = (moment.title or moment.marketing_description or "Untitled moment").strip()
    return {
        "id": moment_wire_id(
            asset_id=moment.asset_id,
            start_ms=moment.start_ms,
            end_ms=moment.end_ms,
            moment_type=moment.moment_type.value,
        ),
        "title": title[:MAX_MOMENT_TITLE_CHARS],
        "start_ms": moment.start_ms,
        "end_ms": moment.end_ms,
        "category": moment_category(moment),
        "score": moment_score(moment),
    }


def moments_to_wire(moments: list[MediaMomentDraft]) -> list[dict[str, object]]:
    wire = [w for w in (moment_to_wire(m) for m in moments) if w is not None]
    return wire[:MAX_MOMENTS]
