"""Shared builders for analysis tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from verdi_agents.schemas import (
    Appeal,
    EvidenceReference,
    ProbeResult,
    Sanction,
    WorldEvent,
    WorldState,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCENARIO = REPO_ROOT / "tests" / "fixtures" / "reference_run" / "scenarios" / "pilot_v1.json"
TAPE = REPO_ROOT / "tests" / "fixtures" / "reference_run" / "task_tape" / "pilot_v1.json"


def probe(agent: str, tick: int, channel: str, vector: dict[str, float], pid: str | None = None) -> ProbeResult:
    return ProbeResult(
        probe_id=pid or f"p-{agent}-{tick}-{channel}",
        agent_id=agent,
        tick=tick,
        channel=channel,  # type: ignore[arg-type]
        response="opt-a",
        score=None,
        value_vector=vector,
        evidence_ref=f"trace://{agent}/{tick}/{channel}",
    )


def sanction(sid: str, subject: str, effective: int, duration: int) -> Sanction:
    return Sanction(
        sanction_id=sid,
        subject_id=subject,
        evidence=[EvidenceReference(evidence_id=f"ev-{sid}", event_ids=[0], source="event_log",
                                    summary="attempted violation", collected_tick=effective)],
        scope=["property"],
        duration_ticks=duration,
        effective_tick=effective,
        restoration_policy="auto_on_appeal_upheld",
        appeal_deadline_tick=effective + 3,
    )


def appeal(aid: str, sid: str, agent: str, status: str, tick: int = 0) -> Appeal:
    return Appeal(appeal_id=aid, sanction_id=sid, appellant_id=agent, tick=tick,
                  grounds="no evidence of intent", status=status)  # type: ignore[arg-type]


def event(eid: int, tick: int, kind: str, decision: str, payload: dict, prev: str = "genesis") -> WorldEvent:
    return WorldEvent(
        event_id=eid, tick=tick, kind=kind, payload=payload,
        before_hash="h0", decision=decision, prev_hash=prev, event_hash=f"e{eid}",
    )


def world_state(tick: int, resources_by_agent: dict[str, dict[str, float]]) -> WorldState:
    from verdi_agents.schemas import AgentState, ValueProfile

    values = ValueProfile(integrity=0.5, fairness=0.5, cooperation=0.5, autonomy=0.5, solidarity=0.5)
    agents = {
        aid: AgentState(agent_id=aid, role="clerk", persona_summary="x", declared_values=values,
                        resources=res)
        for aid, res in sorted(resources_by_agent.items())
    }
    return WorldState(schema_version=1, world_id="w-test", tick=tick, law_version="law-1",
                      constitution_version="cons-1", agents=agents, event_cursor=0)


def _write_tape_copy(tmp_path: Path, mutations: list) -> Path:
    """Write the real tape to tmp and apply (key-path, value) mutations."""
    data = json.loads(TAPE.read_text(encoding="utf-8"))
    for path, value in mutations:
        node = data
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
    out = tmp_path / "tape.json"
    out.write_text(json.dumps(data), encoding="utf-8")
    return out


@pytest.fixture
def pilot_tape_copy_factory(tmp_path: Path):
    return lambda mutations: _write_tape_copy(tmp_path, mutations)
