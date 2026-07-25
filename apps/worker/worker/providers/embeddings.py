"""Embedding provider interface (spec section 13.4)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from openai import AsyncOpenAI

from worker.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class EmbeddingMetadata:
    """Recorded alongside every embedding per spec section 13.4."""

    provider: str
    model: str
    dimensions: int
    version: str


class EmbeddingProvider(Protocol):
    async def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbeddingProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        dimensions: int,
        base_url: str | None = None,
        batch_size: int = 32,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._dimensions = dimensions
        self._batch_size = batch_size

    @property
    def metadata(self) -> EmbeddingMetadata:
        return EmbeddingMetadata(
            provider="openai",
            model=self._model,
            dimensions=self._dimensions,
            version="1",
        )

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        embeddings: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            response = await self._client.embeddings.create(
                model=self._model,
                input=batch,
                dimensions=self._dimensions,
            )
            embeddings.extend(item.embedding for item in response.data)

        logger.info("embeddings.generated", count=len(embeddings), model=self._model)
        return embeddings


class NullEmbeddingProvider:
    """No-op provider for local development and tests without an API key."""

    def __init__(self, dimensions: int = 1536) -> None:
        self._dimensions = dimensions

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        logger.warning("embeddings.null_provider_used", count=len(texts))
        return [[0.0] * self._dimensions for _ in texts]
