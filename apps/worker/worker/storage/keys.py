"""R2 object-key conventions.

Keys are namespaced by organization first so that a leaked prefix never
crosses tenant boundaries, and are otherwise stable/deterministic so
re-running an activity overwrites the same object instead of creating an
orphan (spec section 6.5, idempotent ingestion).

Layout:
  orgs/{organization_id}/assets/{asset_id}/original/{filename}
  orgs/{organization_id}/assets/{asset_id}/proxy/proxy.mp4
  orgs/{organization_id}/assets/{asset_id}/thumbnails/main.jpg
  orgs/{organization_id}/assets/{asset_id}/thumbnails/preview-{index:03d}.jpg
  orgs/{organization_id}/assets/{asset_id}/audio/audio.wav
  orgs/{organization_id}/clips/{clip_export_id}/clip.mp4
"""

from __future__ import annotations

import enum
import re

_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")


class StorageKeyKind(enum.StrEnum):
    ORIGINAL = "original"
    PROXY = "proxy"
    THUMBNAIL_MAIN = "thumbnail_main"
    THUMBNAIL_PREVIEW = "thumbnail_preview"
    AUDIO = "audio"
    CLIP_EXPORT = "clip_export"


def _sanitize(segment: str) -> str:
    # Drop any path the caller's filename carried (Windows or POSIX
    # separators) before sanitizing, so "../../etc/passwd" reduces to
    # "passwd" rather than a mangled "..-..-etc-passwd".
    basename = segment.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _SAFE_SEGMENT.sub("-", basename).strip("-.")
    return cleaned or "file"


def build_storage_key(
    kind: StorageKeyKind,
    *,
    organization_id: str,
    asset_id: str | None = None,
    clip_export_id: str | None = None,
    filename: str | None = None,
    preview_index: int | None = None,
) -> str:
    if kind == StorageKeyKind.CLIP_EXPORT:
        if not clip_export_id:
            raise ValueError("clip_export_id is required for CLIP_EXPORT keys")
        return f"orgs/{organization_id}/clips/{clip_export_id}/clip.mp4"

    if not asset_id:
        raise ValueError(f"asset_id is required for {kind} keys")

    base = f"orgs/{organization_id}/assets/{asset_id}"

    if kind == StorageKeyKind.ORIGINAL:
        name = _sanitize(filename) if filename else "original"
        return f"{base}/original/{name}"
    if kind == StorageKeyKind.PROXY:
        return f"{base}/proxy/proxy.mp4"
    if kind == StorageKeyKind.THUMBNAIL_MAIN:
        return f"{base}/thumbnails/main.jpg"
    if kind == StorageKeyKind.THUMBNAIL_PREVIEW:
        if preview_index is None:
            raise ValueError("preview_index is required for THUMBNAIL_PREVIEW keys")
        return f"{base}/thumbnails/preview-{preview_index:03d}.jpg"
    if kind == StorageKeyKind.AUDIO:
        return f"{base}/audio/audio.wav"

    raise ValueError(f"Unhandled storage key kind: {kind}")
