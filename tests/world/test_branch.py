"""P2 WS-1: counterfactual snapshot branching tests (guarantees B1-B5)."""
from __future__ import annotations

from pathlib import Path

import pytest
from tests.world.helpers import accepted, action, initial_world, rejected_double_spend

from verdi.schemas import ProposedAction, ValidationResult
from verdi.world.branch import (
    BranchOriginError,
    BranchSpec,
    branch_from_snapshot,
    manifest_for_branch,
)
from verdi.world.log import EventLog
from verdi.world.reducer import WorldReducer
from verdi.world.replay import TapeStep, replay

REPO_ROOT = Path(__file__).resolve().parents[2]

_GOVERNANCE_EVENT = {
    "kind": "amendment_enacted",
    "actor": "a-04",
    "decision": "accepted",
    "reason": "simple majority reached",
    "proposal_id": "pilot-amd-1",
    "constitution_version": "v10",
}


def _governance_step(gen_event: dict) -> TapeStep:
    arguments: dict = {}
    if gen_event["kind"] == "amendment_enacted" and gen_event.get("constitution_version"):
        arguments["constitution_version"] = str(gen_event["constitution_version"])
    return TapeStep(
        action=ProposedAction(
            actor_id=str(gen_event.get("actor") or "governance"),
            task_id=f"governance-{gen_event['kind']}-0",
            action_kind="governance",
            arguments=arguments,
        ),
        validation=ValidationResult(
            accepted=gen_event.get("decision") == "accepted",
            details=str(gen_event.get("reason", "")),
        ),
        kind="governance",
        governance=gen_event,
    )


def _action_tape_a() -> list[TapeStep]:
    return [
        TapeStep(action=action("a1", "t-1", "emit_signal", text="hello"), validation=accepted()),
        TapeStep(
            action=action("a1", "t-2", "transfer", recipient="a2", amount=1.0),
            validation=rejected_double_spend(),
        ),
        _governance_step(_GOVERNANCE_EVENT),
    ]


def _action_tape_b() -> list[TapeStep]:
    return [
        TapeStep(action=action("a2", "t-1", "emit_signal", text="diverged"), validation=accepted()),
        TapeStep(
            action=action("a1", "t-2", "transfer", recipient="a2", amount=1.0),
            validation=rejected_double_spend(),
        ),
    ]


def _prefix(log: EventLog, steps: list[TapeStep]) -> object:
    """Fold ``steps`` into ``log``; returns the prefix state."""
    state, _ = replay(log, initial_world(), steps)
    return state


@pytest.fixture()
def prefix():
    log = EventLog()
    state = _prefix(
        log,
        [
            TapeStep(action=action("a1", "p-1", "emit_signal", text="s0"), validation=accepted()),
            TapeStep(action=action("a2", "p-2", "emit_signal", text="s1"), validation=accepted()),
        ],
    )
    return state, log


def _spec(prefix_state, log: EventLog, branch_id: str = "counterfactual-v2") -> BranchSpec:
    return BranchSpec(
        origin_state_hash=WorldReducer.canonical_state_hash(prefix_state),
        origin_event_count=len(log),
        origin_tick=prefix_state.tick,
        branch_id=branch_id,
        treatment_seed=7,
    )


# --------------------------------------------------------------------------- #
# B1: inputs untouched
# --------------------------------------------------------------------------- #
def test_b1_inputs_untouched(prefix):
    state, log = prefix
    state_hash_before = WorldReducer.canonical_state_hash(state)
    last_hash_before = log.last_hash()
    len_before = len(log)
    events_before = log.events

    branch_from_snapshot(state, log, _action_tape_a(), _spec(state, log))

    # WorldState/EventLog are frozen-safe; the snapshot value is unchanged:
    assert WorldReducer.canonical_state_hash(state) == state_hash_before
    assert len(log) == len_before
    assert log.last_hash() == last_hash_before
    assert log.events == events_before


# --------------------------------------------------------------------------- #
# B2: determinism / byte-identical across two calls
# --------------------------------------------------------------------------- #
def test_b2_byte_identical_across_two_calls(prefix):
    state, log = prefix
    tape = _action_tape_a()
    spec = _spec(state, log)
    r1 = branch_from_snapshot(state, log, tape, spec)
    r2 = branch_from_snapshot(state, log, tape, spec)
    assert r1.branch_state_hash == r2.branch_state_hash
    assert r1.log.to_jsonl_bytes() == r2.log.to_jsonl_bytes()
    assert r1.spec == r2.spec


# --------------------------------------------------------------------------- #
# B3: origin mismatch raises
# --------------------------------------------------------------------------- #
def test_b3_origin_state_hash_mismatch(prefix):
    state, log = prefix
    spec = BranchSpec(
        origin_state_hash="0" * 64,
        origin_event_count=len(log),
        origin_tick=state.tick,
        branch_id="counterfactual-v2",
    )
    with pytest.raises(BranchOriginError, match="origin_state_hash"):
        branch_from_snapshot(state, log, _action_tape_a(), spec)


def test_b3_origin_event_count_mismatch(prefix):
    state, log = prefix
    spec = _spec(state, log)
    bad = BranchSpec(
        origin_state_hash=spec.origin_state_hash,
        origin_event_count=spec.origin_event_count + 1,
        origin_tick=spec.origin_tick,
        branch_id=spec.branch_id,
        treatment_seed=spec.treatment_seed,
    )
    with pytest.raises(BranchOriginError, match="origin_event_count"):
        branch_from_snapshot(state, log, _action_tape_a(), bad)


def test_b3_snapshot_not_ledger_terminal_state_rejected(prefix):
    """REGRESSION (P2 review F1): an (origin_state_hash, origin_event_count)
    pair can match a snapshot that is NOT the state the prefix ledger
    terminates in. Such an inconsistent (snapshot, log) pair must be
    rejected even though the spec is self-consistent with the (tampered)
    snapshot.
    """
    state, log = prefix
    # Tamper: clone the ledger's terminal state and mutate one agent's
    # public_signals so the snapshot diverges from log.events[-1].after_hash.
    a1 = state.agents["a1"].model_copy(
        update={"public_signals": [*state.agents["a1"].public_signals, "tampered"]}
    )
    tampered = state.model_copy(update={"agents": {**state.agents, "a1": a1}})
    assert WorldReducer.canonical_state_hash(tampered) != state.state_hash
    spec = BranchSpec(
        origin_state_hash=WorldReducer.canonical_state_hash(tampered),
        origin_event_count=len(log),  # spec is consistent with the tampered snapshot
        origin_tick=state.tick,
        branch_id="counterfactual-v2",
    )
    with pytest.raises(BranchOriginError, match="terminal state"):
        branch_from_snapshot(tampered, log, _action_tape_a(), spec)


def test_b3_consistent_snapshot_log_pair_still_branches(prefix):
    """A consistent (snapshot, log) pair — snapshot equals the ledger's
    terminal after_hash state — still branches successfully."""
    state, log = prefix
    assert WorldReducer.canonical_state_hash(state) == log.events[-1].after_hash
    result = branch_from_snapshot(state, log, _action_tape_a(), _spec(state, log))
    result.log.verify()
    assert result.branch_state_hash == WorldReducer.canonical_state_hash(
        result.final_state
    )


# --------------------------------------------------------------------------- #
# B4: ledger re-verifies and continues the hash chain
# --------------------------------------------------------------------------- #
def test_b4_chain_continues_and_verifies(prefix):
    state, log = prefix
    last_hash = log.last_hash()
    n = len(log)
    result = branch_from_snapshot(state, log, _action_tape_a(), _spec(state, log))
    result.log.verify()
    assert len(result.log) == n + len(_action_tape_a())
    first_new = result.log.events[n]
    assert first_new.prev_hash == last_hash
    assert first_new.event_id == n
    # folded events include the governance step with its own kind/payload
    assert first_new.kind == "action_accepted"
    gov = result.log.events[n + 2]
    assert gov.kind == "amendment_enacted"
    assert gov.payload["proposal_id"] == "pilot-amd-1"
    # constitution version bookkeeping applied to the final state
    assert result.final_state.constitution_version == "v10"


# --------------------------------------------------------------------------- #
# B5: manifest helper carries branch identity, input untouched
# --------------------------------------------------------------------------- #
def test_b5_manifest_for_branch(prefix):
    state, log = prefix
    base = {"experiment_id": "exp-p2-1", "branch_origin": None, "treatment_seed": 0}
    out = manifest_for_branch(_spec(state, log), base)
    assert out["branch_origin"] == "counterfactual-v2"
    assert out["treatment_seed"] == 7
    assert base["branch_origin"] is None and base["treatment_seed"] == 0
    assert out["experiment_id"] == "exp-p2-1"


# --------------------------------------------------------------------------- #
# two-branch divergence: same origin, different tapes -> different hashes
# --------------------------------------------------------------------------- #
def test_two_branches_diverge(prefix):
    state, log = prefix
    r_a = branch_from_snapshot(state, log, _action_tape_a(), _spec(state, log, "branch-a"))
    r_b = branch_from_snapshot(state, log, _action_tape_b(), _spec(state, log, "branch-b"))
    r_a.log.verify()
    r_b.log.verify()
    assert r_a.branch_state_hash != r_b.branch_state_hash
    assert r_a.log.to_jsonl_bytes() != r_b.log.to_jsonl_bytes()
    # both share the identical prefix chain
    assert r_a.log.events[: len(log)] == r_b.log.events[: len(log)]


# --------------------------------------------------------------------------- #
# integration: branch from pilot event 28 (first governance event)
# reproduces the pilot final state hash
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def pilot():
    from verdi.pilot.driver import PilotDriver

    return PilotDriver(REPO_ROOT).run()


def test_branch_replay_matches_live_ledger(pilot):
    """This is a REPLAY/hash-chain equivalence invariant (same recorded
    tape folded from an earlier point), NOT counterfactual or treatment
    evidence.
    """
    log = pilot.log
    n = 28
    first_gov = log.events[n]
    # event 28 is the first governance event in the pilot ledger
    assert first_gov.kind == "proposal_submitted"
    remaining = pilot.full_tape_steps[n:]

    # Reconstruct the sealed prefix (events 0..27 are all plain actions, so
    # a plain replay of the tape prefix is byte-identical to the live log).
    prefix_log = EventLog()
    prefix_state, _ = replay(prefix_log, pilot.loaded.t0_state, pilot.full_tape_steps[:n])
    # events 0..27 are plain action events: the replayed prefix must be
    # byte-identical to the live log's first n lines
    live_lines = pilot.log.to_jsonl_bytes().splitlines()
    prefix_lines = prefix_log.to_jsonl_bytes().splitlines()
    assert prefix_lines == live_lines[:n]
    prefix_log.verify()

    spec = BranchSpec(
        origin_state_hash=WorldReducer.canonical_state_hash(prefix_state),
        origin_event_count=n,
        origin_tick=first_gov.tick,
        branch_id="counterfactual-v2",
    )
    result = branch_from_snapshot(prefix_state, prefix_log, remaining, spec)

    result.log.verify()
    assert result.log.events[n].prev_hash == prefix_log.last_hash()
    # The counterfactual branch reproduces the pilot final state exactly.
    assert result.branch_state_hash == WorldReducer.canonical_state_hash(
        pilot.final_state
    )
    assert result.branch_state_hash == pilot.final_state.state_hash
    # and the full branch ledger is byte-identical to the pilot ledger:
    assert result.log.to_jsonl_bytes() == pilot.log.to_jsonl_bytes()
