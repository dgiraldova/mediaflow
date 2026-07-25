"""Drive synchronization planning and duplicate detection (MVP1-063, MVP1-064).

Deciding *what to import* is separated from *doing the import* so the rules are
testable without a Drive account. ``build_sync_plan`` is a pure function: given
what Drive reports and what we already have, it returns what to ingest, what to
skip and why.

Duplicate rules (spec section 11.2, steps 6-8):
  - Same Drive file id already imported -> skip, unless it changed since.
  - Same content (md5) already imported from anywhere -> skip.
  - Anything else -> import.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from worker.google_drive.client import DriveFile
from worker.media.validation import SUPPORTED_VIDEO_MIME_TYPES

# Drive reports md5Checksum, not sha256, so Drive-sourced dedup keys are
# namespaced to avoid ever colliding with our own sha256 asset checksums.
DRIVE_CHECKSUM_PREFIX = "gdrive-md5:"


@dataclass(frozen=True)
class KnownAsset:
    """What the database already knows about a previously imported file."""

    source_external_id: str | None
    checksum: str | None
    modified_time: str | None = None


@dataclass(frozen=True)
class SyncPlanItem:
    file: DriveFile
    reason: str


@dataclass
class SyncPlan:
    to_ingest: list[SyncPlanItem] = field(default_factory=list)
    skipped: list[SyncPlanItem] = field(default_factory=list)
    next_cursor: str | None = None

    @property
    def ingest_count(self) -> int:
        return len(self.to_ingest)

    @property
    def skip_count(self) -> int:
        return len(self.skipped)


def drive_checksum_key(md5_checksum: str) -> str:
    return f"{DRIVE_CHECKSUM_PREFIX}{md5_checksum}"


def build_sync_plan(
    files: list[DriveFile],
    known_assets: list[KnownAsset],
    *,
    current_cursor: str | None = None,
) -> SyncPlan:
    known_by_external_id = {
        asset.source_external_id: asset for asset in known_assets if asset.source_external_id
    }
    known_checksums = {asset.checksum for asset in known_assets if asset.checksum}

    plan = SyncPlan(next_cursor=current_cursor)

    for file in files:
        if file.mime_type not in SUPPORTED_VIDEO_MIME_TYPES:
            plan.skipped.append(SyncPlanItem(file, "unsupported_format"))
            continue

        if file.size_bytes <= 0:
            plan.skipped.append(SyncPlanItem(file, "empty_file"))
            continue

        existing = known_by_external_id.get(file.id)
        if existing is not None:
            if _has_changed(existing, file):
                plan.to_ingest.append(SyncPlanItem(file, "source_modified"))
            else:
                plan.skipped.append(SyncPlanItem(file, "already_imported"))
            plan.next_cursor = _advance_cursor(plan.next_cursor, file.modified_time)
            continue

        if file.md5_checksum and drive_checksum_key(file.md5_checksum) in known_checksums:
            plan.skipped.append(SyncPlanItem(file, "duplicate_content"))
            plan.next_cursor = _advance_cursor(plan.next_cursor, file.modified_time)
            continue

        plan.to_ingest.append(SyncPlanItem(file, "new_file"))
        plan.next_cursor = _advance_cursor(plan.next_cursor, file.modified_time)

    return plan


def _has_changed(existing: KnownAsset, file: DriveFile) -> bool:
    """Has the Drive file changed since we imported it?

    Prefer the checksum: a file can be touched (modifiedTime bumped) without
    its content changing, and re-ingesting identical content wastes AI spend.
    """
    if file.md5_checksum and existing.checksum:
        return drive_checksum_key(file.md5_checksum) != existing.checksum
    if existing.modified_time and file.modified_time:
        return file.modified_time > existing.modified_time
    # Without either signal, assume unchanged rather than reprocess on
    # every sync.
    return False


def _advance_cursor(cursor: str | None, modified_time: str) -> str | None:
    """Track the newest modifiedTime seen, for the next incremental sync.

    Drive timestamps are RFC 3339 in UTC, which sorts correctly as text.
    """
    if not modified_time:
        return cursor
    if cursor is None or modified_time > cursor:
        return modified_time
    return cursor
