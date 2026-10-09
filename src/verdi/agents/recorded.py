"""Recorded model: offline, deterministic replay stand-in for the vLLM client.

Serves canned completion outputs from a tape. Two addressing modes:
- by request order (the default): pops the next tape entry per call;
- by request key: a mapping from an explicit key (e.g. the prompt text) to a
  canned output, with the remaining tape consumed in order as fallback.

This is what makes full-pipeline replay work without GPU
(docs/RESEARCH_SPEC.md: replayable snapshots without live model invocation).
"""
from __future__ import annotations

import uuid

from verdi.agents.client import ModelResult


class TapeExhausted(RuntimeError):
    """Raised when the tape has no more entries for the requested output."""


class RecordedModel:
    """Deterministic ModelCaller backed by a canned tape of JSON strings."""

    def __init__(self, tape: list[str], *, model: str = "recorded-tape") -> None:
        self._tape = list(tape)
        self._model = model
        self.request_order: list[str] = []

    def __len__(self) -> int:
        return len(self._tape)

    @property
    def remaining(self) -> int:
        return len(self._tape)

    async def generate_structured(
        self,
        messages: list[dict[str, str]],
        *,
        response_format_json: bool = False,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ModelResult:
        if not self._tape:
            raise TapeExhausted(
                f"recorded tape exhausted after {len(self.request_order)} request(s)"
            )
        content = self._tape.pop(0)
        request_id = f"rec-{len(self.request_order)}"
        self.request_order.append(request_id)
        return ModelResult(
            content=content,
            request_id=request_id,
            model=self._model,
            trace_id=str(uuid.uuid5(uuid.NAMESPACE_URL, request_id)),
        )


__all__ = ["RecordedModel", "TapeExhausted"]
