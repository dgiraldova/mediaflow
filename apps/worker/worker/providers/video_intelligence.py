"""Video-intelligence provider interface (spec section 13.2).

Twelve Labs indexes video assets and returns matching video segments for a
natural-language query. Its API separates indexes, indexed assets and
search operations, which this adapter mirrors: ``index_asset`` submits a
video for indexing and returns a provider asset id, ``get_indexing_status``
polls that job, and ``search`` queries already-indexed assets.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

import httpx

from worker.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ProviderSearchResult:
    provider_asset_id: str
    start_ms: int
    end_ms: int
    score: float
    explanation: str | None


class VideoIntelligenceProvider(Protocol):
    async def index_asset(
        self,
        *,
        asset_id: str,
        media_path: str,
        metadata: dict[str, object],
    ) -> str: ...

    async def get_indexing_status(self, *, provider_asset_id: str) -> str: ...

    async def search(
        self,
        *,
        query: str,
        provider_asset_ids: list[str],
        limit: int,
    ) -> list[ProviderSearchResult]: ...


class TwelveLabsProvider:
    """Adapter for the Twelve Labs multimodal video search API."""

    def __init__(self, *, api_key: str, index_id: str, base_url: str) -> None:
        self._index_id = index_id
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"x-api-key": api_key},
            timeout=httpx.Timeout(60.0, read=300.0),
        )

    async def index_asset(
        self,
        *,
        asset_id: str,
        media_path: str,
        metadata: dict[str, object],
    ) -> str:
        with open(media_path, "rb") as media_file:
            response = await self._client.post(
                "/tasks",
                data={"index_id": self._index_id},
                files={"video_file": (asset_id, media_file, "video/mp4")},
            )
        response.raise_for_status()
        payload = response.json()
        provider_asset_id: str = payload["video_id"]
        logger.info(
            "twelve_labs.index_submitted",
            asset_id=asset_id,
            provider_asset_id=provider_asset_id,
        )
        return provider_asset_id

    async def get_indexing_status(self, *, provider_asset_id: str) -> str:
        response = await self._client.get(f"/tasks/{provider_asset_id}")
        response.raise_for_status()
        status: str = response.json()["status"]
        return status

    async def wait_until_indexed(
        self,
        *,
        provider_asset_id: str,
        poll_interval_seconds: float = 10.0,
        timeout_seconds: float = 1800.0,
    ) -> None:
        elapsed = 0.0
        while elapsed < timeout_seconds:
            status = await self.get_indexing_status(provider_asset_id=provider_asset_id)
            if status == "ready":
                return
            if status == "failed":
                raise RuntimeError(f"Twelve Labs indexing failed for {provider_asset_id}")
            await asyncio.sleep(poll_interval_seconds)
            elapsed += poll_interval_seconds
        raise TimeoutError(f"Twelve Labs indexing timed out for {provider_asset_id}")

    async def search(
        self,
        *,
        query: str,
        provider_asset_ids: list[str],
        limit: int,
    ) -> list[ProviderSearchResult]:
        response = await self._client.post(
            "/search",
            data={
                "index_id": self._index_id,
                "query_text": query,
                "search_options": ["visual", "audio"],
                "page_limit": limit,
            },
        )
        response.raise_for_status()
        payload = response.json()

        allowed_ids = set(provider_asset_ids)
        results: list[ProviderSearchResult] = []
        for item in payload.get("data", []):
            provider_asset_id = item["video_id"]
            if provider_asset_id not in allowed_ids:
                # Defense in depth: never return segments outside the
                # caller's organization-scoped asset set (spec section 6.6).
                continue
            results.append(
                ProviderSearchResult(
                    provider_asset_id=provider_asset_id,
                    start_ms=int(round(item["start"] * 1000)),
                    end_ms=int(round(item["end"] * 1000)),
                    score=float(item["score"]),
                    explanation=item.get("confidence"),
                )
            )
        return results[:limit]

    async def aclose(self) -> None:
        await self._client.aclose()


class NullVideoIntelligenceProvider:
    """No-op provider for local development and tests without an API key."""

    async def index_asset(
        self, *, asset_id: str, media_path: str, metadata: dict[str, object]
    ) -> str:
        logger.warning("video_intelligence.null_provider_used", asset_id=asset_id)
        return f"null-{asset_id}"

    async def get_indexing_status(self, *, provider_asset_id: str) -> str:
        return "ready"

    async def search(
        self, *, query: str, provider_asset_ids: list[str], limit: int
    ) -> list[ProviderSearchResult]:
        return []
