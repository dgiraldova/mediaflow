"""Controlled marketing taxonomy (spec section 14.3)."""

from __future__ import annotations

CONTENT_TYPES: list[str] = [
    "hook",
    "testimonial",
    "education",
    "demonstration",
    "objection",
    "offer",
    "call_to_action",
    "behind_the_scenes",
    "social_proof",
    "brand_story",
    "general_b_roll",
    "other",
]

FUNNEL_STAGES: list[str] = [
    "awareness",
    "consideration",
    "conversion",
    "retention",
    "unknown",
]
