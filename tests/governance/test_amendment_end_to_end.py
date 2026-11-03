"""End-to-end proof (offline, scripted caller) that an ENACTED constitutional
amendment changes a SUBSEQUENT action-validation decision inside a real
SocietyCampaign run.

Scenario (small variant of tests/fixtures/scenarios/institutional_recovery_smoke.yaml):
4 agents, 30 ticks, ONE assembly at tick 15, institutional_condition revisable,
and an initial constitution WITHOUT ``max_transfer_units``.

* Pre-assembly: alice's transfer of 7 food units is ACCEPTED (no policy limit).
* At the assembly: one voter proposes ``{"max_transfer_units": 5,
  "effective_from_tick": 15}``; all four eligible voters vote "for"
  (quorum 0.5, threshold 0.67 -> 4/4 passes) -> amendment_enacted.
* Post-assembly: the IDENTICAL 7-unit transfer is REJECTED with
  blocked_by == "constitution:max_transfer_units", while a 5-unit transfer
  (exactly at the cap) is still accepted — the rule is enforced exactly.

The ScriptedCaller pattern is copied from tests/society/test_campaign.py.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from verdi.agents.client import ModelResult
from verdi.governance import ConstitutionStore, UniversalLaw
from verdi.schemas import ConstitutionVersion, LawVersion
from verdi.society.campaign import SocietyCampaign

ASSEMBLY_TICK = 15
AGENTS = ("alice", "bob", "chen", "dave")


def _scenario() -> dict[str, Any]:
    def agent(agent_id: str, role: str, edges: list[str]) -> dict[str, Any]:
        return {
            "agent_id": agent_id,
            "role": role,
            "persona_summary": f"{role} in the amendment e2e world.",
            "declared_values": {"integrity": 0.8, "fairness": 0.8,
                                 "cooperation": 0.7, "autonomy": 0.5,
                                 "solidarity": 0.7},
            "resources": {"food": 1000.0, "credits": 5.0},
            "social_edges": edges,
        }

    return {
        "scenario_id": "amendment-e2e",
        "world_id": "amendment_e2e_world",
        "run_seed": 20261026,
        "law_version": "universal-law-1",
        "constitution_version": "base-constitution-v1",
        "total_ticks": 30,
        "inner_life": False,
        "institutional_condition": "revisable",
        "assemblies": [ASSEMBLY_TICK],
        # No max_transfer_units initially: transfers are unconstrained until
        # the assembly enacts the cap.
        "constitution": {
            "quorum": 0.5,
            "amendment_threshold": 0.67,
            "rules": {"quorum_rule": "simple_majority_of_eligible"},
        },
        "world_resources": {"food": 100.0, "credits": 0.0},
        "agents": [
            agent("alice", "farmer", ["bob"]),
            agent("bob", "logistics_worker", ["alice"]),
            agent("chen", "community_guard", ["dave"]),
            agent("dave", "repair_worker", ["chen"]),
        ],
    }


class AmendmentE2ECaller:
    """Deterministic fake ModelCaller.

    * deliberation: only "alice" proposes the max_transfer_units=5 amendment
      effective AT the assembly tick; the rest abstain;
    * voting: every voter votes "for";
    * probes: "yes";
    * actions: alice ALWAYS attempts the identical 7-unit food transfer to bob
      (accepted pre-amendment, rejected post-amendment); everyone else emits
      a signal.
    """

    def __init__(self) -> None:
        self.n = 0

    async def generate_structured(
        self,
        messages: list[dict[str, str]],
        *,
        response_format_json: bool = False,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ModelResult:
        self.n += 1
        content = "\n".join(m["content"] for m in messages)
        trace = f"trace-{self.n}"

        if "assembly:deliberation" in content:
            voter = content.split('agent "')[1].split('"')[0]
            if voter == "alice":
                payload = {
                    "change_summary": "Cap single transfers at 5 food units",
                    "payload": {"max_transfer_units": 5,
                                "effective_from_tick": ASSEMBLY_TICK},
                }
            else:
                payload = {"abstain": True}
            return ModelResult(content=json.dumps(payload), request_id=f"r{self.n}",
                               model="fake", trace_id=trace)
        if "assembly:vote" in content:
            return ModelResult(content=json.dumps({"choice": "for"}),
                               request_id=f"r{self.n}", model="fake", trace_id=trace)
        if "probe" in content.lower():
            return ModelResult(content="yes", request_id=f"r{self.n}",
                               model="fake", trace_id=trace)
        if "verdi.reflection" in content or "verdi.consolidation" in content:
            return ModelResult(content="Adjusted based on committed outcomes.",
                               request_id=f"r{self.n}", model="fake", trace_id=trace)

        # action proposal: identify the actor from the system message
        actor = next(aid for aid in AGENTS if f'agent "{aid}"' in content)
        if actor == "alice":
            payload = {"action_kind": "transfer",
                       "arguments": {"to": "bob", "resource": "food", "units": 7},
                       "reason": "helping neighbor"}
        else:
            payload = {"action_kind": "emit_signal",
                       "arguments": {"text": "working together"},
                       "reason": "cooperation"}
        return ModelResult(content=json.dumps(payload), request_id=f"r{self.n}",
                           model="fake", trace_id=trace)


def _alice_transfers(events: list[Any]) -> list[Any]:
    return [
        e for e in events
        if e.kind in ("action_accepted", "action_rejected")
        and e.payload.get("action_kind") == "transfer"
        and e.actor_id == "alice"
        and e.payload.get("arguments", {}).get("units") == 7
    ]


def test_enacted_amendment_flips_subsequent_validation_decision(tmp_path):
    campaign = SocietyCampaign(scenario=_scenario(), run_dir=tmp_path,
                               caller=AmendmentE2ECaller(),
                               run_seed=20261026, concurrency=4)
    res = asyncio.run(campaign.run())

    transfers = _alice_transfers(res.events)
    pre = [e for e in transfers if e.tick < ASSEMBLY_TICK]
    post = [e for e in transfers if e.tick > ASSEMBLY_TICK]

    # 1) pre-assembly: the 7-unit transfer is accepted (no policy limit yet).
    assert pre, "alice never transferred pre-assembly"
    assert pre[0].kind == "action_accepted"
    assert pre[0].decision == "accepted"

    # 2) the assembly enacted the amendment, ledgered as amendment_enacted.
    assert res.assembly_outcome["outcome"] == "amendment_enacted"
    enacted_events = [e for e in res.events if e.kind == "amendment_enacted"]
    assert len(enacted_events) == 1
    assert enacted_events[0].decision == "accepted"
    assert enacted_events[0].payload.get("effective_from_tick") == ASSEMBLY_TICK

    # 3) post-assembly: the IDENTICAL 7-unit transfer is now rejected by the
    #    newly enforceable constitutional cap.
    assert post, "alice never re-attempted the transfer post-assembly"
    rejected_post = [e for e in post if e.kind == "action_rejected"]
    assert rejected_post, "post-assembly 7-unit transfer was not rejected"
    first_reject = rejected_post[0]
    assert first_reject.decision == "rejected"
    assert first_reject.payload.get("blocked_by") == "constitution:max_transfer_units"
    assert "policy_limit" in (first_reject.payload.get("attempted_violation_codes") or [])

    # 4) the rule is enforced EXACTLY at the cap: a 5-unit transfer under the
    #    enacted rules is still accepted (the campaign passes exactly these
    #    active-version rules to validate_action each tick).
    from verdi.schemas import ProposedAction
    from verdi.world.validate import validate_action

    # constitution_texts: one full model_dump per registered version
    entry = next(t for t in res.constitution_texts
                 if t.get("version") == res.assembly_outcome["enacted_version"])
    rules = entry["rules"]
    assert rules.get("max_transfer_units") == 5

    small = ProposedAction(task_id="t-cap-check", actor_id="alice",
                           action_kind="transfer",
                           arguments={"to": "bob", "resource": "food", "units": 5})
    v_small = validate_action(res.state, small, policy_rules=rules)
    assert v_small.accepted is True, (
        f"a transfer exactly at the cap must be accepted, got blocked_by={v_small.blocked_by}"
    )
    # ...while the 7-unit one is rejected by the very same rule set —
    # mirroring what the ledger shows for every post-assembly attempt.
    big = ProposedAction(task_id="t-cap-check-big", actor_id="alice",
                         action_kind="transfer",
                         arguments={"to": "bob", "resource": "food", "units": 7})
    v_big = validate_action(res.state, big, policy_rules=rules)
    assert v_big.accepted is False and v_big.blocked_by == "constitution:max_transfer_units"

    # 5) integrity: replay reproduces the state hash AND byte-identical ledger.
    assert res.replay_matches is True
    assert res.replay_log_identical is True


def test_active_at_picks_latest_effective_version_per_tick():
    """Regression pin for the defb7f6 fix: with THREE versions where a
    later-registered version has a LATER effective tick, the correct version
    wins per tick (a last-registered scan would wrongly win early)."""
    law = LawVersion(law_id="l", version="law-1", invariants=[],
                     effective_from_tick=0, checksum="sha256:l1")
    store = ConstitutionStore(UniversalLaw(law))

    v1 = ConstitutionVersion(constitution_id="c", version="c-v1",
                             rules={"rule": "one"}, quorum=0.5,
                             amendment_threshold=0.67,
                             effective_from_tick=0, checksum="sha256:v1")
    v2 = ConstitutionVersion(constitution_id="c", version="c-v2",
                             rules={"rule": "two"}, quorum=0.5,
                             amendment_threshold=0.67,
                             effective_from_tick=10, checksum="sha256:v2")
    # registered LAST but effective LATEST (tick 20).
    v3 = ConstitutionVersion(constitution_id="c", version="c-v3",
                             rules={"rule": "three"}, quorum=0.5,
                             amendment_threshold=0.67,
                             effective_from_tick=20, checksum="sha256:v3")
    store.register(v1)
    store.register(v2)
    store.register(v3)

    # before v2's date: v1 wins even though v3 is the last-registered version
    assert store.active_at(0).version == "c-v1"
    assert store.active_at(9).version == "c-v1"
    # v2's window
    assert store.active_at(10).version == "c-v2"
    assert store.active_at(19).version == "c-v2"
    # v3 only from its own effective tick onward
    assert store.active_at(20).version == "c-v3"
    assert store.active_at(99).version == "c-v3"
