"""ADR-013 G5 live paired arms: matched treatment/placebo counterfactuals.

FROZEN design — ``docs/ADR-013-G5-PAIRED-ARMS-PROPOSAL.md`` (this file implements
that contract; ADR-013 is authoritative over any paraphrase):

* ONE L1 live run is the sealed origin. Its ledger prefix (events 0..49,
  through ``appeal_filed``) and its captured live tape (81 verbatim model
  calls) are byte-sealed. Branch point: AFTER sealed event 49.
* Per pair ``p`` in 0..9, ONE shared suffix tape ``R_p`` — the post-branch
  pilot segment (lifecycle ticks 13..29: routine dispatches + tick-17
  assembly + post-tick probes) — is generated LIVE with fresh Qwen calls
  (concurrency 1, hard cap 700 calls) by driving the pilot once with the
  sealed prefix REPLAYED from the L1 tape and the remaining segment answered
  by the live endpoint. R_p is captured verbatim in the ``live_tape.jsonl``
  format (the pair tape = 34 replayed prefix rows + 47 live suffix rows).
* R_p is REPLAYED BYTE-IDENTICAL for both arms of pair ``p`` (TapeReplayCaller):
  * treatment arm T_p: prefix + ``appeal_upheld`` + ``rights_restored`` steps
    (deterministic scripted governance, ``adjudicate_appeal(True, ...)``);
  * placebo arm P_p: prefix + the VERBATIM L1 ``appeal_dismissed`` event 50
    (the driver's default scripted adjudication) + R_p.
  Only the adjudication governance event(s) at the branch position differ.
* Matching (experiments/pairs.py): both arms share the sealed origin snapshot
  hash, the sealed prefix tape hash (same hash chain), and the identical R_p.
  Matched counterfactual pairs — NOT randomized. No causal wording.
* Scaffold invariance: pilot_v1 action kinds are non-mutating, so both arms
  converge to an IDENTICAL final world-state hash; model variance lives in the
  ledger chain (event hashes), not in state.

MANDATORY null control (offline, BEFORE any live call): replaying the FULL
81-row L1 tape through the pilot must reproduce L1 ledger events 50..125
field-for-field (event_hash + payload; event_id offset aside). Divergence is
a pipeline bug and BLOCKS the live run.

Replay provenance (L1, READ ONLY): ``/Users/sushantgautam/Documents/verdi/runs/live/20261010T071750Z/``.
Credentials come exclusively from environment variables; no key ever appears
in any artifact (audited by :func:`audit_artifacts`).
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from verdi.agents.client import ModelClient, ModelError, ModelResult
from verdi.analysis.estimates import (
    ESTIMANDS,
    paired_estimates,
    run_estimand_values,
)
from verdi.analysis.report import (
    _validate_arm_run_dict,
    build_g5_report,
    write_g5_report,
)
from verdi.config import ModelEndpointConfig, model_endpoint_from_env
from verdi.pilot.driver import PilotDriver, PilotRun
from verdi.pilot.live import LiveModelAdapter, TapeReplayCaller
from verdi.schemas.actions import ProposedAction, ValidationResult
from verdi.schemas.events import WorldEvent
from verdi.world.log import EventLog
from verdi.world.reducer import WorldReducer


class G5PipelineError(RuntimeError):
    """An ADR-013 §5 STOP condition; the live run must halt with evidence."""


# --------------------------------------------------------------------------- #
# Frozen constants (ADR-013)
# --------------------------------------------------------------------------- #

L1_RUN_DIR = Path("/Users/sushantgautam/Documents/verdi/runs/live/20261010T071750Z")
L1_TAPE_PATH = L1_RUN_DIR / "live_tape.jsonl"
L1_EVENT_LOG_PATH = L1_RUN_DIR / "event_log.jsonl"

#: Branch point: AFTER sealed event 49 (appeal_filed). Prefix = 0..49.
BRANCH_EVENT_ID = 49
#: L1 event 50 = verbatim appeal_dismissed = the placebo step.
PLACEBO_EVENT_ID = 50
#: R_p spans lifecycle ticks 13..29.
SUFFIX_TICK_MIN = 13
SUFFIX_TICK_MAX = 29
#: Post-branch lifecycle ticks snapshotted for estimands (subset of the
#: driver's SNAPSHOT_TICKS (5,10,17,24,29)); the terminal snapshot (29) feeds
#: welfare/gini. Documented in each arm run dict.
POST_BRANCH_SNAPSHOT_TICKS: tuple[int, ...] = (17, 24, 29)

N_PAIRS = 10
HARD_CALL_CAP = 700
EXPECTED_SUFFIX_CALLS = 47
#: ADR-013 §5: bounded cross-pair retries on transient endpoint errors
#: (on top of ModelClient's own per-request retry budget).
_TRANSIENT_RETRY_LIMIT = 3
#: Qwen3.8-27B is a reasoning model; propose() passes max_tokens=None and the
#: completion can come back empty below ~1024 tokens. Force a floor on LIVE
#: calls only (never replay), so taped rows stay byte-identical to L1 format.
LIVE_MAX_TOKENS = 2048

TREATMENT_GROUNDS = "upheld-appeal treatment arm"
TREATMENT_ARM = "treatment"
PLACEBO_ARM = "placebo"

#: Adjudication kinds that must not appear in the OTHER arm's ledger.
_TREATMENT_ONLY_KINDS = frozenset({"appeal_upheld", "rights_restored"})
_PLACEBO_ONLY_KINDS = frozenset({"appeal_dismissed"})

# The frozen report wording contains exactly these three NEGATED "causal"
# occurrences (WS-4 mandated framing, analysis/report.py). No NEW occurrences
# may be introduced by the paired extensions below.
_FROZEN_CAUSAL_OCCURRENCES = 3


# --------------------------------------------------------------------------- #
# L1 provenance loading (read-only)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class L1Provenance:
    """Sealed L1 origin: verified ledger + verbatim tape rows."""

    event_log: EventLog
    tape_rows: list[dict[str, Any]] = field(compare=False)

    @property
    def prefix_events(self) -> tuple[WorldEvent, ...]:
        """Sealed prefix: L1 events 0..BRANCH_EVENT_ID inclusive (50 events)."""
        return self.event_log.events[: BRANCH_EVENT_ID + 1]

    @property
    def prefix_tape_rows(self) -> list[dict[str, Any]]:
        """L1 tape rows for lifecycle tick <= 12 (replayed, never live)."""
        return [r for r in self.tape_rows if int(r["lifecycle_tick"]) <= 12]

    @property
    def suffix_tape_rows(self) -> list[dict[str, Any]]:
        """L1's own R (lifecycle ticks 13..29) — used by the null control."""
        return [
            r
            for r in self.tape_rows
            if SUFFIX_TICK_MIN <= int(r["lifecycle_tick"]) <= SUFFIX_TICK_MAX
        ]

    @property
    def placebo_event(self) -> WorldEvent:
        """Verbatim L1 ``appeal_dismissed`` (event 50)."""
        ev = self.event_log.events[PLACEBO_EVENT_ID]
        if ev.kind != "appeal_dismissed":
            raise G5PipelineError(
                f"L1 event {PLACEBO_EVENT_ID} is {ev.kind!r}, expected "
                "appeal_dismissed — L1 provenance moved"
            )
        return ev

    @property
    def origin_state_hash(self) -> str:
        """Terminal after_hash of the sealed prefix (event 49)."""
        after = self.prefix_events[-1].after_hash
        if not after:
            raise G5PipelineError("L1 branch event 49 has no after_hash")
        return after


def load_l1(run_dir: str | Path = L1_RUN_DIR) -> L1Provenance:
    """Load and sanity-check the sealed L1 ledger + tape (read-only)."""
    run_dir = Path(run_dir)
    if not (run_dir / "live_tape.jsonl").is_file():
        raise G5PipelineError(f"L1 tape missing: {run_dir}")
    tape_rows = [
        json.loads(line)
        for line in (run_dir / "live_tape.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    log = EventLog.load_jsonl(run_dir / "event_log.jsonl")
    if len(log.events) != 126:
        raise G5PipelineError(f"L1 ledger has {len(log.events)} events, expected 126")
    if log.events[BRANCH_EVENT_ID].kind != "appeal_filed":
        raise G5PipelineError("L1 event 49 is not appeal_filed — provenance moved")
    if l1_prefix_count(tape_rows) != 34:
        raise G5PipelineError("L1 tape prefix row count is not 34 — provenance moved")
    return L1Provenance(event_log=log, tape_rows=tape_rows)


def l1_prefix_count(tape_rows: list[dict[str, Any]]) -> int:
    return sum(1 for r in tape_rows if int(r["lifecycle_tick"]) <= 12)


def prefix_event_log(l1: L1Provenance) -> EventLog:
    """Sealed prefix as a standalone, chain-verified EventLog (0..49)."""
    prefix = EventLog()
    for ev in l1.prefix_events:
        # Sealed events re-seal to the same hash on append (idempotent),
        # so the chain verifies end-to-end.
        prefix.append(ev.model_copy(deep=True))
    prefix.verify()
    return prefix


# --------------------------------------------------------------------------- #
# R_p generation: prefix replay, then live endpoint (one live call per pair
# segment dispatch, concurrency 1)
# --------------------------------------------------------------------------- #


class _ClientFactory:
    """Zero-arg ModelClient factory: one client per dispatch event loop."""

    def __init__(self, config: ModelEndpointConfig) -> None:
        self._config = config

    def __call__(self) -> ModelClient:
        return ModelClient(self._config)


class _MaxTokensGuard:
    """Force a max_tokens floor on ONE downstream caller (reasoning model).

    Applied ONLY to the live side; replayed rows never pass through it, so
    captured prefix rows stay byte-identical to the L1 tape.
    """

    def __init__(self, inner: LiveModelAdapter, floor: int) -> None:
        self._inner = inner
        self._floor = floor
        self.last_result: ModelResult | None = None
        self.last_prompt: str | None = None

    async def generate_structured(
        self,
        messages: list[dict[str, str]],
        *,
        response_format_json: bool = False,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ModelResult:
        if max_tokens is None:
            max_tokens = self._floor
        result = await self._inner.generate_structured(
            messages,
            response_format_json=response_format_json,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self.last_result = self._inner.last_result
        self.last_prompt = self._inner.last_prompt
        return result


class PrefixReplayLiveCaller:
    """ModelCaller for ONE R_p generation (live, per pair).

    Serves the sealed prefix from the L1 tape (ticks <= 12, seq order), then
    answers the remaining segment with the LIVE endpoint (one ModelClient per
    dispatch). The pilot's live-capture path records every call — including
    the replayed prefix rows — into the pair's ``live_tape.jsonl``; R_p is the
    tick >= 13 rows of that file. The run is fully deterministic in its
    scaffolding; only the suffix responses are fresh model outputs.
    """

    def __init__(self, l1: L1Provenance, config: ModelEndpointConfig) -> None:
        self._prefix = TapeReplayCaller(l1.prefix_tape_rows)
        self._live = LiveModelAdapter(_ClientFactory(config), model=config.model)
        self._guarded = _MaxTokensGuard(self._live, LIVE_MAX_TOKENS)
        self.model = str(config.model)
        self.call_count = 0
        self.live_call_count = 0
        self.last_result: ModelResult | None = None
        self.last_prompt: str | None = None

    def __len__(self) -> int:
        return len(self._prefix) + max(0, EXPECTED_SUFFIX_CALLS - self.live_call_count)

    async def generate_structured(
        self,
        messages: list[dict[str, str]],
        *,
        response_format_json: bool = False,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ModelResult:
        self.call_count += 1
        if self.call_count <= len(self._prefix):
            result = await self._prefix.generate_structured(
                messages,
                response_format_json=response_format_json,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            self.last_result = self._prefix.last_result
            self.last_prompt = self._prefix.last_prompt
            return self.last_result
        self.live_call_count += 1
        result = await self._guarded.generate_structured(
            messages,
            response_format_json=response_format_json,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        self.last_result = self._guarded.last_result
        self.last_prompt = self._guarded.last_prompt
        return result


def generate_pair_tape(
    l1: L1Provenance,
    repo_root: str | Path,
    pair_dir: str | Path,
    config: ModelEndpointConfig,
    used_calls: int,
) -> tuple[list[dict[str, Any]], int]:
    """Generate ONE shared R_p live (pair tape = 34 replayed + 47 live rows).

    Returns ``(pair_tape_rows, used_calls_after)``. Cost: exactly
    ``EXPECTED_SUFFIX_CALLS`` live endpoint calls; bounded retries on
    429/5xx are inside ModelClient. Budget is enforced by the caller via
    ``used_calls`` and the ``HARD_CALL_CAP``. Persistent endpoint failure
    propagates and must be handled per ADR-013 §5 (STOP + evidence).
    """
    if used_calls + EXPECTED_SUFFIX_CALLS > HARD_CALL_CAP:
        raise G5PipelineError(
            f"budget: {used_calls} used + {EXPECTED_SUFFIX_CALLS} needed "
            f"exceeds hard cap {HARD_CALL_CAP}"
        )
    pair_dir = Path(pair_dir)
    pair_dir.mkdir(parents=True, exist_ok=True)
    caller = PrefixReplayLiveCaller(l1, config)
    driver = PilotDriver(
        repo_root,
        model=caller,
        model_id=config.model,
        base_url=config.base_url,
        live_capture_dir=pair_dir,
    )
    run = driver.run()
    if run.quarantined:
        raise G5PipelineError(f"R_p generation produced quarantined proposals: {run.quarantined}")
    pair_tape = [
        json.loads(line)
        for line in (pair_dir / "live_tape.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    live_rows = [r for r in pair_tape if int(r["lifecycle_tick"]) >= SUFFIX_TICK_MIN]
    if len(live_rows) != EXPECTED_SUFFIX_CALLS:
        raise G5PipelineError(
            f"R_p has {len(live_rows)} suffix rows, expected {EXPECTED_SUFFIX_CALLS}"
        )
    if caller.live_call_count != EXPECTED_SUFFIX_CALLS:
        raise G5PipelineError(
            f"live caller made {caller.live_call_count} calls, expected {EXPECTED_SUFFIX_CALLS}"
        )
    return pair_tape, used_calls + caller.live_call_count


# --------------------------------------------------------------------------- #
# Mandatory placebo-inert null control (offline; BEFORE any live call)
# --------------------------------------------------------------------------- #


def null_control(l1: L1Provenance, repo_root: str | Path) -> dict[str, Any]:
    """Replay the FULL 81-row L1 tape; folded suffix must match L1 verbatim.

    Compares folded events 50..125 against L1 events 50..125 field-for-field
    on ``event_hash`` + ``payload`` (event_id offset aside — ids are equal
    here). Any divergence raises :class:`G5PipelineError`: the live run is
    BLOCKED until the pipeline bug is fixed.
    """
    driver = PilotDriver(
        repo_root,
        model=TapeReplayCaller(l1.tape_rows),
        model_id="live-tape-replay-null-control",
        live_capture_dir="",
    )
    run = driver.run()
    if len(run.log.events) != len(l1.event_log.events):
        raise G5PipelineError(
            f"null control: folded ledger has {len(run.log.events)} events, "
            f"L1 has {len(l1.event_log.events)} — BLOCK"
        )
    divergences: list[dict[str, Any]] = []
    for i in range(PLACEBO_EVENT_ID, len(l1.event_log.events)):
        folded, l1_ev = run.log.events[i], l1.event_log.events[i]
        if folded.event_hash != l1_ev.event_hash or folded.payload != l1_ev.payload:
            divergences.append(
                {
                    "event_id": i,
                    "kind": l1_ev.kind,
                    "l1_event_hash": l1_ev.event_hash,
                    "folded_event_hash": folded.event_hash,
                }
            )
    if divergences:
        raise G5PipelineError(
            "null control DIVERGENCE — pipeline bug, BLOCK the live run: "
            f"{divergences[:3]} (+{max(0, len(divergences) - 3)} more)"
        )
    return {
        "method": (
            "full 81-row L1 tape replayed through PilotDriver (TapeReplayCaller, "
            "live capture off, default scripted dismissal); folded ledger events "
            "50..125 compared against L1 event_log.jsonl field-for-field on "
            "event_hash + payload"
        ),
        "events_compared": len(l1.event_log.events) - PLACEBO_EVENT_ID,
        "byte_identical": True,
        "folded_final_state_hash": run.final_state.state_hash,
        "l1_final_event_after_hash": (run.log.events[-1].after_hash or ""),
    }


# --------------------------------------------------------------------------- #
# Matching context + arm manifests + matched pair (experiments/pairs.py)
# --------------------------------------------------------------------------- #


def matching_context(l1: L1Provenance, repo_root: str | Path | None = None) -> dict[str, Any]:
    """Sealed origin snapshot hash + sealed prefix tape hash (all arms/pairs).

    B3(a)(b)(c) satisfied structurally: origin hash == L1 event 49
    ``after_hash`` == canonical_state_hash of the folded prefix terminal
    state; prefix count == 50 == len(prefix ledger).
    """
    from verdi.experiments.pairs import tape_hash

    origin = l1.origin_state_hash
    prefix = prefix_event_log(l1)
    # B3 cross-check: fold the prefix through a fresh reducer to confirm the
    # terminal canonical state hash equals the sealed after_hash.
    state = _fold_prefix_state(l1, repo_root)
    folded_hash = WorldReducer.canonical_state_hash(state)
    if folded_hash != origin:
        raise G5PipelineError(
            f"B3 violation: folded prefix terminal hash {folded_hash} != sealed after_hash {origin}"
        )
    return {
        "branch_event_id": BRANCH_EVENT_ID,
        "origin_state_hash": origin,
        "origin_event_count": len(prefix.events),
        "origin_tick": int(l1.prefix_events[-1].tick),
        "prefix_tape_hash": tape_hash(prefix),
        "folded_prefix_terminal_hash": folded_hash,
    }


def _fold_prefix_state(l1: L1Provenance, repo_root: str | Path | None = None):
    """Fold L1 events 0..49 through a fresh WorldReducer (no side effects).

    Rebuilds the exact reducer transitions the driver performs: action
    events carry their recorded arguments; governance events use
    ``event_payload_action`` (constitution-version bookkeeping only).
    """
    reducer = WorldReducer()
    state = None
    for ev in l1.prefix_events:
        payload = ev.payload
        if _is_governance_kind(ev.kind):
            action = _governance_action(payload, ev)
        else:
            action = ProposedAction(
                actor_id=str(ev.actor_id),
                task_id=str(payload.get("task_id") or f"task-{ev.event_id}"),
                action_kind=str(payload.get("action_kind")),
                arguments=dict(payload.get("arguments") or {}),
            )
        accepted = ev.decision == "accepted"
        validation = ValidationResult(
            accepted=accepted,
            blocked_by=(None if accepted else str(payload.get("blocked_by") or "law")),
            attempted_violation_codes=list(payload.get("attempted_violation_codes") or []),
            details=str(payload.get("details") or ""),
        )
        if state is None:
            state = reducer.apply(_t0_state(repo_root), action, validation)
        else:
            state = reducer.apply(state, action, validation)
    assert state is not None
    return state


_GOVERNANCE_KINDS = frozenset(
    {
        "proposal_submitted",
        "vote_cast",
        "amendment_enacted",
        "sanction_imposed",
        "appeal_filed",
        "appeal_dismissed",
        "appeal_upheld",
        "rights_restored",
    }
)


def _is_governance_kind(kind: str) -> bool:
    return kind in _GOVERNANCE_KINDS


def _t0_state(repo_root: str | Path | None = None):
    """The pilot_v1 t0 WorldState from the scenario loader (pure read).

    Resolves the repo root exactly as :class:`PilotDriver` does (explicit
    arg, else package-relative parents[3]).
    """
    from verdi.analysis.scenario import ScenarioLoader

    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[3]
    loader = ScenarioLoader(
        root / "experiments" / "scenarios" / "pilot_v1.json",
        root / "experiments" / "task_tape" / "pilot_v1.json",
    )
    return loader.load().t0_state


def arm_manifest(
    l1: L1Provenance,
    pair: int,
    arm: str,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """Per-arm plain-dict manifest satisfying ``make_matched_pair``.

    Both arms of a pair carry the SAME ``origin_state_hash`` and the SAME
    ``tape_hash`` (the sealed prefix tape hash), so the identity check in
    ``experiments.pairs.make_matched_pair`` holds by construction.
    """
    if arm not in (TREATMENT_ARM, PLACEBO_ARM):
        raise ValueError(f"unknown arm {arm!r}")
    return {
        "arm": arm,
        "pair": int(pair),
        "experiment_id": "g5-paired-arms-pilot-v1",
        "branch_origin": f"g5-pair-{pair}",
        "treatment_seed": int(pair),
        "origin_state_hash": str(context["origin_state_hash"]),
        "tape_hash": str(context["prefix_tape_hash"]),
        "adjudication": (
            "appeal_upheld + rights_restored (scripted, applied=True)"
            if arm == TREATMENT_ARM
            else "appeal_dismissed verbatim L1 event 50 (applied=False)"
        ),
        "r_tape": f"R_{pair}",
    }


def matched_pair(
    l1: L1Provenance,
    pair: int,
    context: Mapping[str, Any],
):
    """Validate the pair identity via experiments/pairs.make_matched_pair."""
    from verdi.experiments.pairs import make_matched_pair

    return make_matched_pair(
        arm_manifest(l1, pair, TREATMENT_ARM, context),
        arm_manifest(l1, pair, PLACEBO_ARM, context),
    )


# --------------------------------------------------------------------------- #
# Arm fold (offline) + arm run dict (analysis/report.py compatible)
# --------------------------------------------------------------------------- #


def run_arm(
    repo_root: str | Path,
    pair_tape: list[dict[str, Any]],
    arm: str,
    out_dir: str | Path | None = None,
) -> PilotRun:
    """Offline fold of ONE arm: replay the shared 81-row pair tape.

    Treatment arm: ``appeal_adjudication=(True, TREATMENT_GROUNDS)`` →
    ``appeal_upheld`` + ``rights_restored`` at tick 12. Placebo arm:
    ``appeal_adjudication=None`` → the driver's default scripted dismissal,
    byte-identical to L1 event 50. Live capture OFF (replay only).
    """
    if arm == TREATMENT_ARM:
        adjudication: tuple[bool, str] | None = (True, TREATMENT_GROUNDS)
    elif arm == PLACEBO_ARM:
        adjudication = None
    else:
        raise ValueError(f"unknown arm {arm!r}")
    caller = TapeReplayCaller(pair_tape)
    driver = PilotDriver(
        repo_root,
        model=caller,
        model_id=str(caller._model),
        live_capture_dir="",
        appeal_adjudication=adjudication,
    )
    return driver.run(out_dir)


def arm_run_dict(run: PilotRun, arm: str) -> dict[str, Any]:
    """The ``_REQUIRED_ARM_KEYS``-satisfying run dict for one folded arm.

    ``state_snapshots`` keeps real :class:`WorldState` objects (in memory) so
    ``build_g5_report``'s trajectory hashing works; ``estimates`` carries the
    six PREREG-registered estimand values computed over the post-branch
    snapshots (ticks 17/24/29) plus the full ledger.
    """
    applied = arm == TREATMENT_ARM
    snapshots: list[tuple[int, Any]] = [
        (tick, st) for tick, st in run.state_snapshots if tick in POST_BRANCH_SNAPSHOT_TICKS
    ]
    if not snapshots:
        raise G5PipelineError(f"arm {arm!r} has no post-branch snapshots")
    states_by_tick = {int(tick): st for tick, st in run.state_snapshots}
    estimates = run_estimand_values(states_by_tick=states_by_tick, log=run.log)
    for key in ESTIMANDS:
        if key not in estimates:
            raise G5PipelineError(f"estimand {key!r} missing from run_estimand_values")
    return {
        "arm": arm,
        "applied": applied,
        "reason": (
            "matched counterfactual treatment arm: appeal upheld + rights "
            "restored (scripted deterministic governance intervention), "
            "shared R_p replayed byte-identical"
            if applied
            else "matched counterfactual placebo arm: verbatim L1 appeal "
            "dismissal (event 50), shared R_p replayed byte-identical"
        ),
        "final_state_hash": run.final_state.state_hash,
        "state_snapshots": snapshots,
        "snapshot_ticks": list(POST_BRANCH_SNAPSHOT_TICKS),
        "estimates": estimates,
        "terminal_state_hash": run.final_state.state_hash,
    }


# --------------------------------------------------------------------------- #
# Audits (mandatory, tested)
# --------------------------------------------------------------------------- #


def _api_key_value() -> str | None:
    import os

    return os.environ.get("VERDI_VLLM_API_KEY") or None


def audit_artifacts(run_root: str | Path) -> dict[str, Any]:
    """Mandatory audits over a g5_pairs run directory (ADR-013 §2).

    (a) no API-key value in ANY artifact text; (b) no ``api_key`` field in
    any meta; (c) R_p arm-blindness: R_p (tick >= 13) prompts/tape contain no
    treatment-outcome strings; (d) cross-arm leak: each arm ledger contains
    only its own adjudication kind(s). Raises :class:`G5PipelineError` on
    any finding.
    """
    run_root = Path(run_root)
    key = _api_key_value()
    findings: list[str] = []
    key_hits = 0
    if key:
        for path in sorted(run_root.rglob("*")):
            if not path.is_file():
                continue
            try:
                text = path.read_bytes()
            except OSError:
                continue
            if key.encode() in text:
                findings.append(f"api key value found in {path}")
                key_hits += 1
    for meta in sorted(run_root.rglob("*meta*.json")):
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        blob = json.dumps(data)
        if '"api_key"' in blob or "'api_key'" in blob:
            findings.append(f"api_key field present in {meta}")

    r_p_leak_terms = ("appeal_upheld", "rights_restored")
    for tape in sorted(run_root.glob("pair_*/live_tape.jsonl")):
        for line in tape.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if int(row.get("lifecycle_tick", 0)) >= SUFFIX_TICK_MIN:
                prompt = str(row.get("prompt") or "")
                for term in r_p_leak_terms:
                    if term in prompt:
                        findings.append(f"R_p arm-blind leak {term!r} in {tape}")

    for pair_dir in sorted(run_root.glob("pair_*")):
        for arm, banned in (
            (TREATMENT_ARM, _PLACEBO_ONLY_KINDS),
            (PLACEBO_ARM, _TREATMENT_ONLY_KINDS),
        ):
            log_path = pair_dir / arm / "event_log.jsonl"
            if not log_path.is_file():
                continue
            for line in log_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                kind = json.loads(line).get("kind")
                if kind in banned:
                    findings.append(f"cross-arm leak: {kind!r} present in {arm} ledger {log_path}")
    if findings:
        raise G5PipelineError("G5 artifact audit FAILURES: " + "; ".join(findings[:5]))
    return {
        "scanned_root": str(run_root),
        "api_key_value_scanned": bool(key),
        "api_key_value_hits": key_hits,
        "artifacts": len(list(run_root.rglob("*"))),
        "clean": True,
    }


# --------------------------------------------------------------------------- #
# Deterministic G5 paired report
# --------------------------------------------------------------------------- #


def build_g5_paired_report(
    l1: L1Provenance,
    treatment_run: dict[str, Any],
    placebo_run: dict[str, Any],
    pairs_results: dict[str, Any],
    null_control_evidence: dict[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """Byte-stable G5 report over all 10 pairs (pure over the inputs).

    Extends the FROZEN ``build_g5_report`` (pair-0 arms as the trajectory
    exemplar) with the ADR-013 §1 null-control evidence, the sealed-origin
    matching context, and the cross-pair replication spread. NO new
    "causal" wording is introduced beyond the frozen framing (the frozen
    output contains exactly two negated "causal" occurrences).
    """
    _validate_arm_run_dict("treatment", treatment_run)
    _validate_arm_run_dict("placebo", placebo_run)
    report = build_g5_report(
        _ReportPilotProxy(treatment_run, context),
        treatment_run,
        placebo_run,
        pairs_results,
    )
    report["null_control_placebo_inert"] = dict(null_control_evidence)
    report["matching"] = {
        "origin_state_hash": str(context["origin_state_hash"]),
        "origin_event_count": int(context["origin_event_count"]),
        "origin_tick": int(context["origin_tick"]),
        "prefix_tape_hash": str(context["prefix_tape_hash"]),
        "branch_event_id": BRANCH_EVENT_ID,
        "r_tape": "one shared R_p per pair, replayed byte-identical for both arms",
        "invariants": (
            "equal sealed origin snapshot hash; equal sealed prefix tape hash; "
            "identical R_p; only the adjudication governance events differ",
        ),
    }
    report["replication"] = {
        "n_pairs": int(context.get("n_pairs", N_PAIRS)),
        "note": (
            "between-pair spread is REPLICATION spread, not sampling noise; "
            "bootstrap CI over paired differences at n_pairs=10"
        ),
    }
    # Guard: only the two frozen negated "causal" occurrences may remain.
    blob = json.dumps(report)
    n_causal = len(re.findall(r"causal", blob, flags=re.IGNORECASE))
    if n_causal > _FROZEN_CAUSAL_OCCURRENCES:
        raise G5PipelineError(
            f"report contains {n_causal} 'causal' occurrences, expected at most "
            f"{_FROZEN_CAUSAL_OCCURRENCES} (frozen framing only)"
        )
    return report


class _ReportPilotProxy:
    """Minimal ``pilot`` object for build_g5_report: manifest + final state
    + state_snapshots from the exemplar arm's serialized trajectory."""

    def __init__(self, treatment_run: dict[str, Any], context: Mapping[str, Any]) -> None:
        self._treatment = treatment_run

        class _Final:
            state_hash = treatment_run.get("final_state_hash")

        self.final_state = _Final()
        self.state_snapshots = list(treatment_run.get("state_snapshots") or [])
        self.manifest = {
            "experiment_id": "g5-paired-arms-pilot-v1",
            "branch_origin": "g5-paired-arms",
            "treatment_seed": int(context.get("treatment_seed", 0)),
        }


# --------------------------------------------------------------------------- #
# Live orchestration (script-style; CI never calls this)
# --------------------------------------------------------------------------- #


def write_run_meta(
    run_root: str | Path,
    *,
    pairs: list[int],
    used_calls: int,
    quarantines: list[Any],
    context: Mapping[str, Any],
    null_control_evidence: dict[str, Any],
    report_path: str | Path,
    git_sha: str | None = None,
) -> Path:
    """Write ``g5_run_meta.json`` (NO key — env var names only)."""
    run_root = Path(run_root)
    run_root.mkdir(parents=True, exist_ok=True)
    meta = {
        "model_id_env_var": "VERDI_VLLM_MODEL",
        "base_url_env_var": "VERDI_VLLM_BASE_URL",
        "endpoint_env_vars": [
            "VERDI_MAX_CONCURRENCY",
            "VERDI_MAX_RETRIES",
            "VERDI_REQUEST_TIMEOUT",
            "VERDI_VLLM_API_KEY",
            "VERDI_VLLM_BASE_URL",
            "VERDI_VLLM_MODEL",
        ],
        "n_pairs": len(pairs),
        "pairs": pairs,
        "hard_call_cap": HARD_CALL_CAP,
        "live_calls_used": int(used_calls),
        "expected_suffix_calls_per_pair": EXPECTED_SUFFIX_CALLS,
        "quarantines": list(quarantines),
        "origin_state_hash": str(context["origin_state_hash"]),
        "prefix_tape_hash": str(context["prefix_tape_hash"]),
        "null_control": null_control_evidence,
        "report_path": str(report_path),
        "git_sha": git_sha,
        "framing": (
            "matched counterfactual pairs, NOT randomized; no causal claim; "
            "identical final state hash = scaffold invariance (pilot_v1 action "
            "kinds are non-mutating); model variance lives in the ledger chain"
        ),
    }
    meta_path = run_root / "g5_run_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return meta_path


def run_g5_pairs(
    repo_root: str | Path,
    run_root: str | Path,
    *,
    report_path: str | Path,
    config: ModelEndpointConfig | None = None,
) -> dict[str, Any]:
    """Full ADR-013 live loop: null control → 10 pairs → estimands → report.

    Concurrency 1, hard cap 700, Qwen3.8-27B only. Script-style: not a test,
    no network in CI. Persistent endpoint failure → G5PipelineError with
    evidence; the caller must STOP and report BLOCKED per ADR-013 §5.
    """
    import subprocess

    config = config or model_endpoint_from_env()
    if config is None:
        raise G5PipelineError("VERDI_VLLM_* endpoint not configured — source the env first")
    run_root = Path(run_root)
    run_root.mkdir(parents=True, exist_ok=True)
    l1 = load_l1()
    context = matching_context(l1, repo_root)

    # GATE 1: mandatory placebo-inert null control, offline, before any live.
    null_evidence = null_control(l1, repo_root)

    quarantines: list[Any] = []
    pairs_results_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    exemplar_t: dict[str, Any] | None = None
    exemplar_p: dict[str, Any] = None
    used_calls = 0

    def _generate_with_transient_retry(pair_dir: Path) -> list[Any]:
        """ADR-013 §5: on transient endpoint errors (429/5xx), back off and
        retry within a bounded budget; persistent failure raises
        G5PipelineError (caller must STOP and report BLOCKED).

        ModelClient already exhausts its own per-request retry budget
        (config.max_retries) on 429/502/503 + transport errors before
        raising ModelError; this layer adds the cross-pair backoff on top.
        A partial (incomplete) pair dir from a failed attempt is removed
        so a retry re-runs the pair cleanly (used_calls already reflects
        calls spent in the failed attempt — conservative budget accounting).
        """
        nonlocal used_calls
        import time

        last: Exception | None = None
        for attempt in range(1, _TRANSIENT_RETRY_LIMIT + 1):
            try:
                tape, used_calls = generate_pair_tape(l1, repo_root, pair_dir, config, used_calls)
                return tape
            except G5PipelineError:
                raise
            except ModelError as exc:
                transient = exc.status_code in (429, 500, 502, 503, 504) or exc.status_code is None
                if not transient:
                    raise G5PipelineError(
                        f"non-transient endpoint failure (HTTP "
                        f"{exc.status_code}): {exc!r} — STOP per ADR-013 §5"
                    ) from exc
                last = exc
                if attempt >= _TRANSIENT_RETRY_LIMIT:
                    break
                delay = min(120.0, 15.0 * (2 ** (attempt - 1)))
                print(
                    f"[g5-pairs] pair {pair} transient endpoint error "
                    f"(status={exc.status_code}); backoff {delay:.0f}s "
                    f"(attempt {attempt}/{_TRANSIENT_RETRY_LIMIT})",
                    flush=True,
                )
                time.sleep(delay)
            except (ConnectionError, TimeoutError, OSError) as exc:
                last = exc
                if attempt >= _TRANSIENT_RETRY_LIMIT:
                    break
                delay = min(120.0, 15.0 * (2 ** (attempt - 1)))
                print(
                    f"[g5-pairs] pair {pair} transport error "
                    f"({type(exc).__name__}); backoff {delay:.0f}s "
                    f"(attempt {attempt}/{_TRANSIENT_RETRY_LIMIT})",
                    flush=True,
                )
                time.sleep(delay)
        # Drop the partial pair dir so resume logic stays marker-based.
        shutil.rmtree(pair_dir, ignore_errors=True)
        raise G5PipelineError(
            f"persistent endpoint failure on pair {pair} after "
            f"{_TRANSIENT_RETRY_LIMIT} attempts: {last!r} — STOP per "
            f"ADR-013 §5, report BLOCKED with this evidence"
        )

    for pair in range(N_PAIRS):
        pair_dir = run_root / f"pair_{pair}"
        pair_dir.mkdir(parents=True, exist_ok=True)
        # Resume: a pair with both completion markers reuses its saved tape
        # (byte-identical) instead of spending live calls; the arm folds are
        # re-run offline below (cheap, deterministic) and re-written.
        if (pair_dir / f"{TREATMENT_ARM}.done").exists() and (
            pair_dir / f"{PLACEBO_ARM}.done"
        ).exists():
            tape_rows = [
                json.loads(line)
                for line in (pair_dir / "live_tape.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            live_rows = [r for r in tape_rows if int(r["lifecycle_tick"]) >= SUFFIX_TICK_MIN]
            if len(live_rows) != EXPECTED_SUFFIX_CALLS:
                raise G5PipelineError(
                    f"resume pair {pair}: saved tape has {len(live_rows)} suffix "
                    f"rows, expected {EXPECTED_SUFFIX_CALLS}"
                )
            pair_tape = tape_rows
            used_calls += len(live_rows)
            print(f"[g5-pairs] pair {pair}: resume (markers present)", flush=True)
        else:
            pair_tape = _generate_with_transient_retry(pair_dir)
            print(f"[g5-pairs] pair {pair}: live tape generated", flush=True)
        # pair identity check (matching invariants, experiments/pairs.py)
        matched_pair(l1, pair, context)
        t_run = run_arm(repo_root, pair_tape, TREATMENT_ARM, pair_dir / TREATMENT_ARM)
        p_run = run_arm(repo_root, pair_tape, PLACEBO_ARM, pair_dir / PLACEBO_ARM)
        t_dict = arm_run_dict(t_run, TREATMENT_ARM)
        p_dict = arm_run_dict(p_run, PLACEBO_ARM)
        # Scaffold invariance: both arms converge to the same final state hash.
        if t_dict["final_state_hash"] != p_dict["final_state_hash"]:
            raise G5PipelineError(
                f"pair {pair}: final state hash mismatch across arms "
                f"(scaffold invariance violated): "
                f"{t_dict['final_state_hash']} != {p_dict['final_state_hash']}"
            )
        pairs_results_pairs.append((t_dict, p_dict))
        if exemplar_t is None:
            exemplar_t, exemplar_p = t_dict, p_dict
        (pair_dir / "treatment_run_dict.json").write_text(
            json.dumps(_serializable_run_dict(t_dict), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (pair_dir / "placebo_run_dict.json").write_text(
            json.dumps(_serializable_run_dict(p_dict), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        # Persist per-arm PilotRun artifacts (pilot_report.json + event_log.jsonl)
        t_run.report.write(pair_dir / TREATMENT_ARM / "pilot_report.json")
        (pair_dir / TREATMENT_ARM / "event_log.jsonl").write_bytes(t_run.log.to_jsonl_bytes())
        p_run.report.write(pair_dir / PLACEBO_ARM / "pilot_report.json")
        (pair_dir / PLACEBO_ARM / "event_log.jsonl").write_bytes(p_run.log.to_jsonl_bytes())
        quarantines.extend(f"pair_{pair}:{t}" for t, _ in t_run.quarantined)
        quarantines.extend(f"pair_{pair}:{t}" for t, _ in p_run.quarantined)
        # Completion markers (both arms folded + persisted) → resumable.
        (pair_dir / f"{TREATMENT_ARM}.done").write_text("ok\n", encoding="utf-8")
        (pair_dir / f"{PLACEBO_ARM}.done").write_text("ok\n", encoding="utf-8")

    if exemplar_t is None or exemplar_p is None:
        raise G5PipelineError("no pairs completed")
    context["n_pairs"] = N_PAIRS
    context["treatment_seed"] = 0
    pairs_results = paired_estimates(pairs_results_pairs)
    report = build_g5_paired_report(
        l1, exemplar_t, exemplar_p, pairs_results, null_evidence, context
    )
    # Byte-stability: build twice, write, require identical bytes.
    report2 = build_g5_paired_report(
        load_l1(),
        exemplar_t,
        exemplar_p,
        paired_estimates(pairs_results_pairs),
        null_control(l1, repo_root),
        context,
    )
    if json.dumps(report, sort_keys=True) != json.dumps(report2, sort_keys=True):
        raise G5PipelineError("G5 report is not byte-stable across two builds")
    write_g5_report(report, report_path)

    git_sha = None
    try:
        git_sha = (
            subprocess.run(
                ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
            or None
        )
    except (OSError, subprocess.SubprocessError):
        git_sha = None
    write_run_meta(
        run_root,
        pairs=list(range(N_PAIRS)),
        used_calls=used_calls,
        quarantines=quarantines,
        context=context,
        null_control_evidence=null_evidence,
        report_path=report_path,
        git_sha=git_sha,
    )
    # Final audit gate over the whole run dir (incl. meta/report refs).
    audit_artifacts(run_root)
    return {
        "pairs": N_PAIRS,
        "used_calls": used_calls,
        "quarantines": quarantines,
        "report": str(report_path),
        "run_root": str(run_root),
    }


def _serializable_run_dict(run_dict: Mapping[str, Any]) -> dict[str, Any]:
    """JSON-safe copy of an arm run dict (snapshots → tick→state_hash)."""
    out = dict(run_dict)
    out["state_hashes_by_lifecycle_tick"] = {
        str(tick): _state_hash(st) for tick, st in (run_dict.get("state_snapshots") or [])
    }
    out.pop("state_snapshots", None)
    return out


def _state_hash(state: Any) -> str:
    return WorldReducer.canonical_state_hash(state)


# --------------------------------------------------------------------------- #
# Internal action builders for prefix state folding
# --------------------------------------------------------------------------- #


def _governance_action(payload: dict[str, Any], ev: WorldEvent) -> ProposedAction:
    """Canonical governance action for one ledger event (mirror of driver).

    Only ``amendment_enacted`` carries a constitution-version argument (the
    payload's ``new_version``); the ledger payload of a governance event does
    NOT include ``action_kind``.
    """
    arguments: dict[str, Any] = {}
    if ev.kind == "amendment_enacted":
        new_version = payload.get("new_version") or payload.get("constitution_version")
        if new_version:
            arguments["constitution_version"] = str(new_version)
    return ProposedAction(
        actor_id=str(payload.get("actor") or ev.actor_id or "governance"),
        task_id=str(payload.get("task_id") or f"governance-{ev.kind}-{ev.event_id}"),
        action_kind="governance",
        arguments=arguments,
    )


__all__ = [
    "G5PipelineError",
    "L1Provenance",
    "load_l1",
    "prefix_event_log",
    "PrefixReplayLiveCaller",
    "generate_pair_tape",
    "null_control",
    "matching_context",
    "arm_manifest",
    "matched_pair",
    "run_arm",
    "arm_run_dict",
    "audit_artifacts",
    "build_g5_paired_report",
    "write_run_meta",
    "run_g5_pairs",
    "LIVE_MAX_TOKENS",
    "HARD_CALL_CAP",
    "N_PAIRS",
]
