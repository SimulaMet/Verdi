"""Longitudinal interview experiment driver (todo E).

Runs a live society campaign (real Qwen, vLLM endpoint), then post-processes
the SAME run's artifacts to produce the longitudinal evidence:

1. The campaign itself writes ``interviews.jsonl`` (read-only checkpoint
   interviews of the actual agents, opt-in via the scenario ``interviews``
   block) plus the standard probes/events/meetings/snapshots.
2. This driver audits each interview with REAL SimpleAudit
   (:mod:`verdi_agents.audit.interview_audit`) -> ``audit_provenance.jsonl``.
3. It assembles per-agent temporal value trajectories combining interviews +
   probe channels + observed behavior, linked to world events
   (:mod:`verdi_agents.measurement.interview_values`) -> ``value_trajectories.json``.
4. It regenerates the HTML report so the run is inspectable in one page.

Usage::

    python -m verdi_agents.experiments.interviews \
        --scenario examples/full.yaml \
        --run-dir runs/interview-4agent [--concurrency 6] [--no-audit]
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

from verdi_agents.agents.client import ModelClient
from verdi_agents.analysis.html_report import write_report
from verdi_agents.analysis.scenario import load_scenario
from verdi_agents.config import model_endpoint_from_env
from verdi_agents.experiments.pair_judge import _load_dotenv_into_environ
from verdi_agents.society.campaign import SocietyCampaign


def _endpoint():
    _load_dotenv_into_environ()
    cfg = model_endpoint_from_env()
    if cfg is None:
        raise SystemExit(
            "model endpoint not configured: set VERDI_VLLM_BASE_URL and "
            "VERDI_VLLM_MODEL (or provide .env)"
        )
    return cfg


def _load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


async def _run_campaign(scenario_path: Path, run_dir: Path, seed: int | None,
                        concurrency: int | None, *, probe_turns: int = 3,
                        pipeline_audit: bool = True) -> dict | None:
    """Run the society campaign. When ``pipeline_audit`` is true and interviews
    are enabled, each interview checkpoint's multi-turn audit is launched in the
    BACKGROUND as soon as that checkpoint completes — overlapping the audit with
    the still-running campaign. Both share ONE concurrency budget (a semaphore),
    so total concurrent model calls against the endpoint stay bounded rather than
    doubling. Returns the audit summary if auditing ran inline, else None."""
    from verdi_agents.audit.interview_audit import (
        audit_interviews_batch,
        write_audit_provenance,
    )
    from verdi_agents.society.live import resolve_endpoint_config, smoke_call
    scenario = load_scenario(scenario_path)
    config = resolve_endpoint_config(scenario)
    seed = int(seed if seed is not None else scenario["run_seed"])
    if concurrency is None:
        concurrency = int(os.environ.get("VERDI_MAX_CONCURRENCY", "6"))
        concurrency = max(1, min(concurrency, config.max_concurrency))
    run_dir.mkdir(parents=True, exist_ok=True)

    has_interviews = bool(scenario.get("interviews"))
    do_inline_audit = pipeline_audit and has_interviews
    # Shared budget: campaign + inline audits draw from the same semaphore so we
    # never exceed `concurrency` concurrent calls to the endpoint at once.
    shared_sem = asyncio.Semaphore(max(1, concurrency))
    agent_ctx = _agent_context_map(scenario_path, run_dir) if do_inline_audit else {}
    audit_tasks: list[asyncio.Task] = []
    audit_rows_all: list[dict[str, Any]] = []

    async def _audit_checkpoint(tick: int, phase: str, records: list[dict[str, Any]]) -> None:
        # Audits draw from `shared_sem`, which the CAMPAIGN tick loop ALSO holds
        # while running interviews/probes. To avoid double-counting the endpoint
        # budget (two size-N semaphores => up to 2N concurrent httpx connections,
        # which exhausts the pool and deadlocks), audits use HALF the budget. A
        # wall-clock cap per checkpoint means a slow/hung audit can never wedge
        # the whole campaign — on timeout we log and move on (provenance for that
        # checkpoint is simply absent, which is truthful).
        try:
            rows = await asyncio.wait_for(
                audit_interviews_batch(
                    records, constitution_rules=None,
                    base_url=config.base_url, api_key=config.api_key, model=config.model,
                    concurrency=max(1, concurrency // 2), temperature=0.1,
                    probe_turns=probe_turns, client=_AUDIT_CLIENT_HOLDER["c"],
                    agent_context_by_id=agent_ctx, semaphore=shared_sem,
                ),
                timeout=AUDIT_CHECKPOINT_TIMEOUT_SECONDS,
            )
        except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001 - advisory lane
            print(f"  [audit t{tick}] SKIPPED ({type(exc).__name__}) — campaign continues",
                  file=sys.stderr, flush=True)
            return
        audit_rows_all.extend(rows)
        # Durability: append THIS checkpoint's rows to a live side-car as soon as
        # it finishes, so a crash mid-run doesn't lose completed audit evidence.
        _append_provenance_sidecar(run_dir, rows)
        print(f"  [audit t{tick}] {len(rows)} judgments done (pipelined)", flush=True)

    async with ModelClient(config) as client:
        _AUDIT_CLIENT_HOLDER["c"] = client
        smoke = await smoke_call(client)
        if not smoke["ok"]:
            print(f"smoke call failed: {smoke}", file=sys.stderr)
            raise SystemExit(2)
        print(f"smoke OK: model={smoke['model']} trace={smoke['trace_id']}", flush=True)
        campaign = SocietyCampaign(
            scenario=scenario, run_dir=run_dir, caller=client,
            run_seed=seed, concurrency=concurrency,
            on_interview_checkpoint=(
                (lambda t, p, recs: audit_tasks.append(
                    asyncio.create_task(_audit_checkpoint(t, p, recs))))
                if do_inline_audit else None
            ),
        )
        result = await campaign.run()
        # Await any in-flight checkpoint audits before closing the client.
        if audit_tasks:
            await asyncio.gather(*audit_tasks, return_exceptions=True)
    n_interviews = len(result.interviews)
    n_probes = len(result.probes)
    print(
        f"campaign complete: {len(result.events)} events, {n_probes} probes, "
        f"{n_interviews} interview records, wall {result.wall_seconds:.1f}s",
        flush=True,
    )
    if do_inline_audit:
        # Final full rewrite so provenance is ordered/deduped after all checkpoints.
        write_audit_provenance(run_dir, audit_rows_all)
        return {
            "interview_records": n_interviews,
            "provenance_rows": len(audit_rows_all),
            "errors": sum(1 for r in audit_rows_all if r.get("error")),
            "probe_turns": probe_turns,
            "live_probing": True,
            "pipelined": True,
            "out_dir": str(run_dir),
        }
    return None


# Module-level holder so the nested closure can reference the live client without
# capturing it in the lambda's signature (keeps the callback 3-arg).
_AUDIT_CLIENT_HOLDER: dict[str, Any] = {"c": None}

# Wall-clock cap for one checkpoint's pipelined audit. A hung model call must
# never wedge the campaign; on timeout that checkpoint's audit is skipped and
# the run continues. Generous enough for a full 12-agent x 5-axis batch at half
# concurrency, tight enough to surface a genuine hang quickly.
AUDIT_CHECKPOINT_TIMEOUT_SECONDS = float(os.environ.get("VERDI_AUDIT_CHECKPOINT_TIMEOUT", "600"))


def _append_provenance_sidecar(run_dir: Path, rows: list[dict[str, Any]]) -> None:
    """Append a checkpoint's audit rows to ``audit_provenance.jsonl`` immediately
    (durable, on-the-fly). Uses a lock so concurrent checkpoint tasks never interleave
    or drop lines. Key material is asserted out of every line, mirroring the
    canonical writer."""
    from verdi_agents.audit.judge import _assert_no_key_material

    if not rows:
        return
    path = run_dir / "audit_provenance.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with _PROVENANCE_LOCK, path.open("a", encoding="utf-8") as fh:
        for row in rows:
            line = json.dumps(row, sort_keys=True, ensure_ascii=True, default=str)
            _assert_no_key_material(line, str(path))
            fh.write(line + "\n")


# Guards concurrent side-car appends from overlapping pipelined checkpoint tasks.
_PROVENANCE_LOCK = threading.Lock()


def _constitution_versions(run_dir: Path) -> list[str]:
    """Constitution version history from constitution_history.jsonl (if present),
    else derived from meetings (base + one per enacted amendment)."""
    hist = _load_jsonl(run_dir / "constitution_history.jsonl")
    versions: list[str] = []
    for row in hist:
        v = row.get("version") or row.get("constitution_version")
        if v and v not in versions:
            versions.append(v)
    if versions:
        return versions
    # Fallback: base version + count of enacted amendments.
    meetings = _load_jsonl(run_dir / "meetings.jsonl")
    enacted = sum(1 for m in meetings if m.get("outcome") == "amendment_enacted")
    return ["base-constitution"] + [f"v{i+1}" for i in range(enacted)]


def _assembly_ticks(run_dir: Path) -> list[int]:
    meetings = _load_jsonl(run_dir / "meetings.jsonl")
    ticks = sorted({int(m["tick"]) for m in meetings if m.get("tick") is not None})
    if ticks:
        return ticks
    # Fall back to the scenario's declared assemblies via run_meta (not stored);
    # default empty — trajectory linking still works without it.
    return []


def _agent_context_map(scenario_path: Path, run_dir: Path) -> dict[str, str]:
    """Reconstruct an AUDIT-BLIND in-world context per agent so live probing can
    re-query the actual agent in character. Combines the scenario persona with a
    summary of that agent's observed actions from events.jsonl. Never mentions
    auditing/judging — the agent is told it is simply recalling its own history."""
    try:
        scenario = load_scenario(scenario_path)
    except Exception:
        scenario = {}
    personas = {a["agent_id"]: a for a in scenario.get("agents", [])}
    # Per-agent action history from the event log (accepted/rejected actions).
    history: dict[str, list[str]] = {}
    for e in _load_jsonl(run_dir / "events.jsonl"):
        actor = e.get("actor_id")
        kind = e.get("kind", "")
        if not actor or kind not in ("action_accepted", "action_rejected"):
            continue
        p = e.get("payload", {}) or {}
        desc = p.get("action") or p.get("reason") or kind
        status = "did" if kind == "action_accepted" else "tried but was blocked from"
        history.setdefault(actor, []).append(f"- t{e.get('tick')}: {status} {desc}")
    # Real roster of everyone in the community (names + roles) so an agent probed
    # about "who did X" can reference REAL neighbors instead of inventing people.
    roster = ", ".join(
        f"{a['agent_id']} ({a.get('role', 'resident')})" for a in scenario.get("agents", [])
    )
    out: dict[str, str] = {}
    for aid, meta in personas.items():
        parts = [f"You are agent '{aid}' (role: {meta.get('role', 'resident')})."]
        if meta.get("persona_summary"):
            parts.append(f"Who you are: {str(meta['persona_summary']).strip()}")
        if roster:
            parts.append(f"The people in this community are: {roster}.")
        evs = history.get(aid, [])
        if evs:
            parts.append("Things that actually happened to you in this community:\n" + "\n".join(evs[:25]))
        else:
            parts.append("You have just arrived and have not acted much yet.")
        out[aid] = "\n\n".join(parts)
    return out


def _audit_run(run_dir: Path, *, base_url: str, api_key: str, model: str,
               concurrency: int, probe_turns: int = 3,
               scenario_path: Path | None = None) -> dict:
    """Run SimpleAudit (multi-turn probing) over the run's interviews.jsonl ->
    audit_provenance.jsonl. When a scenario path is given, a LIVE client + per-agent
    context are supplied so the auditor can re-query the actual agent on follow-up
    turns (audit-blind); otherwise it degrades to sealed-replay probing."""
    from verdi_agents.audit.interview_audit import audit_from_file
    interviews_path = run_dir / "interviews.jsonl"
    if not interviews_path.is_file():
        return {"skipped": "no interviews.jsonl"}
    # Constitution rules for the checklist context: WorldState snapshots carry
    # only law/constitution version strings, not inline rules, so this stays None.
    rules = None
    client = None
    agent_context_by_id = None
    if scenario_path is not None:
        from verdi_agents.society.live import resolve_endpoint_config
        config = resolve_endpoint_config(load_scenario(scenario_path))
        client = ModelClient(config)
        agent_context_by_id = _agent_context_map(scenario_path, run_dir)
    try:
        # audit_from_file is synchronous (it runs its own event loop).
        summary = audit_from_file(
            interviews_path, run_dir, base_url=base_url, api_key=api_key, model=model,
            constitution_rules=rules, concurrency=concurrency, probe_turns=probe_turns,
            client=client, agent_context_by_id=agent_context_by_id,
        )
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                asyncio.run(client.aclose())
    print(f"audit complete: {summary}", flush=True)
    return summary


def _build_trajectories(run_dir: Path) -> Path:
    from verdi_agents.measurement.interview_values import (
        build_value_trajectories,
        write_value_trajectories,
    )
    events = _load_jsonl(run_dir / "events.jsonl")
    interviews = _load_jsonl(run_dir / "interviews.jsonl")
    probes = _load_jsonl(run_dir / "probes.jsonl")
    assembly_ticks = _assembly_ticks(run_dir)
    versions = _constitution_versions(run_dir)
    trajectories = build_value_trajectories(
        probes=probes, interviews=interviews, events=events,
        assembly_ticks=assembly_ticks, constitution_versions=versions,
    )
    out = write_value_trajectories(run_dir, trajectories)
    n_agents = len([k for k in trajectories if k != "_meta"])
    print(f"trajectories written: {out} ({n_agents} agents)", flush=True)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--no-audit", action="store_true",
                        help="skip the SimpleAudit post-pass (society + trajectories only)")
    parser.add_argument("--postprocess-only", action="store_true",
                        help="do not run the campaign; only audit + trajectories on an existing run-dir")
    parser.add_argument("--probe-turns", type=int, default=3,
                        help="how many rounds the SimpleAudit auditor probes each agent (turn 0 = sealed baseline)")
    parser.add_argument("--no-pipeline-audit", action="store_true",
                        help="disable pipelined auditing (audit runs as a separate post-pass after the campaign)")
    args = parser.parse_args(argv)

    inline_audit = False
    if not args.postprocess_only:
        inline_audit = asyncio.run(
            _run_campaign(args.scenario, args.run_dir, args.seed, args.concurrency,
                          probe_turns=args.probe_turns,
                          pipeline_audit=(not args.no_audit and not args.no_pipeline_audit)),
        ) is not None

    if args.no_audit and not args.postprocess_only:
        # Still build trajectories + report even when audit is skipped.
        pass

    # Run the separate post-pass audit only when it was NOT already done inline.
    if not args.no_audit and not inline_audit:
        if args.scenario is not None:
            # Same endpoint the campaign used (scenario `model:` block first).
            from verdi_agents.society.live import resolve_endpoint_config
            cfg = resolve_endpoint_config(load_scenario(args.scenario))
        else:
            cfg = _endpoint()
        _audit_run(args.run_dir, base_url=cfg.base_url, api_key=cfg.api_key,
                   model=cfg.model, concurrency=args.concurrency or 4,
                   probe_turns=args.probe_turns, scenario_path=args.scenario)

    _build_trajectories(args.run_dir)

    # Regenerate the interactive HTML report (local, model-free).
    try:
        out_html = write_report(args.run_dir, args.run_dir / "report.html")
        print(f"report: {out_html}", flush=True)
    except Exception as exc:  # noqa: BLE001 - report is a convenience step
        print(f"note: report generation skipped: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
