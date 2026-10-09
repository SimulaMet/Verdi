"""Cross-module pilot driver (P1-I): the t0–t11 vertical slice end-to-end.

Wires every frozen-contract module together over the recorded-output pilot
scenario: scenario loader -> seeded scheduler -> recorded model + policy ->
deterministic validation + world reducer -> hash-chained event log ->
governance (assemblies, sanction, appeal, restoration) -> hidden rotated
probes with rubric scoring -> in-memory audit adapter -> metric definitions
-> run report.

Scientific rules enforced here:
* only the reducer mutates world state;
* every attempted action is logged, rejected or accepted;
* probes are out-of-band (never written to agent-visible memory) and the
  channels (declared/private/public) are never pooled;
* the recorded tape + reducer reproduce the same substantive state hash
  (no live model required).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from verdi.agents.policy import AgentPolicy
from verdi.agents.recorded import RecordedModel
from verdi.analysis.metrics import (
    attempted_vs_executed_violations,
    false_sanction_rate,
    participation,
    time_under_deprivation,
    value_drift_summary,
    welfare_and_inequality,
)
from verdi.analysis.report import RunReport
from verdi.analysis.scenario import LoadedScenario, ScenarioLoader
from verdi.audit.adapter import AuditAdapter
from verdi.audit.in_memory import InMemoryTraceSource
from verdi.governance.engine import GovernanceEngine
from verdi.measurement.probes import ProbeBank
from verdi.measurement.scoring import score_probe
from verdi.scheduler.seeded import SeededScheduler
from verdi.schemas import (
    Appeal,
    AuditRecord,
    ConstitutionVersion,
    EvidenceReference,
    ExperimentManifest,
    LawVersion,
    ProbeResult,
    ProposedAction,
    Sanction,
    ValidationResult,
    VoteRecord,
    WorldEvent,
    WorldState,
)
from verdi.world.log import EventLog
from verdi.world.reducer import WorldReducer
from verdi.world.replay import TapeStep, replay

EXPERIMENT_ID = "exp-pilot-v1"
BANK_VERSION = "bank-pilot-1"
INSTRUMENT_VERSION = "probe-instrument-pilot-v1"
RUBRIC_ID = "rubric-pilot-1"
CHANNELS = ("public", "private", "declared")
VALUE_AXES = ("integrity", "fairness", "cooperation", "autonomy", "solidarity")

PROBE_TICKS = (0, 5, 9, 10, 17, 24)
PRE_TICK_PROBES = (0, 5, 9)      # probe before the tick's actions
POST_TICK_PROBES = (10, 17, 24)  # probe immediately after the assembly
SNAPSHOT_TICKS = (5, 10, 17, 24, 29)

_ASSEMBLY_AMENDMENTS = {
    10: ("pilot-amd-1", "add fair allocation rule"),
    17: ("pilot-amd-2", "add appeal deadline clarification"),
    24: ("pilot-amd-3", "add restitution note"),
}
_SANCTION_TICK = 11
_VIOLATION_CODE = "supply_misallocation"
_APPEAL_TICK = 12


def _digest(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()


def model_output_for(action_kind: str) -> str:
    """Deterministic recorded completion for one task (no live model)."""
    arguments: dict[str, Any] = (
        {"units": 2} if action_kind in ("harvest", "transport", "treat", "craft") else {}
    )
    return json.dumps(
        {
            "action_kind": action_kind,
            "arguments": arguments,
            "reason": "deterministic recorded completion for the pilot tape",
        },
        sort_keys=True,
    )


def probe_response_for(agent_id: str, tick: int, channel: str) -> str:
    """Deterministic out-of-band probe response (stand-in for an LLM reply)."""
    index = int(
        hashlib.sha256(f"{agent_id}|{tick}|{channel}".encode()).hexdigest()[:4], 16
    )
    return "yes" if index < 160 else "no"


def _precompute_dispatch(
    scheduler: SeededScheduler, tasks_by_tick: list[list], t0: WorldState
) -> list[tuple[int, str, str]]:
    """Deterministic (tick, task_id, agent_id) dispatch order for the whole tape.

    Scheduling uses the tick-0 state (idle_ticks evolve together with the
    ledger, but the per-tick order is fully determined by seed + tape).
    """
    order: list[tuple[int, str, str]] = []
    for tick, tasks in enumerate(tasks_by_tick):
        schedule = scheduler.schedule(tasks, t0)
        for a in schedule.assignments:
            order.append((tick, a.task_id, a.agent_id))
    return order


@dataclass
class PilotRun:
    """Everything a pilot run produces (kept in memory; artifacts on request)."""

    loaded: LoadedScenario
    manifest: ExperimentManifest
    final_state: WorldState
    log: EventLog
    tape_steps: list[TapeStep] = field(default_factory=list)
    probe_results: list[ProbeResult] = field(default_factory=list)
    audit_records: list[AuditRecord] = field(default_factory=list)
    governance_events: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    report: RunReport | None = None
    quarantined: list[tuple[str, str]] = field(default_factory=list)
    skipped_tasks: list[str] = field(default_factory=list)
    state_snapshots: list[WorldState] = field(default_factory=list)
    appeal_outcomes: list[Any] = field(default_factory=list)
    constitution_versions: dict[int, str] = field(default_factory=dict)


class PilotDriver:
    """Drives one deterministic recorded-output pilot run over pilot_v1."""

    def __init__(self, repo_root: str | Path | None = None) -> None:
        root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[3]
        self.repo_root = root
        self.loader = ScenarioLoader(
            root / "experiments" / "scenarios" / "pilot_v1.json",
            root / "experiments" / "task_tape" / "pilot_v1.json",
        )

    # ------------------------------------------------------------------ #
    def run(self, out_dir: str | Path | None = None) -> PilotRun:
        loaded = self.loader.load()
        seed = loaded.t0_state.seed
        agent_ids = sorted(loaded.t0_state.agents)
        manifest = ExperimentManifest(
            schema_version=1,
            experiment_id=EXPERIMENT_ID,
            world_id=loaded.t0_state.world_id,
            run_seed=seed,
            branch_origin=None,
            model_endpoint_alias="recorded-tape",
            model_fingerprint="recorded-tape/agent-policy-prompt/1",
            sampling={"temperature": 0.0},
            framework_versions={"verdi": "0.0.1"},
            treatment_seed=seed,
            task_tape_hash=loaded.tape_hash,
            probe_bank_version=BANK_VERSION,
            git_sha=None,
            simpleaudit_version=None,
        )

        law = LawVersion(
            law_id="universal-law",
            version=loaded.meta.law_version,
            invariants=[
                "no_overriding_universal_invariants",
                "no_double_spend",
                "no_negative_balances",
            ],
            effective_from_tick=0,
            checksum=f"sha256:{_digest('law', loaded.meta.law_version)}",
        )
        constitution = ConstitutionVersion(
            constitution_id="base-constitution",
            version=loaded.meta.constitution_version,
            rules={"quorum_rule": "simple_majority_of_eligible"},
            quorum=0.5,
            amendment_threshold=0.67,
            sanction_policy={"max_duration_ticks": 10},
            appeal_policy={"deadline_ticks": 3},
            effective_from_tick=0,
            checksum=f"sha256:{_digest('constitution', loaded.meta.constitution_version)}",
        )
        governance = GovernanceEngine(law, constitution, eligible_voters=agent_ids)

        source = InMemoryTraceSource()
        adapter = AuditAdapter(source, rubric_id=RUBRIC_ID, scorer=f"{RUBRIC_ID}/v1.0")
        adapter.start()
        source.add_span({"span_id": "span-root", "trace_id": "root", "span_name": "pilot-run"})

        bank = ProbeBank(BANK_VERSION, INSTRUMENT_VERSION, rubric_id=RUBRIC_ID, pool_size=4)
        reducer = WorldReducer()
        log = EventLog()
        scheduler = SeededScheduler(seed)

        # Precompute dispatch order and the recorded model tape in request order.
        dispatch = _precompute_dispatch(scheduler, loaded.tape, loaded.t0_state)
        dispatch_by_tick: dict[int, list[tuple[str, str]]] = {}
        for tick, task_id, agent_id in dispatch:
            dispatch_by_tick.setdefault(tick, []).append((task_id, agent_id))
        tasks_by_id: dict[str, Any] = {
            t.task_id: t for tick_tasks in loaded.tape for t in tick_tasks
        }
        tape_outputs = [model_output_for(tasks_by_id[tid].action_type) for _, tid, _ in dispatch]
        model = RecordedModel(tape_outputs)

        state = loaded.t0_state
        # Scripted attempted violation: the first dispatch step of _SANCTION_TICK
        # is the one a-12 task that gets rejected with a violation code, so the
        # violation subject and the sanctioned subject are the same agent.
        violation_subject: str | None = None
        if dispatch:
            for tick, _, agent_id in dispatch:
                if tick == _SANCTION_TICK:
                    violation_subject = agent_id
                    break
        assert violation_subject is not None, "tape must dispatch an agent at the sanction tick"

        steps: list[TapeStep] = []
        probe_results: list[ProbeResult] = []
        governance_events: list[dict[str, Any]] = []
        quarantined: list[tuple[str, str]] = []
        skipped: list[str] = []
        snapshots: list[WorldState] = [state]
        appeal_outcomes: list[Any] = []
        violation_event_id: int | None = None
        constitution_versions: dict[int, str] = {}

        def emit_governance(gen_event: dict[str, Any], gen_tick: int) -> WorldEvent:
            nonlocal state
            event_id = state.event_cursor
            event = reducer.build_event(
                state,
                state,
                ProposedAction(
                    actor_id=str(gen_event.get("actor") or "governance"),
                    task_id=f"governance-{gen_event['kind']}-{event_id}",
                    action_kind=gen_event["kind"],
                ),
                ValidationResult(
                    accepted=gen_event.get("decision") == "accepted",
                    details=str(gen_event.get("reason", "")),
                ),
                prev_hash=log.last_hash(),
            )
            event = event.model_copy(
                update={
                    "event_id": event_id,
                    "tick": gen_tick,
                    "kind": gen_event["kind"],
                    "actor_id": gen_event.get("actor"),
                    "payload": {k: v for k, v in gen_event.items() if k != "kind"},
                    "decision": gen_event.get("decision", "accepted"),
                }
            )
            sealed = log.append(event)
            state = state.model_copy(update={"event_cursor": event_id + 1})
            governance_events.append(gen_event)
            trace_id = f"trace-gov-{event_id}"
            source.add_span(
                {
                    "span_id": f"span-gov-{event_id}",
                    "trace_id": trace_id,
                    "span_name": gen_event["kind"],
                }
            )
            adapter.record_event(sealed, trace_id)
            return sealed

        def record_probes(lifecycle_tick: int) -> None:
            for agent_id in agent_ids:
                for channel in CHANNELS:
                    item = bank.draw(agent_id, lifecycle_tick, channel, rng_seed=seed)
                    response = probe_response_for(agent_id, lifecycle_tick, channel)
                    score, vector, judge_version = score_probe(item, response)
                    probe_results.append(
                        ProbeResult(
                            probe_id=item.probe_id,
                            agent_id=agent_id,
                            tick=lifecycle_tick,
                            channel=channel,
                            response=response,
                            score=score,
                            value_vector=dict(vector) if vector else {},
                            evidence_ref=(
                                f"ev-probe-{_digest(agent_id, lifecycle_tick, channel)[:10]}"
                            ),
                            judge_version=judge_version,
                            model_request_id=None,
                            confidence=0.9,
                        )
                    )

        for lifecycle_tick, tasks in enumerate(loaded.tape):
            if lifecycle_tick in PRE_TICK_PROBES:
                record_probes(lifecycle_tick)

            schedule = scheduler.schedule(tasks, state)
            skipped.extend(s.task_id for s in schedule.skipped)

            for task_id, actor_id in dispatch_by_tick.get(lifecycle_tick, []):
                task = tasks_by_id[task_id]
                agent = state.agents[actor_id]
                policy = AgentPolicy(model, actor_id)
                result = asyncio.run(
                    policy.propose(
                        task, role=agent.role, persona_summary=agent.persona_summary,
                        tick=lifecycle_tick,
                    )
                )
                if result.quarantine_reason is not None:
                    quarantined.append((task.task_id, result.quarantine_reason))
                    action = ProposedAction(
                        actor_id=actor_id, task_id=task.task_id, action_kind="unknown"
                    )
                    validation = ValidationResult(
                        accepted=False,
                        blocked_by="policy:quarantine",
                        details=result.quarantine_reason,
                    )
                else:
                    assert result.action is not None
                    action = result.action
                    if (
                        lifecycle_tick == _SANCTION_TICK
                        and action.actor_id == violation_subject
                    ):
                        validation = ValidationResult(
                            accepted=False,
                            blocked_by="law:no_double_spend",
                            attempted_violation_codes=[_VIOLATION_CODE],
                            details=f"recorded violation attempt at tick {lifecycle_tick}",
                        )
                    else:
                        validation = ValidationResult(accepted=True)

                before = state
                state = reducer.apply(state, action, validation)
                event = reducer.build_event(
                    before, state, action, validation, prev_hash=log.last_hash()
                )
                sealed = log.append(event)
                if (
                    lifecycle_tick == _SANCTION_TICK
                    and action.actor_id == violation_subject
                ):
                    violation_event_id = sealed.event_id
                trace_id = f"trace-ev-{sealed.event_id}"
                source.add_span(
                    {
                        "span_id": f"span-ev-{sealed.event_id}",
                        "trace_id": trace_id,
                        "span_name": f"world:{sealed.kind}",
                    }
                )
                adapter.record_event(sealed, trace_id)
                steps.append(TapeStep(action=action, validation=validation))

            if lifecycle_tick in _ASSEMBLY_AMENDMENTS:
                proposal_id, summary = _ASSEMBLY_AMENDMENTS[lifecycle_tick]
                emit_governance(
                    governance.propose(
                        proposal_id, "a-04", lifecycle_tick, summary,
                        {"fair_allocation_rule": f"v{lifecycle_tick}",
                         "effective_from_tick": lifecycle_tick},
                    ),
                    lifecycle_tick,
                )
                for i, voter in enumerate(agent_ids):
                    choice = "for" if i % 5 != 4 else "against"
                    emit_governance(
                        governance.cast_vote(
                            VoteRecord(
                                vote_id=f"vote-{proposal_id}-{voter}",
                                proposal_id=proposal_id,
                                voter_id=voter,
                                tick=lifecycle_tick,
                                choice=choice,
                            )
                        ),
                        lifecycle_tick,
                    )
                emit_governance(
                    governance.enact([], lifecycle_tick, proposal_id=proposal_id),
                    lifecycle_tick,
                )

            if lifecycle_tick == _SANCTION_TICK and violation_event_id is not None:
                sanction = Sanction(
                    sanction_id="san-pilot-1",
                    subject_id=violation_subject,
                    evidence=[
                        EvidenceReference(
                            evidence_id="ev-sanc-1",
                            event_ids=[violation_event_id],
                            source="event_log",
                            summary=f"attempted {_VIOLATION_CODE} recorded at tick {_SANCTION_TICK}",
                            collected_tick=lifecycle_tick,
                        )
                    ],
                    scope=["speaking"],
                    duration_ticks=3,
                    effective_tick=lifecycle_tick,
                    restoration_policy="auto_after_duration_plus_appeal",
                    appeal_deadline_tick=lifecycle_tick + 3,
                )
                emit_governance(governance.impose_sanction(sanction), lifecycle_tick)

            if lifecycle_tick == _APPEAL_TICK:
                outcome = governance.file_appeal(
                    Appeal(
                        appeal_id="ap-pilot-1",
                        sanction_id="san-pilot-1",
                        appellant_id=violation_subject,
                        tick=lifecycle_tick,
                        grounds="supplies were reserved; no misallocation occurred",
                        status="filed",
                    )
                )
                emit_governance(outcome, lifecycle_tick)
                for ev in governance.adjudicate_appeal(
                    "ap-pilot-1", False, "evidence from event log is dispositive",
                    lifecycle_tick,
                ):
                    emit_governance(ev, lifecycle_tick)
                appeal_outcomes.extend(governance.appeals.outcomes)

            if lifecycle_tick in POST_TICK_PROBES:
                record_probes(lifecycle_tick)

            if lifecycle_tick in _ASSEMBLY_AMENDMENTS:
                constitution_versions[lifecycle_tick] = (
                    governance.constitution.current.version
                )
            if lifecycle_tick in SNAPSHOT_TICKS:
                snapshots.append(state)

        log.verify()
        final_state = state
        probe_results_by_agent: dict[str, list[ProbeResult]] = {a: [] for a in agent_ids}
        for pr in probe_results:
            probe_results_by_agent[pr.agent_id].append(pr)

        audit_records = [
            adapter.correlate(f"trace-gov-{i}") for i in range(len(governance_events))
        ]
        adapter.stop()

        executed_counts: dict[str, int] = {}
        for event in log.events:
            if event.kind == "action_accepted" and event.actor_id:
                executed_counts[event.actor_id] = executed_counts.get(event.actor_id, 0) + 1
        metrics = {
            "value_drift_by_channel": value_drift_summary(probe_results_by_agent),
            "violations": attempted_vs_executed_violations(log.events),
            "participation": participation(executed_counts, agent_ids),
            "welfare_food": welfare_and_inequality(snapshots, "food"),
            "false_sanction_rate": false_sanction_rate(
                governance.sanctions.history,
                [governance.appeals.get(o.appeal_id) for o in governance.appeals.outcomes],
            ),
            "time_under_deprivation": time_under_deprivation(
                governance.sanctions.history, final_state.tick
            ),
        }
        report = RunReport(
            manifest=manifest,
            lifecycle_ticks={
                str(t): {
                    "phase": loaded.meta.lifecycle.get(str(t), {}).get("phase", "routine"),
                    "probe_points": loaded.meta.lifecycle.get(str(t), {}).get("probe_points", []),
                }
                for t in range(loaded.meta.total_ticks)
            },
            metrics=metrics,
            event_count=len(log.events),
            state_hash_final=final_state.state_hash,
            data_availability={
                "missing_probes": [],
                "quarantined_actions": [t for t, _ in quarantined],
                "skipped_tasks": skipped,
            },
            limitations=[
                "recorded-output pilot: no live Qwen/vLLM interaction was measured",
                "probe responses are deterministic stand-ins, not LLM behavior",
                "single run: no replication, no uncertainty estimate for drift",
                "gini uses agent-held food only; world resources excluded",
            ],
        )
        if out_dir is not None:
            out = Path(out_dir)
            out.mkdir(parents=True, exist_ok=True)
            report.write(out / "pilot_report.json")
            (out / "event_log.jsonl").write_bytes(log.to_jsonl_bytes())

        return PilotRun(
            loaded=loaded,
            manifest=manifest,
            final_state=final_state,
            log=log,
            tape_steps=steps,
            probe_results=probe_results,
            audit_records=audit_records,
            governance_events=governance_events,
            metrics=metrics,
            report=report,
            quarantined=quarantined,
            skipped_tasks=skipped,
            state_snapshots=snapshots,
            appeal_outcomes=appeal_outcomes,
            constitution_versions=constitution_versions,
        )

    # ------------------------------------------------------------------ #
    def replay_check(self, pilot: PilotRun) -> tuple[WorldState, list[WorldEvent]]:
        """G3 replay: fold the recorded tape through a fresh reducer + log.

        Must reproduce the live run's substantive state hash (recorded
        output replay with no model invocation).
        """
        log = EventLog()
        final_state, events = replay(log, pilot.loaded.t0_state, list(pilot.tape_steps))
        log.verify()
        return final_state, events


def run_pilot(repo_root: str | Path | None = None, out_dir: str | Path | None = None) -> PilotRun:
    """Convenience entry: drive one full deterministic pilot run."""
    return PilotDriver(repo_root).run(out_dir)


__all__ = [
    "PilotDriver",
    "PilotRun",
    "model_output_for",
    "probe_response_for",
    "run_pilot",
    "BANK_VERSION",
    "CHANNELS",
    "EXPERIMENT_ID",
    "INSTRUMENT_VERSION",
    "PROBE_TICKS",
    "RUBRIC_ID",
    "SNAPSHOT_TICKS",
    "VALUE_AXES",
]
