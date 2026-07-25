from __future__ import annotations

from dataclasses import dataclass

from worker.moments.segmentation import (
    MAX_SEMANTIC_MOMENT_MS,
    MIN_MOMENT_MS,
    generate_candidate_moments,
)


@dataclass
class Segment:
    start_ms: int
    end_ms: int
    text: str
    speaker_label: str | None = None


def test_empty_transcript_produces_no_candidates():
    assert generate_candidate_moments([]) == []


def test_contiguous_speech_merges_into_one_candidate():
    segments = [
        Segment(0, 5_000, "We started the company in 2019."),
        Segment(5_100, 11_000, "Our first customer was a small agency."),
        Segment(11_050, 16_000, "They needed a faster way to find footage."),
    ]
    candidates = generate_candidate_moments(segments)
    assert len(candidates) == 1
    assert candidates[0].start_ms == 0
    assert candidates[0].end_ms == 16_000
    assert "small agency" in candidates[0].transcript_text


def test_long_silence_gap_creates_a_boundary():
    segments = [
        Segment(0, 8_000, "First topic entirely."),
        Segment(20_000, 30_000, "A completely different topic."),
    ]
    candidates = generate_candidate_moments(segments)
    assert len(candidates) == 2


def test_speaker_change_creates_a_boundary():
    segments = [
        Segment(0, 8_000, "Tell me about the rollout.", speaker_label="interviewer"),
        Segment(
            8_100, 18_000, "Implementation was easier than expected.", speaker_label="customer"
        ),
    ]
    candidates = generate_candidate_moments(segments)
    assert len(candidates) == 2
    assert candidates[1].speaker_labels == ["customer"]


def test_scene_change_creates_a_boundary():
    segments = [
        Segment(0, 8_000, "Here is the dashboard."),
        Segment(8_100, 16_000, "And here is the mobile view."),
    ]
    without = generate_candidate_moments(segments)
    with_scene = generate_candidate_moments(segments, scene_change_ms=[8_050])
    assert len(without) == 1
    assert len(with_scene) == 2


def test_no_candidate_exceeds_max_semantic_length():
    segments = [Segment(i * 10_000, (i + 1) * 10_000 - 50, f"Sentence {i}.") for i in range(30)]
    candidates = generate_candidate_moments(segments)
    assert candidates
    for candidate in candidates:
        assert candidate.duration_ms <= MAX_SEMANTIC_MOMENT_MS


def test_undersized_candidates_are_merged_away():
    segments = [
        Segment(0, 12_000, "A full thought that stands on its own."),
        Segment(30_000, 31_000, "Yeah."),
    ]
    candidates = generate_candidate_moments(segments)
    # The 1s "Yeah." is below the 3s minimum, so it folds into its neighbor
    # rather than surfacing as a standalone moment.
    assert len(candidates) == 1
    assert candidates[0].duration_ms >= MIN_MOMENT_MS
    assert "Yeah." in candidates[0].transcript_text


def test_accepts_domain_transcript_drafts_not_just_activity_payloads():
    """Segmentation must work with TranscriptSegmentDraft (text_original)."""
    from worker.domain.models import TranscriptSegmentDraft

    drafts = [
        TranscriptSegmentDraft(
            asset_id="a",
            organization_id="o",
            sequence_number=i,
            start_ms=start,
            end_ms=end,
            text_original=text,
            text_normalized=text.lower(),
            provider="test",
            analysis_version="v1",
            speaker_label="customer",
        )
        for i, (start, end, text) in enumerate(
            [(0, 6_000, "First thought."), (6_100, 15_000, "Second thought.")]
        )
    ]
    candidates = generate_candidate_moments(drafts)
    assert len(candidates) == 1
    assert "First thought." in candidates[0].transcript_text
    assert "Second thought." in candidates[0].transcript_text


def test_candidates_are_returned_in_chronological_order():
    segments = [
        Segment(30_000, 40_000, "Third."),
        Segment(0, 10_000, "First."),
        Segment(15_000, 25_000, "Second."),
    ]
    candidates = generate_candidate_moments(segments)
    starts = [c.start_ms for c in candidates]
    assert starts == sorted(starts)
