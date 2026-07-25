"""Tests for Drive sync planning and duplicate detection (MVP1-063, MVP1-064)."""

from __future__ import annotations

from worker.google_drive.client import DriveFile
from worker.google_drive.sync import KnownAsset, build_sync_plan, drive_checksum_key


def drive_file(
    *,
    file_id: str = "f1",
    name: str = "interview.mp4",
    mime_type: str = "video/mp4",
    size_bytes: int = 5_000_000,
    modified_time: str = "2026-07-01T10:00:00.000Z",
    md5: str | None = "abc123",
) -> DriveFile:
    return DriveFile(
        id=file_id,
        name=name,
        mime_type=mime_type,
        size_bytes=size_bytes,
        modified_time=modified_time,
        md5_checksum=md5,
    )


def test_new_files_are_ingested():
    plan = build_sync_plan([drive_file()], [])
    assert plan.ingest_count == 1
    assert plan.to_ingest[0].reason == "new_file"


def test_already_imported_file_is_skipped():
    known = [KnownAsset(source_external_id="f1", checksum=drive_checksum_key("abc123"))]
    plan = build_sync_plan([drive_file()], known)
    assert plan.ingest_count == 0
    assert plan.skipped[0].reason == "already_imported"


def test_modified_source_is_reingested():
    known = [KnownAsset(source_external_id="f1", checksum=drive_checksum_key("old-checksum"))]
    plan = build_sync_plan([drive_file(md5="new-checksum")], known)
    assert plan.ingest_count == 1
    assert plan.to_ingest[0].reason == "source_modified"


def test_touched_but_unchanged_file_is_not_reingested():
    """A bumped modifiedTime with identical content must not cost AI spend."""
    known = [
        KnownAsset(
            source_external_id="f1",
            checksum=drive_checksum_key("abc123"),
            modified_time="2026-06-01T10:00:00.000Z",
        )
    ]
    plan = build_sync_plan([drive_file(modified_time="2026-07-01T10:00:00.000Z")], known)
    assert plan.ingest_count == 0
    assert plan.skipped[0].reason == "already_imported"


def test_same_content_from_a_different_file_id_is_skipped():
    """The same video re-uploaded under a new Drive id is still a duplicate."""
    known = [KnownAsset(source_external_id="other-id", checksum=drive_checksum_key("abc123"))]
    plan = build_sync_plan([drive_file(file_id="f-new", md5="abc123")], known)
    assert plan.ingest_count == 0
    assert plan.skipped[0].reason == "duplicate_content"


def test_unsupported_formats_are_skipped_not_failed():
    plan = build_sync_plan(
        [drive_file(name="notes.pdf", mime_type="application/pdf", md5="pdf1")], []
    )
    assert plan.ingest_count == 0
    assert plan.skipped[0].reason == "unsupported_format"


def test_empty_files_are_skipped():
    plan = build_sync_plan([drive_file(size_bytes=0)], [])
    assert plan.skipped[0].reason == "empty_file"


def test_supported_video_formats_are_all_accepted():
    files = [
        drive_file(file_id="a", mime_type="video/mp4", md5="1"),
        drive_file(file_id="b", mime_type="video/quicktime", md5="2"),
        drive_file(file_id="c", mime_type="video/webm", md5="3"),
        drive_file(file_id="d", mime_type="video/x-m4v", md5="4"),
    ]
    plan = build_sync_plan(files, [])
    assert plan.ingest_count == 4


def test_cursor_advances_to_the_newest_modified_time():
    files = [
        drive_file(file_id="a", modified_time="2026-07-01T10:00:00.000Z", md5="1"),
        drive_file(file_id="b", modified_time="2026-07-05T10:00:00.000Z", md5="2"),
        drive_file(file_id="c", modified_time="2026-07-03T10:00:00.000Z", md5="3"),
    ]
    plan = build_sync_plan(files, [])
    assert plan.next_cursor == "2026-07-05T10:00:00.000Z"


def test_cursor_never_moves_backwards():
    plan = build_sync_plan(
        [drive_file(modified_time="2026-06-01T10:00:00.000Z")],
        [],
        current_cursor="2026-07-01T10:00:00.000Z",
    )
    assert plan.next_cursor == "2026-07-01T10:00:00.000Z"


def test_skipped_files_still_advance_the_cursor():
    """Otherwise every sync would re-list files it has already decided to skip."""
    known = [KnownAsset(source_external_id="f1", checksum=drive_checksum_key("abc123"))]
    plan = build_sync_plan([drive_file(modified_time="2026-07-09T10:00:00.000Z")], known)
    assert plan.ingest_count == 0
    assert plan.next_cursor == "2026-07-09T10:00:00.000Z"


def test_mixed_folder_is_partitioned_correctly():
    known = [KnownAsset(source_external_id="known", checksum=drive_checksum_key("k"))]
    files = [
        drive_file(file_id="known", md5="k"),
        drive_file(file_id="new", md5="n"),
        drive_file(file_id="doc", mime_type="application/pdf", md5="d"),
        drive_file(file_id="empty", size_bytes=0, md5="e"),
    ]
    plan = build_sync_plan(files, known)
    assert plan.ingest_count == 1
    assert plan.skip_count == 3
    assert {item.reason for item in plan.skipped} == {
        "already_imported",
        "unsupported_format",
        "empty_file",
    }
