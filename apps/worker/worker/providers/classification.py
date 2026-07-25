"""Structured-classification provider interface (spec section 13.3).

All AI output must be validated before persistence (spec section 13.3 and
AGENTS.md rule "never persist unvalidated AI output"). ``MomentClassification``
is the Pydantic model every adapter must return; OpenAI's structured
outputs mode enforces this schema at generation time, but we still
validate on our side since the provider boundary is untrusted input.
"""

from __future__ import annotations

from typing import Protocol

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from worker.logging import get_logger
from worker.moments.taxonomy import CONTENT_TYPES, FUNNEL_STAGES

logger = get_logger(__name__)


class MomentClassification(BaseModel):
    title: str
    visual_description: str
    marketing_description: str
    content_types: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    pain_points: list[str] = Field(default_factory=list)
    benefits: list[str] = Field(default_factory=list)
    funnel_stages: list[str] = Field(default_factory=list)
    people_labels: list[str] = Field(default_factory=list)
    product_labels: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    technical_quality_score: float | None = None


class MomentClassificationInput(BaseModel):
    """Prompt inputs per spec section 14.2."""

    transcript_text: str
    neighboring_context: str
    visual_description: str
    start_ms: int
    end_ms: int
    asset_title: str
    organization_vocabulary: list[str] = Field(default_factory=list)
    language: str | None = None


class StructuredClassificationProvider(Protocol):
    async def classify_moment(
        self, moment_input: MomentClassificationInput
    ) -> MomentClassification: ...


_SYSTEM_PROMPT = f"""You are a marketing analyst classifying a short segment of a \
business video for a searchable media library. Use the transcript and visual \
description to produce a concise title, a visual description, and a marketing \
description, plus structured labels drawn from the controlled taxonomy below \
whenever they apply. Do not invent taxonomy values outside this list; use \
"other" or "unknown" when nothing fits.

content_types: {", ".join(CONTENT_TYPES)}
funnel_stages: {", ".join(FUNNEL_STAGES)}
"""


class OpenAIClassificationProvider:
    def __init__(self, *, api_key: str, model: str, base_url: str | None = None) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    async def classify_moment(
        self, moment_input: MomentClassificationInput
    ) -> MomentClassification:
        response = await self._client.responses.parse(
            model=self._model,
            input=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": moment_input.model_dump_json()},
            ],
            text_format=MomentClassification,
        )
        result = response.output_parsed
        if result is None:
            raise ValueError("OpenAI classification returned no parsed output")

        # Defense in depth even though structured outputs already constrain
        # the shape: strip any taxonomy value the model hallucinated.
        return result.model_copy(
            update={
                "content_types": [c for c in result.content_types if c in CONTENT_TYPES],
                "funnel_stages": [f for f in result.funnel_stages if f in FUNNEL_STAGES],
            }
        )


class NullClassificationProvider:
    """No-op provider for local development and tests without an API key."""

    async def classify_moment(
        self, moment_input: MomentClassificationInput
    ) -> MomentClassification:
        logger.warning("classification.null_provider_used")
        return MomentClassification(
            title=moment_input.asset_title,
            visual_description=moment_input.visual_description or "",
            marketing_description=moment_input.transcript_text[:200],
            content_types=["other"],
            funnel_stages=["unknown"],
        )
