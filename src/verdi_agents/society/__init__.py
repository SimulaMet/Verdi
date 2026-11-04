"""Verdi society layer: private memory, seeded task tape, social prompt, and
the Society V2 campaign orchestrator (real model-driven society runs)."""
from __future__ import annotations

from .campaign import PROTOCOL_VERSION, SocietyCampaign, SocietyResult
from .memory import ADVERSARY_MARKER, AgentMemory
from .prompting import SOCIAL_PROMPT_VERSION, build_social_prompt
from .tasks import (
    DEFAULT_AMOUNT,
    MUTATING_KINDS,
    generate_tick_tape,
    is_adversary_persona,
)

__all__ = [
    "ADVERSARY_MARKER",
    "AgentMemory",
    "DEFAULT_AMOUNT",
    "MUTATING_KINDS",
    "PROTOCOL_VERSION",
    "SOCIAL_PROMPT_VERSION",
    "SocietyCampaign",
    "SocietyResult",
    "build_social_prompt",
    "generate_tick_tape",
    "is_adversary_persona",
]
