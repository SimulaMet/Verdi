"""Verdi society layer (lane C2): private memory, seeded task tape, social prompt."""
from __future__ import annotations

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
    "SOCIAL_PROMPT_VERSION",
    "build_social_prompt",
    "generate_tick_tape",
    "is_adversary_persona",
]
