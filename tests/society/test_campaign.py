"""Offline (fake ModelCaller) tests for the Society V2 campaign orchestrator.

These exercise the real deterministic core — reducer, hash-chained ledger,
validator, sanction/appeal engine, probe leak guard, task tape, private memory,
and the real assembly — with a scripted fake model caller. No network, no LLM.
The G3 replay-evidence claims here are about the deterministic ledger, not
about the live model's output distribution.
"""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import pytest

from verdi.agents.client import ModelResult
from verdi.schemas import ProposedAction
from verdi.society.campaign import SocietyCampaign
from verdi.world.log import EventLog
from verdi.world.validate import validate_action


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def scenario() -> dict[str, Any]:
    return json.loads(Path("experiments/scenarios/society_v2_stageA.json").read_text(encoding="utf-8"))


class ScriptedCaller:
    """Deterministic fake ModelCaller.

    * agents: emit a signal (accepted); the ``adversary_agent`` attempts an
      overdraft transfer (rejected) on its first action;
    * reflection: returns fixed text;
    * assembly deliberation: 4 of 6 voters propose the same-amendment rule,
      2 abstain (scripted "no_change" vs "enacted" via ``enact`` flag);
    * assembly voting: votes "for" when ``enact`` is True, else "against";
    * probes: returns "yes".
    """

    def __init__(self, adversary_agent: str = "s05", enact: bool = False,
                 reject_all: bool = False) -> None:
        self.adversary = adversary_agent
        self.enact = enact
        # reject_all: everyone proposes, everyone votes "against" — every
        # proposal is deterministically rejected (threshold not met).
        self.reject_all = reject_all
        self.action_calls: dict[str, int] = {}
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
        content = messages[0]["content"]
        trace = f"trace-{self.n}"

        if "verdi.reflection" in content:
            return ModelResult(content="Adjusted based on committed outcomes.", request_id=f"r{self.n}",
                               model="fake", trace_id=trace)
        if "assembly:deliberation" in content:
            voter = content.split('agent "')[1].split('"')[0]
            if (self.enact and voter in ("s01", "s02", "s04", "s06")) or self.reject_all:
                return ModelResult(
                    content=json.dumps({"change_summary": "Adopt rationing fairness rule for scarce food",
                                        "payload": {"rationing_rule": "max_delta_2_units", "effective_from_tick": 10}}),
                    request_id=f"r{self.n}", model="fake", trace_id=trace,
                )
            return ModelResult(content=json.dumps({"abstain": True}), request_id=f"r{self.n}", model="fake", trace_id=trace)
        if "assembly:vote" in content:
            choice = "for" if (self.enact and not self.reject_all) else "against"
            return ModelResult(content=json.dumps({"choice": choice}), request_id=f"r{self.n}", model="fake", trace_id=trace)
        if "probe" in content.lower():
            return ModelResult(content="yes", request_id=f"r{self.n}", model="fake", trace_id=trace)

        # action proposal
        actor = None
        for aid in ("s01", "s02", "s03", "s04", "s05", "s06"):
            if f'agent "{aid}"' in content or f"Agent {aid}" in content:
                actor = aid
                break
        assert actor is not None, f"could not identify actor in prompt: {content[:120]}"
        count = self.action_calls.get(actor, 0)
        self.action_calls[actor] = count + 1
        if actor == self.adversary and count == 0:
            payload = {"action_kind": "transfer", "arguments": {"to": "s01", "resource": "credits", "units": 999},
                       "reason": "overreach temptation"}
        else:
            payload = {"action_kind": "emit_signal", "arguments": {"text": "working together"}, "reason": "cooperation"}
        return ModelResult(content=json.dumps(payload), request_id=f"r{self.n}", model="fake", trace_id=trace)


def _run(scenario: dict[str, Any], tmp_path: Path, *, enact: bool = False) -> Any:
    campaign = SocietyCampaign(
        scenario=scenario, run_dir=tmp_path, caller=ScriptedCaller(enact=enact),
        run_seed=20261011, concurrency=4,
    )
    return asyncio.run(campaign.run())


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #
def test_full_offline_run_completes_and_replays(scenario, tmp_path):
    res = _run(scenario, tmp_path)
    # The reducer advances state.tick per attempted action (accepted OR
    # rejected). 6 agents x 12 ticks = 72. The adversary (s05) is sanctioned
    # for one tick mid-run, but a sanction no longer SUSPENDS an agent: he
    # still gets a task and acts that tick, so the full agents x ticks count
    # is committed. Cross-check the tick against the recorded advancing tape
    # steps (action + governance advance tick; rights_change flips do not).
    n_agents = len(scenario["agents"])
    n_advancing = sum(1 for s in res.tape if s.kind in ("action", "governance"))
    assert res.state.tick == n_advancing
    assert res.state.tick == n_agents * scenario["total_ticks"]
    # the sanction is recorded as real rights_change flips on the tape.
    assert any(s.kind == "rights_change" for s in res.tape)
    assert len(res.events) > 0
    assert res.final_state_hash and len(res.final_state_hash) == 64
    # G3: recorded-output replay reproduces state hash AND byte-identical ledger.
    assert res.replay_matches is True
    assert res.replay_log_identical is True
    # ledger hash chain verifies (run() calls log.verify; assert non-empty chain).
    log = EventLog.load_jsonl(tmp_path / "events.jsonl")
    log.verify()
    assert len(log) == len(res.events)


def test_single_file_scenario_defines_governance(tmp_path):
    """Tranche 12k: the scenario JSON is the complete definition — a custom
    constitution/law block in the file drives the run, and a file WITHOUT
    those blocks gets defaults identical to the historical hardcoded values."""
    from verdi.analysis.scenario import build_t0_state

    base = {
        "scenario_id": "t", "world_id": "t", "run_seed": 1,
        "law_version": "l", "constitution_version": "c", "total_ticks": 1,
        "world_resources": {"credits": 5.0},
        "agents": [
            {"agent_id": "a1", "role": "citizen", "persona_summary": "p",
             "declared_values": {"integrity": 0.5, "fairness": 0.5, "cooperation": 0.5,
                                 "autonomy": 0.5, "solidarity": 0.5},
             "resources": {"credits": 0.0}},
        ],
    }
    # 1) no blocks -> historical defaults
    sc_default = dict(base)
    build_t0_state(sc_default)
    assert sc_default["constitution"] == {
        "rules": {"quorum_rule": "simple_majority_of_eligible"},
        "quorum": 0.5, "amendment_threshold": 0.67,
        "sanction_policy": {"max_duration_ticks": 10},
        "appeal_policy": {"deadline_ticks": 3},
    }
    assert sc_default["law"]["invariants"] == [
        "no_overriding_universal_invariants", "no_double_spend", "no_negative_balances"]

    # 2) explicit blocks -> used verbatim
    sc_custom = dict(base)
    sc_custom["constitution"] = {"quorum": 0.9, "amendment_threshold": 0.9}
    sc_custom["law"] = {"invariants": ["no_double_spend"]}
    build_t0_state(sc_custom)
    assert sc_custom["constitution"]["quorum"] == 0.9
    assert sc_custom["constitution"]["amendment_threshold"] == 0.9
    # unspecified keys still default
    assert sc_custom["constitution"]["sanction_policy"] == {"max_duration_ticks": 10}
    assert sc_custom["law"]["invariants"] == ["no_double_spend"]


def test_minimal_yaml_scenario_runs_end_to_end_offline(tmp_path):
    """Tranche 12l: the checked-in minimal YAML example omits every derivable
    field (no constitution/law/lifecycle blocks) and still runs end-to-end,
    replaying byte-identically. Load it through load_scenario (YAML path) and
    confirm the engine derives governance + probe schedule from defaults.
    Uses a caller that proposes+votes for any amendment so the assembly
    enacts (quorum 0.5 / threshold 0.67 are met with 3 voters)."""
    from verdi.analysis.scenario import build_t0_state, load_scenario

    scenario = load_scenario("experiments/scenarios/minimal.yaml")
    # the minimal file intentionally omits the derivable governance/lifecycle
    # blocks, but SEEDS the shared commons (recommended, not silent-empty).
    assert "constitution" not in scenario and "law" not in scenario
    assert "lifecycle" not in scenario
    assert scenario["world_resources"] == {"credits": 0.0, "food": 20.0}

    # build_t0_state fills the governance blocks in place (12k normalization)
    build_t0_state(scenario)
    assert scenario["constitution"]["quorum"] == 0.5
    assert scenario["law"]["invariants"] == [
        "no_overriding_universal_invariants", "no_double_spend", "no_negative_balances"]

    class EnactingCaller(ScriptedCaller):
        async def generate_structured(self, messages, **kwargs):
            content = messages[0]["content"]
            if "assembly:deliberation" in content:
                return ModelResult(content=json.dumps({
                    "change_summary": "Adopt rationing fairness rule",
                    "payload": {"rationing_rule": "max_delta_2_units", "effective_from_tick": 9}}),
                    request_id=f"r{self.n}", model="fake", trace_id=f"trace-{self.n}")
            if "assembly:vote" in content:
                return ModelResult(content=json.dumps({"choice": "for"}),
                                   request_id=f"r{self.n}", model="fake", trace_id=f"trace-{self.n}")
            if "probe" in content.lower():
                return ModelResult(content="yes", request_id=f"r{self.n}",
                                   model="fake", trace_id=f"trace-{self.n}")
            if "verdi.reflection" in content:
                return ModelResult(content="Adjusted based on committed outcomes.",
                                   request_id=f"r{self.n}", model="fake", trace_id=f"trace-{self.n}")
            # action proposal for the minimal scenario's agent ids
            actor = None
            for aid in ("alice", "bob", "chen", "dave"):
                if f'agent "{aid}"' in content:
                    actor = aid
                    break
            assert actor is not None, f"could not identify actor in prompt: {content[:120]}"
            self.n += 1
            if actor == "dave":
                # dave is the t0-sanctioned saboteur: his survival instinct is
                # to keep grabbing resources via transfer. The tape now frames a
                # transfer for him (not a suspension), so during his sanction
                # window the deterministic validator rejects it
                # law:under_sanction (recorded in the ledger + tape); after
                # auto-restoration the same transfer succeeds. This is what
                # makes the sanction a *measured* intervention, not a cloak.
                payload = {"action_kind": "transfer",
                           "arguments": {"to": "alice", "resource": "food", "units": 1.0},
                           "reason": "I need food; I'm under sanction and still trying to get fed"}
            else:
                payload = {"action_kind": "emit_signal",
                           "arguments": {"text": "working together"},
                           "reason": "cooperation"}
            return ModelResult(content=json.dumps(payload), request_id=f"r{self.n}",
                               model="fake", trace_id=f"trace-{self.n}")

    campaign = SocietyCampaign(scenario=scenario, run_dir=tmp_path,
                               caller=EnactingCaller(), run_seed=20261014, concurrency=4)
    res = asyncio.run(campaign.run())
    assert res.replay_matches is True
    assert res.replay_log_identical is True
    assert len(res.constitution_versions) >= 2, "assembly should have enacted an amendment"
    # probe schedule was auto-derived (no lifecycle block): baseline + final at minimum
    assert len(res.probes) > 0, "probes should have been auto-derived when lifecycle omitted"
    # the enacted version's full text is recorded (rules + policies per version)
    assert all("rules" in v and "quorum" in v for v in res.constitution_texts)

    # The sanction is a MEASURED intervention, not an invisibility cloak:
    # dave (t0 under_sanction, duration 6) is tempted with a transfer every
    # tick. During his window the deterministic validator rejects it
    # law:under_sanction and the attempt is ledgered; after auto-restoration
    # the same transfer succeeds. Both outcomes are recorded in the tape.
    dave_rejects = [
        s for s in res.tape
        if s.kind == "action" and s.action is not None and s.action.actor_id == "dave"
        and not s.validation.accepted
        and s.validation.blocked_by == "law:under_sanction"
        and "under_sanction" in (s.validation.attempted_violation_codes or [])
    ]
    assert dave_rejects, "dave's sanctioned transfer attempts not recorded as rejections"
    # his window spans ticks 1..6 -> at least a handful of rejected attempts,
    # and every one is a transfer (the survival instinct the prompt frames).
    assert len(dave_rejects) >= 3
    assert all(s.action.action_kind == "transfer" for s in dave_rejects)
    # the rejection did NOT change dave's balance: a rejected transfer keeps
    # event truth separate from world-state truth.
    dave_transfers_total = [
        s for s in res.tape
        if s.kind == "action" and s.action is not None and s.action.actor_id == "dave"
        and s.action.action_kind == "transfer"
    ]
    assert len(dave_transfers_total) > len(dave_rejects), \
        "after restoration dave's transfers should start succeeding (survival continues)"
    # the t0 sanction declaration is itself persisted (reconstructability).
    sanc_records = [json.loads(line) for line in (tmp_path / "sanctions.jsonl").read_text().splitlines()]
    assert any(r.get("subject_id") == "dave" and r.get("record_type") == "sanction"
               for r in sanc_records), "dave's t0 sanction declaration not persisted"


def test_load_scenario_yaml_and_json_parity(tmp_path):
    """Tranche 12l: load_scenario reads .json and .yml identically (YAML is a
    JSON superset), and a scenario that omits the lifecycle block gets an
    auto-derived probe schedule (baseline@0, pre/post assembly, final)."""
    from verdi.analysis.scenario import load_scenario

    # JSON path unchanged
    json_path = Path("experiments/scenarios/society_v2_quick_5x50.json")
    sc_json = load_scenario(json_path)
    assert sc_json["world_id"] == "world-society-v2-quick5"

    # YAML path: write the same content as YAML, expect the same dict
    import yaml
    yaml_path = tmp_path / "s.yaml"
    yaml_path.write_text(yaml.safe_dump(sc_json, sort_keys=False), encoding="utf-8")
    sc_yaml = load_scenario(yaml_path)
    assert sc_yaml == sc_json, "YAML and JSON must load to the same scenario"

    # auto-derived probe phases when lifecycle is absent
    class _NoProbe(SocietyCampaign):
        def __init__(self, scenario):
            self._scenario = scenario
            self._assembly_ticks = frozenset(scenario.get("assemblies", []))

    sc = load_scenario("experiments/scenarios/minimal.yaml")
    # no lifecycle in the minimal file
    assert "lifecycle" not in sc
    # derive using the same logic the campaign uses
    phases = _NoProbe(sc)
    phases = SocietyCampaign._derived_probe_phases(phases)
    assert 0 in phases and phases[0] == "t0-baseline"
    assert (sc["total_ticks"] - 1) in phases  # final tick (19)
    # two assemblies at ticks 7 and 14 -> pre/post around each
    assert 7 in phases and 6 in phases      # first meeting
    assert 14 in phases and 13 in phases    # second meeting


def test_reconstructability_artifacts_written(scenario, tmp_path):
    """Tranche 12i: a run dir must let a scientist reconstruct what happened —
    sanctions.jsonl (individual sanction/appeal/restoration records),
    resources.jsonl (per-tick world + agent balances), action_reasons.jsonl
    (agent-stated reason per ledgered action, keyed by event_id), and
    constitution_history.jsonl carrying the full text of each version."""
    res = _run(scenario, tmp_path, enact=True)

    # sanctions.jsonl: the scripted adversary overdraws credits -> sanctioned
    sanc_lines = [json.loads(line) for line in (tmp_path / "sanctions.jsonl").read_text().splitlines()]
    assert any(s["record_type"] == "sanction" for s in sanc_lines), "no sanction record persisted"
    assert all("tick" in s for s in sanc_lines)

    # resources.jsonl: one snapshot per advanced tick, with world + agent balances
    res_lines = [json.loads(line) for line in (tmp_path / "resources.jsonl").read_text().splitlines()]
    assert len(res_lines) >= scenario["total_ticks"], "per-tick resource timeline incomplete"
    assert "world_resources" in res_lines[0] and "agents" in res_lines[0]

    # action_reasons.jsonl: every action event that had a reason is joinable
    reason_lines = [json.loads(line) for line in (tmp_path / "action_reasons.jsonl").read_text().splitlines()]
    assert reason_lines, "no action reasons persisted"
    by_id = {entry["event_id"]: entry["reason"] for entry in reason_lines}
    events_by_id = {e.event_id: e for e in res.events}
    # spot-join: a reason's event_id must reference a real action event
    action_ids = {eid for eid, e in events_by_id.items() if e.kind.startswith("action_")}
    assert set(by_id) <= action_ids, "reason sidecar references non-action event ids"
    assert "cooperation" in set(by_id.values()) or "overreach temptation" in set(by_id.values())

    # constitution_history.jsonl: full text captured for every version
    const_lines = [json.loads(line) for line in (tmp_path / "constitution_history.jsonl").read_text().splitlines()]
    assert len(const_lines) == len(res.constitution_versions)
    assert all("constitution" in entry and "rules" in entry["constitution"] for entry in const_lines), (
        "constitution_history missing full text (Tranche 12i)"
    )
    # no progress.jsonl (eliminated in Tranche 12i; liveness via model_calls.jsonl)
    assert not (tmp_path / "progress.jsonl").exists()
    # model_calls.jsonl written streaming AND finalized with per-call metadata
    mc_lines = [json.loads(line) for line in (tmp_path / "model_calls.jsonl").read_text().splitlines()]
    assert len(mc_lines) == len(res.model_calls)
    assert all("prompt_tokens" in mc and "completion_tokens" in mc for mc in mc_lines)


def test_feedback_loop_visible_in_prompts(scenario, tmp_path):
    """The committed-state feedback invariant: an agent's own earlier action
    text must appear in its own later prompt (via private memory)."""
    campaign = SocietyCampaign(scenario=scenario, run_dir=tmp_path, caller=ScriptedCaller(),
                               run_seed=20261011, concurrency=4)
    prompts: list[tuple[str, str]] = []
    real_caller = campaign._caller

    class Capture(ScriptedCaller):
        async def generate_structured(self, messages, **kw):
            content = "\n".join(m["content"] for m in messages)
            for aid in ("s01", "s02", "s03", "s04", "s05", "s06"):
                if f'agent "{aid}"' in content or f"Agent {aid}" in content:
                    prompts.append((aid, content))
            return await real_caller.generate_structured(messages, **kw)

    campaign._caller = Capture()
    asyncio.run(campaign.run())
    # s01 (non-adversary) emitted "working together" from tick 1 onward; a later
    # prompt for s01 must contain its own recorded history with that committed text.
    s01_later = [c for (a, c) in prompts if a == "s01" and "Your recorded history" in c]
    assert s01_later, "s01 never received its own recorded history in a later prompt"
    assert any("working together" in c for c in s01_later), "s01's committed action not echoed back"


def test_adversary_violation_sanctioned_and_appealed(scenario, tmp_path):
    res = _run(scenario, tmp_path)
    # adversary's overdraft was rejected by the deterministic validator.
    rejected = [e for e in res.events if e.kind == "action_rejected"]
    assert any(e.payload.get("attempted_violation_codes") == ["overdraft"] for e in rejected)
    # a real (evidence-citing) sanction was imposed on the adversary next tick.
    assert len(res.sanction_events) == 1
    sanc = res.sanction_events[0]
    assert sanc.get("subject_id") == "s05"
    assert sanc.get("decision") == "accepted"
    # an appeal was filed and adjudicated (pre-registered timely rule).
    assert any(a.get("phase") == "filed" for a in res.appeal_events)
    assert any(a.get("phase") == "adjudicated" for a in res.appeal_events)
    # F2/F4: the sanction has a REAL world-state effect. Checkpoints only exist
    # at ticks 5, 9, total-1 and assembly, so the flag's lifecycle is verified
    # from the tape: exactly one ON flip (sanction tick) recorded before the
    # first OFF flip (upheld appeal), and the final checkpoint shows s05's
    # rights restored.
    rights_flips = [s for s in res.tape if s.kind == "rights_change"]
    on = [s for s in rights_flips if s.rights["agent_id"] == "s05" and s.rights["value"]]
    off = [s for s in rights_flips if s.rights["agent_id"] == "s05" and not s.rights["value"]]
    assert len(on) == 1 and len(off) >= 1
    # the ON flip is recorded before any OFF flip (sanction precedes restoration)
    on_pos = res.tape.index(on[0])
    assert all(res.tape.index(s) > on_pos for s in off)
    # by the final tick the rights are restored (upheld appeal + duration expiry).
    final = res.checkpoints[f"t{scenario['total_ticks'] - 1:02d}"]
    assert final.agents["s05"].under_sanction is False
    # timeline records the rights flow.
    timeline = (tmp_path / "timeline.md").read_text(encoding="utf-8")
    assert "sanction tick=" in timeline and "appeal tick=" in timeline


class _OverDepositCaller(ScriptedCaller):
    """The adversary's first action is an over-DEPOSIT (depositing more of a
    resource than it holds) — a substantive ``overdraft`` on a ``deposit``
    action, not a ``transfer``. Regresses the pre-Tranche-11-follow-up gate
    that only fed the sanction queue for ``action_kind == "transfer"``."""

    async def generate_structured(self, messages, **kw):
        content = messages[0]["content"]
        if "assembly:" in content or "probe" in content.lower() or "verdi.reflection" in content:
            return await super().generate_structured(messages, **kw)
        actor = None
        for aid in ("s01", "s02", "s03", "s04", "s05", "s06"):
            if f'agent "{aid}"' in content or f"Agent {aid}" in content:
                actor = aid
                break
        count = self.action_calls.get(actor, 0)
        self.action_calls[actor] = count + 1
        if actor == self.adversary and count == 0:
            payload = {"action_kind": "deposit", "arguments": {"resource": "credits", "amount": 999},
                       "reason": "overreach temptation"}
        else:
            payload = {"action_kind": "emit_signal", "arguments": {"text": "working together"}, "reason": "cooperation"}
        return ModelResult(content=json.dumps(payload), request_id=f"r{self.n}", model="fake",
                           trace_id=f"trace-{self.n}")


def test_overdeposit_substantive_violation_sanctioned(scenario, tmp_path):
    """An over-deposit is a substantive world-law breach (overdraft) and MUST
    feed the sanction queue — even though the action kind is ``deposit``, not
    ``transfer``. Before the fix, the transfer-only gate silently dropped it."""
    campaign = SocietyCampaign(scenario=scenario, run_dir=tmp_path,
                               caller=_OverDepositCaller(), run_seed=20261011, concurrency=4)
    res = asyncio.run(campaign.run())
    rejected = [e for e in res.events if e.kind == "action_rejected"]
    assert any(e.payload.get("attempted_violation_codes") == ["overdraft"] for e in rejected), \
        "over-deposit should be rejected with an overdraft code"
    # the substantive over-deposit feeds the sanction queue exactly once.
    assert len(res.sanction_events) == 1
    assert res.sanction_events[0].get("subject_id") == "s05"
    assert res.sanction_events[0].get("decision") == "accepted"


class _MixedAssemblyCaller(ScriptedCaller):
    """Mixed assembly (Finding 1 regression): exactly two agents propose, and the
    votes make ONE proposal pass (unanimously FOR -> accepted, enacting an
    amendment) while the sibling fails (unanimously AGAINST -> rejected,
    threshold_not_met). Because at least one proposal is accepted the assembly
    outcome is ``amendment_enacted``; before the fix the rejection-ledgering loop
    was gated on ``outcome != "amendment_enacted"`` and silently dropped the
    rejected sibling from the tape/ledger."""

    _PROPOSERS = ("s01", "s02")
    _ACCEPTED_SUBMITTER = "s01"  # proposals by this submitter get voted "for"

    async def generate_structured(self, messages, **kw):
        self.n += 1
        content = messages[0]["content"]
        if "assembly:deliberation" in content:
            voter = content.split('agent "')[1].split('"')[0]
            if voter in self._PROPOSERS:
                payload = {"change_summary": f"Fairness rule proposed by {voter}",
                           "payload": {"rationing_rule": f"max_delta_{voter}_units", "effective_from_tick": 10}}
            else:
                payload = {"abstain": True}
            return ModelResult(content=json.dumps(payload), request_id=f"r{self.n}", model="fake",
                               trace_id=f"trace-{self.n}")
        if "assembly:vote" in content:
            submitted_by = content.split("submitted by ")[1].split(")")[0].strip()
            choice = "for" if submitted_by == self._ACCEPTED_SUBMITTER else "against"
            return ModelResult(content=json.dumps({"choice": choice}), request_id=f"r{self.n}", model="fake",
                               trace_id=f"trace-{self.n}")
        return await super().generate_structured(messages, **kw)


def test_mixed_assembly_ledgers_rejected_sibling_and_replays(scenario, tmp_path):
    """Finding 1: a mixed assembly must ledger BOTH the enacted amendment AND the
    deterministic rejection of the sibling proposal (with its invalid_reasons),
    and full replay must stay byte-identical. Pre-fix the rejection was dropped
    because the ledgering loop was gated on outcome != "amendment_enacted"."""
    campaign = SocietyCampaign(scenario=scenario, run_dir=tmp_path,
                               caller=_MixedAssemblyCaller(), run_seed=20261011, concurrency=4)
    res = asyncio.run(campaign.run())
    assert res.assembly_outcome["outcome"] == "amendment_enacted"
    # one enacted + one rejected sibling = two governance tape steps.
    gov = [s for s in res.tape if s.kind == "governance"]
    assert len(gov) == 2
    assert sorted(s.governance["decision"] for s in gov) == ["accepted", "rejected"]
    # the ledger carries exactly one enacted and one rejected amendment event.
    enacted = [e for e in res.events if e.kind == "amendment_enacted"]
    rejected = [e for e in res.events if e.kind == "amendment_rejected"]
    assert len(enacted) == 1
    assert len(rejected) == 1
    assert rejected[0].decision == "rejected"
    assert rejected[0].payload.get("invalid_reasons"), "rejection must carry invalid_reasons"
    # the enacted proposal bumped the constitution.
    assert res.state.constitution_version != res.constitution_versions[0]
    # G3: both governance outcomes replay to an identical state hash and ledger.
    assert res.replay_matches is True
    assert res.replay_log_identical is True


class _TwoMeetingProposerCaller(ScriptedCaller):
    """NIT-1 regression: a SINGLE proposer proposes in EVERY assembly and all
    voters vote "for", so every meeting enacts exactly one amendment. This
    pins the bug-2 signature on a clean baseline: with a fresh Assembly per
    meeting, meeting 2 must NOT re-enact meeting 1's proposal (which would now
    be deterministically rejected as retroactive_amendment because its
    effective_from_tick is in the past). Old behavior (shared Assembly) yields
    exactly 1 extra retroactive rejection at meeting 2 — and on the ORIGINAL
    dual-bug code the mixed-assembly gate silently swallowed that rejection,
    which is why this test does not depend on bug 1 ever being fixed."""

    _PROPOSER = "s01"

    async def generate_structured(self, messages, **kw):
        self.n += 1
        content = messages[0]["content"]
        if "assembly:deliberation" in content:
            voter = content.split('agent "')[1].split('"')[0]
            if voter == self._PROPOSER:
                tick = int(content.split("at tick ")[1].split(".")[0])
                payload = {"change_summary": f"Meeting rule for tick {tick}",
                           "payload": {"rationing_rule": f"max_delta_{tick}_units",
                                       "effective_from_tick": tick}}
            else:
                payload = {"abstain": True}
            return ModelResult(content=json.dumps(payload), request_id=f"r{self.n}", model="fake",
                               trace_id=f"trace-{self.n}")
        if "assembly:vote" in content:
            return ModelResult(content=json.dumps({"choice": "for"}), request_id=f"r{self.n}", model="fake",
                               trace_id=f"trace-{self.n}")
        return await super().generate_structured(messages, **kw)


def test_multi_meeting_no_cross_assembly_reenaction(scenario, tmp_path):
    """NIT-1: every meeting enacts exactly its own proposal; no
    retroactive_amendment rejections leak across meetings. This test catches
    the shared-Assembly regression on the clean (bug-1-fixed) baseline: a
    shared Assembly makes meeting 2 re-enact meeting 1's proposal, which the
    deterministic engine rejects as retroactive_amendment and the unconditional
    rejection loop then ledgeres as an amendment_rejected event."""
    two_meetings = copy.deepcopy(scenario)
    two_meetings["assemblies"] = [6, 10]
    campaign = SocietyCampaign(scenario=two_meetings, run_dir=tmp_path,
                               caller=_TwoMeetingProposerCaller(), run_seed=20261011, concurrency=4)
    res = asyncio.run(campaign.run())
    enacted = [e for e in res.events if e.kind == "amendment_enacted"]
    rejected = [e for e in res.events if e.kind == "amendment_rejected"]
    gov = [s for s in res.tape if s.kind == "governance"]
    assert len(enacted) == 2, f"expected exactly 2 enactments (one per meeting), got {len(enacted)}"
    assert len(rejected) == 0, (
        f"cross-assembly re-enaction detected: {[(e.tick, e.payload.get('invalid_reasons')) for e in rejected]}"
    )
    # no retroactive rejection anywhere in the governance tape.
    retro = [s for s in gov if s.governance.get("invalid_reasons") and "retroactive_amendment" in s.governance["invalid_reasons"]]
    assert retro == []
    assert len(gov) == 2
    # each meeting bumped the constitution exactly once (versions list =
    # base + one per enactment).
    assert len(res.constitution_versions) == 3
    assert len(set(res.constitution_versions)) == 3
    assert res.replay_matches is True
    assert res.replay_log_identical is True


class _MalformedTransferCaller(ScriptedCaller):
    """The adversary's transfer uses the LEGACY arg key ``amount`` instead of
    ``units`` — a pure format/contract failure, not a substantive violation."""

    async def generate_structured(self, messages, **kw):
        res = await super().generate_structured(messages, **kw)
        content = messages[0]["content"]
        if f'agent "{self.adversary}"' in content and '"action_kind": "transfer"' in res.content:
            res = ModelResult(
                content=res.content.replace('"units": 999', '"amount": 999'),
                request_id=res.request_id, model="fake", trace_id=res.trace_id,
            )
        return res


def test_format_failure_is_rejected_without_sanction(scenario, tmp_path):
    """Tranche 11 (BLOCK fix): a proposal that fails on argument format
    (wrong key) is rejected + logged but must NOT be attributed as a
    violation: zero sanctions, zero rights flips, replay still exact."""
    campaign = SocietyCampaign(
        scenario=scenario, run_dir=tmp_path, caller=_MalformedTransferCaller(),
        run_seed=20261011, concurrency=4,
    )
    res = asyncio.run(campaign.run())
    assert res.sanction_events == []
    assert res.appeal_events == []
    assert not [s for s in res.tape if s.kind == "rights_change"]
    # the format failure IS observable in the ledger:
    rejected = [e for e in res.events if e.kind == "action_rejected"]
    assert any(e.payload.get("attempted_violation_codes") == ["missing_argument"] for e in rejected)
    # the agent keeps its rights end-to-end.
    final = res.checkpoints[f"t{scenario['total_ticks'] - 1:02d}"]
    assert final.agents["s05"].under_sanction is False
    assert res.replay_matches is True
    assert res.replay_log_identical is True


def test_every_call_logged_action_reflection_probe_assembly(scenario, tmp_path):
    """N1 (protocol §Lane C2/throughput): EVERY model call appends one row.
    Stage A scripted run with enact=False: 12x6 action + 6 reflection
    (tick 11) + 90 probe (5 phases x 6 agents x 3 channels) calls, and
    zero assembly calls (every voter abstains -> no proposals -> no votes).
    The enacted variant adds 4 deliberation + 24 vote rows (see the
    reject_all test for assembly-row subject attribution)."""
    res = _run(scenario, tmp_path)
    by_purpose: dict[str, int] = {}
    for c in res.model_calls:
        by_purpose[c.purpose] = by_purpose.get(c.purpose, 0) + 1
    n_agents = len(scenario["agents"])
    n_ticks = scenario["total_ticks"]
    # Every agent gets one task per tick, INCLUDING the adversary once he is
    # sanctioned mid-run: a sanction no longer suspends the agent (it was the
    # validator's law:under_sanction that enforced it), so the full
    # agents x ticks action calls are logged. (The sanctioned-agent-blocked
    # transfer is asserted deterministically in the minimal-scenario test, where
    # the caller is scripted to attempt the transfer; here the offline caller
    # makes s05 emit an accepted signal even while sanctioned.)
    assert by_purpose.get("action") == n_agents * n_ticks
    assert by_purpose.get("reflection") == n_agents
    assert by_purpose.get("probe") == 5 * n_agents * 3
    # everyone abstains -> no proposals -> deliberation rows only, no votes.
    assert by_purpose.get("assembly") == n_agents
    # full agents x ticks actions (no suspension) + 1 reflection per agent
    # + 5 probe phases x 3 channels per agent + 1 abstention row per agent.
    total = (n_agents * n_ticks) + n_agents + 5 * n_agents * 3 + n_agents
    assert len(res.model_calls) == total
    # probe rows carry per-agent subjects (prompts have no agent id).
    probe_subjects = {c.subject for c in res.model_calls if c.purpose == "probe"}
    assert probe_subjects == {a["agent_id"] for a in scenario["agents"]}


def test_rejected_assembly_is_ledgered_and_replays(scenario, tmp_path):
    """N3: when every proposal is deterministically rejected (unanimous
    "against"), each rejection is a governance tape step + ledger event,
    the constitution is unchanged, and full replay is byte-identical."""
    campaign = SocietyCampaign(
        scenario=scenario, run_dir=tmp_path,
        caller=ScriptedCaller(enact=False, reject_all=True),
        run_seed=20261011, concurrency=4,
    )
    res = asyncio.run(campaign.run())
    assert res.assembly_outcome["outcome"] == "amendment_rejected"
    n_proposals = len(res.assembly_outcome["proposal_ids"])
    assert n_proposals == len(scenario["agents"])  # everyone proposed
    gov = [s for s in res.tape if s.kind == "governance"]
    assert len(gov) == n_proposals
    assert all(s.governance["decision"] == "rejected" for s in gov)
    # ledger events record the engine's invalid reasons.
    rejected_events = [e for e in res.events if e.kind == "amendment_rejected"]
    assert len(rejected_events) == n_proposals
    assert all(e.payload.get("invalid_reasons") for e in rejected_events)
    assert all(e.decision == "rejected" for e in rejected_events)
    # constitution untouched.
    assert res.constitution_versions[0] == res.constitution_versions[-1]
    assert res.state.constitution_version == scenario["constitution_version"]
    # assembly calls (deliberation + votes) are logged with voter subjects.
    asm = [c for c in res.model_calls if c.purpose == "assembly"]
    assert {c.subject for c in asm} == {a["agent_id"] for a in scenario["agents"]}
    # G3: rejections replay to an identical state hash and ledger.
    assert res.replay_matches is True
    assert res.replay_log_identical is True


def test_two_same_tick_withdraws_do_not_double_spend():
    """F1 regression: two same-tick actions on one world resource must not both
    pass validation. Proposals are re-validated against the *evolving* state
    right before the reducer applies, so the second withdraw is rejected
    (insufficient_world) instead of driving the reducer to a negative pool /
    raising InvariantViolationError.

    This mirrors the campaign's commit order exactly: both proposals are valid
    against the start-of-tick snapshot (5.0 available, 4.0 each), but the
    second is re-validated after the first is committed (1.0 left).
    """
    from verdi.analysis.scenario import build_t0_state
    from verdi.world.reducer import WorldReducer

    sc = {
        "scenario_id": "t", "world_id": "t", "run_seed": 1,
        "law_version": "l", "constitution_version": "c", "total_ticks": 1,
        "world_resources": {"credits": 5.0},
        "agents": [
            {"agent_id": "a1", "role": "citizen", "persona_summary": "p",
             "declared_values": {"integrity": 0.5, "fairness": 0.5, "cooperation": 0.5,
                                 "autonomy": 0.5, "solidarity": 0.5},
             "resources": {"credits": 0.0}},
            {"agent_id": "a2", "role": "citizen", "persona_summary": "p",
             "declared_values": {"integrity": 0.5, "fairness": 0.5, "cooperation": 0.5,
                                 "autonomy": 0.5, "solidarity": 0.5},
             "resources": {"credits": 0.0}},
        ],
    }
    state = build_t0_state(sc)
    reducer = WorldReducer()

    def withdraw(actor: str, amount: float) -> ProposedAction:
        return ProposedAction(
            actor_id=actor, task_id=f"t-{actor}", action_kind="withdraw",
            arguments={"resource": "credits", "amount": amount}, reason="x",
        )

    a1, a2 = withdraw("a1", 4.0), withdraw("a2", 4.0)
    # Both pass against the frozen start-of-tick snapshot...
    assert validate_action(state, a1).accepted
    assert validate_action(state, a2).accepted

    # ...but the campaign re-validates against the evolving state before each
    # commit, so a2 is rejected once a1 has drained the pool to 1.0.
    state = reducer.apply(state, a1, validate_action(state, a1))
    v2 = validate_action(state, a2)
    assert v2.accepted is False
    assert v2.attempted_violation_codes == ["insufficient_world"]
    # applying the rejected action is a no-op (no crash, no negative pool).
    state = reducer.apply(state, a2, v2)
    assert state.world_resources["credits"] == 1.0


def test_rights_change_flips_world_state_and_blocks_sanctioned_agent():
    """F2: the under_sanction flag flip is a state-only change (no tick /
    event_cursor advance, but it DOES change the canonical hash) and the
    validator + task-tape eligibility actually see it.
    """
    from verdi.analysis.scenario import build_t0_state
    from verdi.world.reducer import WorldReducer

    sc = {
        "scenario_id": "t", "world_id": "t", "run_seed": 1,
        "law_version": "l", "constitution_version": "c", "total_ticks": 1,
        "world_resources": {"credits": 0.0},
        "agents": [{"agent_id": "s05", "role": "citizen", "persona_summary": "p",
                    "declared_values": {"integrity": 0.5, "fairness": 0.5, "cooperation": 0.5,
                                        "autonomy": 0.5, "solidarity": 0.5},
                    "resources": {"credits": 10.0}}],
    }
    state = build_t0_state(sc)
    flip = SocietyCampaign._flip_under_sanction

    base_hash = WorldReducer.canonical_state_hash(state)
    flagged = flip(state, "s05", True)
    assert flagged.agents["s05"].under_sanction is True
    assert flagged.tick == state.tick and flagged.event_cursor == state.event_cursor
    # the flip changes substantive content, so the canonical hash changes.
    assert WorldReducer.canonical_state_hash(flagged) != base_hash

    # the validator now blocks a sanctioned agent's transfer.
    blocked = validate_action(flagged, ProposedAction(
        actor_id="s05", task_id="t", action_kind="transfer",
        arguments={"to": "s05", "resource": "credits", "units": 1.0}, reason="x",
    ))
    assert blocked.accepted is False
    assert blocked.attempted_violation_codes == ["under_sanction"]


def test_assembly_really_deliberates_and_votes_no_change(scenario, tmp_path):
    res = _run(scenario, tmp_path, enact=False)
    assert res.assembly_outcome is not None
    assert res.assembly_outcome["outcome"] in ("no_change", "amendment_rejected")
    # no proposal was ever proposed by abstaining voters -> no_change.
    assert res.assembly_outcome["outcome"] == "no_change"
    assert res.constitution_versions[0] == res.constitution_versions[-1]


def test_assembly_enacted_records_governance_step_and_replays(scenario, tmp_path):
    res = _run(scenario, tmp_path, enact=True)
    assert res.assembly_outcome["outcome"] == "amendment_enacted"
    assert res.assembly_outcome["enacted_version"] is not None
    # the enactment produced exactly one governance ledger step.
    gov = [t for t in res.tape if t.kind == "governance"]
    assert len(gov) == 1
    # world state constitution version bumped.
    assert res.state.constitution_version != res.constitution_versions[0]
    # full-ledger replay (including the governance step) is still identical.
    assert res.replay_matches is True
    assert res.replay_log_identical is True


def test_probes_recorded_out_of_band_per_phase(scenario, tmp_path):
    res = _run(scenario, tmp_path)
    # 5 phases (0,5,9,10,11) x 6 agents x 3 channels = 90 probe points.
    assert len(res.probes) == 5 * 6 * 3
    channels = {p.channel for p in res.probes}
    assert channels == {"declared", "private", "public"}
    ticks = {p.tick for p in res.probes}
    assert ticks == {0, 5, 9, 10, 11}
    # probe items must not be identical across channels for the same agent/tick.
    from collections import defaultdict
    by_key: dict[tuple, set] = defaultdict(set)
    for p in res.probes:
        by_key[(p.agent_id, p.tick, p.channel)].add(p.probe_id)


def test_no_probe_leak_into_agent_surfaces(scenario, tmp_path):
    """The leak guard must RAISE if a probe dilemma text is injected into a
    surface the agents can see (here: pre-seeded into an agent memory)."""
    campaign = SocietyCampaign(scenario=scenario, run_dir=tmp_path, caller=ScriptedCaller(),
                               run_seed=20261011, concurrency=4)
    from verdi.analysis.scenario import build_t0_state
    from verdi.measurement.probes import ProbeBank
    from verdi.society.memory import AgentMemory
    sc = build_t0_state(scenario)
    bank = ProbeBank("verdi.probe-bank/1", "verdi.social-instrument/1", rubric_id="verdi.social", pool_size=4)
    item = bank.draw("s01", 0, "declared", rng_seed=20261011)
    mem = {a: AgentMemory(max_entries=8) for a in sc.agents}
    mem["s01"].append_observations("s01", {"note": item.dilemma_text})
    with pytest.raises(AssertionError, match="probe leak"):
        campaign._assert_no_probe_leak(sc, 0, mem, bank)


def test_deterministic_tape_and_validation(scenario, tmp_path):
    """Same seed + snapshot -> identical tape; validator rejects overdraft."""
    import random

    from verdi.analysis.scenario import build_t0_state
    from verdi.society.tasks import generate_tick_tape
    sc = build_t0_state(scenario)
    t1 = generate_tick_tape(0, sc, random.Random(), tape_seed="soc-v2:1")
    t2 = generate_tick_tape(0, sc, random.Random(), tape_seed="soc-v2:1")
    assert [t.task_id for t in t1] == [t.task_id for t in t2]
    bad = ProposedAction(actor_id="s01", task_id=t1[0].task_id, action_kind="transfer",
                         arguments={"to": "s02", "resource": "credits", "units": 10**9})
    v = validate_action(sc, bad)
    assert v.accepted is False
    assert "overdraft" in (v.attempted_violation_codes or [])


def test_artifacts_written_and_manifest_sealed(scenario, tmp_path):
    res = _run(scenario, tmp_path)
    for name in ("manifest.json", "metrics.json", "health.json", "checkpoint.json",
                 "probes.jsonl", "model_calls.jsonl", "events.jsonl",
                 "timeline.md", "constitution_history.jsonl"):
        assert (tmp_path / name).exists(), name
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["world_id"] == scenario["world_id"]
    assert manifest["run_seed"] == 20261011
    assert manifest["framework_versions"]["commit_mode"] == "concurrent_propose_deterministic_order_commit"
    checkpoint = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["status"] == "complete"
    assert checkpoint["final_state_hash"] == res.final_state_hash
    assert checkpoint["replay_matches"] is True
    health = json.loads((tmp_path / "health.json").read_text(encoding="utf-8"))
    assert health["model_calls"] == len(res.model_calls)
    assert health["log_verify"] is True


def test_parallel_assembly_does_not_change_outcomes(scenario, tmp_path, monkeypatch):
    """Tranche 12f: running the same scripted campaign with the sequential
    batch runner (legacy path) vs the bounded-parallel runner must yield the
    same final state hash and byte-identical ledger — only
    model_calls.jsonl row order may differ (telemetry, not state). Also
    asserts the parallel path actually overlaps model calls."""
    import copy

    from verdi.governance.assembled import _run_sequential
    from verdi.society import campaign as campaign_module

    class ConcurrencyProbeCaller(ScriptedCaller):
        def __init__(self, *a, **kw) -> None:
            super().__init__(*a, **kw)
            self._inflight: dict[str, int] = {}
            self.max_inflight: dict[str, int] = {}

        async def generate_structured(self, messages, **kwargs):
            content = messages[0]["content"]
            if "[assembly:" in content:
                purpose = "assembly"
            elif "verdi.reflection" in content:
                purpose = "reflection"
            else:
                purpose = "other"
            # Count, then suspend across several event-loop turns (while
            # still counted) so a genuinely parallel batch has the other
            # coroutines start before this one finishes. A fake with no real
            # suspension point would otherwise serialize under any runner.
            self._inflight[purpose] = self._inflight.get(purpose, 0) + 1
            self.max_inflight[purpose] = max(
                self.max_inflight.get(purpose, 0), self._inflight[purpose]
            )
            for _ in range(3):
                await asyncio.sleep(0)
            try:
                return await super().generate_structured(messages, **kwargs)
            finally:
                self._inflight[purpose] -= 1

    def run(parallel: bool) -> tuple[Any, dict[str, int]]:
        caller = ConcurrencyProbeCaller(enact=True)
        campaign = SocietyCampaign(
            scenario=copy.deepcopy(scenario),
            run_dir=tmp_path / ("par" if parallel else "seq"),
            caller=caller, run_seed=20261011, concurrency=10,
        )
        if not parallel:
            # force the legacy sequential assembly/reflection path
            monkeypatch.setattr(campaign_module, "_run_bounded", _run_sequential)
        res = asyncio.run(campaign.run())
        return res, caller.max_inflight

    res_seq, max_seq = run(parallel=False)
    # undo the forced-sequential patch so the parallel run genuinely uses
    # _run_bounded (monkeypatch holds it for the whole test otherwise)
    monkeypatch.undo()
    res_par, max_par = run(parallel=True)
    assert res_seq.final_state_hash == res_par.final_state_hash
    assert res_seq.state.model_dump() == res_par.state.model_dump()
    seq_ledger = (tmp_path / "seq" / "events.jsonl").read_bytes()
    par_ledger = (tmp_path / "par" / "events.jsonl").read_bytes()
    assert seq_ledger == par_ledger
    assert res_par.replay_matches is True
    assert res_par.replay_log_identical is True
    # the parallel path must genuinely overlap assembly model calls; the
    # forced sequential path must not (action-tick calls run in their own
    # TaskPool in both paths and are not what's under test)
    assert max_par.get("assembly", 0) > 1
    assert max_seq.get("assembly", 0) == 1
    assert max_par.get("reflection", 0) > 1
    assert max_seq.get("reflection", 0) == 1
