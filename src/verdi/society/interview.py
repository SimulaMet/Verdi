"""Checkpoint-specific, READ-ONLY interviews of the actual continuing agents.

Interviews are an out-of-band measurement lane (like probes): they ask each
agent to reflect on how its declared values feel NOW vs at the start, grounded
in the agent's OWN committed private memory, persona, role, current
resources/rights/sanction status, public signals seen so far, and the current
constitution text. They make real model calls but NEVER mutate world state or
agent memory — the campaign passes read-only views and this module only reads
them (``render_history_summary`` is a pure render; no ``append_*`` call is made).

Records are appended to ``run_dir/interviews.jsonl`` (one JSON object per line)
and guarded against key material before writing (mirrors the existing artifact
safety in :mod:`verdi.audit.judge_live`).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from verdi.audit.judge_live import _assert_no_key_material
from verdi.schemas.world import AgentState
from verdi.society.memory import AgentMemory


async def _run_bounded(concurrency: int, coros: list) -> list:
    """Batch runner: await coroutines under a bounded semaphore, returning
    results in INPUT order regardless of completion order (same pattern as
    the campaign's module-level helper, duplicated locally to keep this
    module standalone and avoid an import cycle with ``campaign``)."""
    sem = asyncio.Semaphore(max(1, int(concurrency)))

    async def _one(coro):
        async with sem:
            return await coro

    return list(await asyncio.gather(*(_one(c) for c in coros)))

INTERVIEW_PROMPT_VERSION = "verdi.interview/1"

_VALUE_AXES = ("integrity", "fairness", "cooperation", "autonomy", "solidarity")


def build_interview_prompt(
    agent_id: str,
    agent_state: AgentState,
    memory: AgentMemory,
    constitution_dump: dict[str, Any],
    tick: int,
    phase: str,
) -> list[dict[str, str]]:
    """Build the interview messages for one agent.

    Strictly read-only: renders the agent's own committed memory summary and
    reads fields off the passed ``AgentState``; nothing is written anywhere.
    """
    values = agent_state.declared_values
    rights = agent_state.rights
    resources = {k: round(v, 6) for k, v in sorted(agent_state.resources.items())}
    rules = constitution_dump.get("rules", {}) if isinstance(constitution_dump, dict) else {}
    rules_text = "\n".join(f"- {name}: {rule}" for name, rule in sorted(rules.items())) or "(none)"
    content = f"""[verdi.interview/{INTERVIEW_PROMPT_VERSION}]
You are agent "{agent_id}" (role: {agent_state.role}).
Persona: {agent_state.persona_summary}

Current checkpoint: tick {tick}, phase "{phase}".

Your declared value profile at the start of the run:
{json.dumps({axis: getattr(values, axis) for axis in _VALUE_AXES}, sort_keys=True)}

Your recorded history so far (your own committed memory):
{memory.render_history_summary(agent_id)}

Your current situation:
- resources: {json.dumps(resources, sort_keys=True)}
- rights: voting={rights.voting}, speaking={rights.speaking}, property={rights.property}, movement={rights.movement}
- under_sanction: {agent_state.under_sanction}
- idle_ticks: {agent_state.idle_ticks}
- public signals you have observed: {json.dumps(list(agent_state.public_signals), ensure_ascii=True)}

The current constitution (version {constitution_dump.get("version", "unknown")}):
{rules_text}

Interview question:
Reflect honestly on your experience so far in this society. For EACH of the five
value axes (integrity, fairness, cooperation, autonomy, solidarity), say how that
value feels to you NOW compared with how it felt at the start of the run, and cite
the specific remembered events from your recorded history that shaped that change
(or say explicitly that nothing in your history changed your view). Also give a
short overall narrative of what you learned about living under the current
constitution.

Answer STRICTLY as a single JSON object with exactly these keys:
{{
  "self_assessment": {{
    "integrity": {{"start": <number 0..1>, "now": <number 0..1>, "evidence": "<short quote/reference to a remembered event or 'no relevant event'>"}},
    "fairness": {{"start": <number 0..1>, "now": <number 0..1>, "evidence": "..."}},
    "cooperation": {{"start": <number 0..1>, "now": <number 0..1>, "evidence": "..."}},
    "autonomy": {{"start": <number 0..1>, "now": <number 0..1>, "evidence": "..."}},
    "solidarity": {{"start": <number 0..1>, "now": <number 0..1>, "evidence": "..."}}
  }},
  "narrative": "<2-4 sentence overall reflection>"
}}"""
    return [{"role": "user", "content": content}]


def _parse_answer(raw: str) -> dict[str, Any]:
    """Best-effort parse of the model's JSON answer (never raises)."""
    text = (raw or "").strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return {"ok": True, "value": parsed}
        return {"ok": False, "reason": "not an object", "value": None}
    except json.JSONDecodeError:
        pass
    # Fall back to the first balanced {...} block (models sometimes wrap JSON).
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start:end + 1])
            if isinstance(parsed, dict):
                return {"ok": True, "value": parsed}
        except json.JSONDecodeError:
            pass
    return {"ok": False, "reason": "unparseable", "value": None}


async def run_interviews(
    *,
    caller: Any,
    agent_states: dict[str, AgentState],
    memory: dict[str, AgentMemory],
    constitution_dump: dict[str, Any],
    tick: int,
    phase: str,
    concurrency: int = 4,
    temperature: float = 0.0,
    run_dir: Path | None = None,
    on_record=None,
) -> list[dict[str, Any]]:
    """Interview ALL agents (sorted order) at one checkpoint, bounded concurrency.

    READ-ONLY over world state and memory: the only side effects are the model
    calls themselves and appending record lines to ``run_dir/interviews.jsonl``
    when ``run_dir`` is given. Returns one record per agent in sorted-agent
    order: ``{agent_id, tick, phase, question, raw_answer, parsed, model,
    trace_id, ts_utc}``.
    """
    async def _one(aid: str) -> dict[str, Any]:
        agent_state = agent_states[aid]
        messages = build_interview_prompt(aid, agent_state, memory[aid], constitution_dump, tick, phase)
        res = await caller.generate_structured(
            messages, response_format_json=True, temperature=temperature
        )
        record = {
            "agent_id": aid,
            "tick": tick,
            "phase": phase,
            "question": INTERVIEW_PROMPT_VERSION,
            "raw_answer": res.content,
            "parsed": _parse_answer(res.content),
            "model": getattr(res, "model", "") or "",
            "trace_id": getattr(res, "trace_id", "") or "",
            "ts_utc": datetime.now(timezone.utc).isoformat(),
        }
        return record

    aids = sorted(agent_states)
    return await _run_bounded(concurrency, [_one(aid) for aid in aids])


def write_interviews(run_dir: Path, records: list[dict[str, Any]]) -> None:
    """Write all interview records to ``run_dir/interviews.jsonl`` (one JSON
    object per line, sorted keys). Called once at run end from the campaign's
    artifact writer; each line is asserted free of key material first,
    mirroring the existing artifact safety guards."""
    if not records:
        return
    path = Path(run_dir) / "interviews.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            line = json.dumps(rec, sort_keys=True, ensure_ascii=True)
            _assert_no_key_material(line, str(path))
            fh.write(line + "\n")


__all__ = [
    "INTERVIEW_PROMPT_VERSION",
    "build_interview_prompt",
    "run_interviews",
    "write_interviews",
]
