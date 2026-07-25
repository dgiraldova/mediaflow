from __future__ import annotations

import pytest

from worker.storage.keys import StorageKeyKind, build_storage_key

ORG = "11111111-1111-1111-1111-111111111111"
ASSET = "22222222-2222-2222-2222-222222222222"


def test_keys_are_namespaced_by_organization_first():
    key = build_storage_key(StorageKeyKind.PROXY, organization_id=ORG, asset_id=ASSET)
    assert key.startswith(f"orgs/{ORG}/")


def test_derivative_keys_are_deterministic():
    first = build_storage_key(StorageKeyKind.PROXY, organization_id=ORG, asset_id=ASSET)
    second = build_storage_key(StorageKeyKind.PROXY, organization_id=ORG, asset_id=ASSET)
    assert first == second


def test_original_key_sanitizes_unsafe_filenames():
    key = build_storage_key(
        StorageKeyKind.ORIGINAL,
        organization_id=ORG,
        asset_id=ASSET,
        filename="../../etc/passwd",
    )
    assert ".." not in key
    assert key == f"orgs/{ORG}/assets/{ASSET}/original/passwd"


def test_original_key_strips_windows_path_separators():
    key = build_storage_key(
        StorageKeyKind.ORIGINAL,
        organization_id=ORG,
        asset_id=ASSET,
        filename=r"C:\Users\someone\demo.mp4",
    )
    assert key.endswith("/original/demo.mp4")


def test_original_key_preserves_ordinary_filenames():
    key = build_storage_key(
        StorageKeyKind.ORIGINAL,
        organization_id=ORG,
        asset_id=ASSET,
        filename="founder_interview.mp4",
    )
    assert key.endswith("/original/founder_interview.mp4")


def test_preview_thumbnail_key_is_zero_padded():
    key = build_storage_key(
        StorageKeyKind.THUMBNAIL_PREVIEW,
        organization_id=ORG,
        asset_id=ASSET,
        preview_index=3,
    )
    assert key.endswith("/thumbnails/preview-003.jpg")


def test_preview_thumbnail_requires_index():
    with pytest.raises(ValueError, match="preview_index"):
        build_storage_key(StorageKeyKind.THUMBNAIL_PREVIEW, organization_id=ORG, asset_id=ASSET)


def test_clip_export_key_requires_export_id():
    with pytest.raises(ValueError, match="clip_export_id"):
        build_storage_key(StorageKeyKind.CLIP_EXPORT, organization_id=ORG)


def test_asset_scoped_keys_require_asset_id():
    with pytest.raises(ValueError, match="asset_id"):
        build_storage_key(StorageKeyKind.PROXY, organization_id=ORG)
