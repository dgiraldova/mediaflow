from __future__ import annotations

import pytest

from worker.storage.local import LocalStorage


@pytest.mark.asyncio
async def test_local_storage_round_trip_and_ranges(tmp_path):
    storage = LocalStorage(root=str(tmp_path))
    key = "orgs/demo/assets/asset-1/proxy/proxy.mp4"

    stored = await storage.put_object(
        key=key,
        body=b"0123456789",
        content_type="video/mp4",
    )

    assert stored.byte_size == 10
    assert await storage.read_object(key=key, byte_range=(2, 5)) == b"2345"

    destination = tmp_path / "downloaded.mp4"
    await storage.download_to_path(key=key, destination_path=str(destination))
    assert destination.read_bytes() == b"0123456789"

    await storage.delete_object(key=key)
    assert await storage.head_object(key=key) is None


@pytest.mark.asyncio
async def test_local_storage_rejects_path_traversal(tmp_path):
    storage = LocalStorage(root=str(tmp_path))

    with pytest.raises(ValueError, match="safe relative path"):
        await storage.put_object(
            key="../outside.mp4",
            body=b"unsafe",
            content_type="video/mp4",
        )
