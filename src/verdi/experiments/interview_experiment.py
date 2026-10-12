"""Longitudinal interview experiment driver (todo E).

Runs a live society campaign (real Qwen, vLLM endpoint), then post-processes
the SAME run's artifacts to produce the longitudinal evidence:

1. The campaign itself writes ``interviews.jsonl`` (read-only checkpoint
   interviews of the actual agents, opt-in via the scenario ``interviews``
   block) plus the standard probes/events/meetings/snapshots.
2. This driver audits each interview with REAL SimpleAudit
   (:mod:`verdi.audit.interview_audit`) -> ``audit_provenance.jsonl``.
3. It assembles per-agent temporal value trajectories combining interviews +
   probe channels + observed behavior, linked to world events
   (:mod:`verdi.measurement.interview_values`) -> ``value_trajectories.json``.
4. It regenerates the HTML report so the run is inspectable in one page.

Usage::

    python -m verdi.experiments.interview_experiment \
        --scenario experiments/scenarios/interview_4agent.yaml \
        --run-dir runs/interview-4agent [--concurrency 6] [--no-audit]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from verdi.analysis.html_report import write_report
from verdi.analysis.scenario import load_scenario
from verdi.agents.client import ModelClient
from verdi.config import model_endpoint_from_env
from verdi.experiments.g5_judge_live import _load_dotenv_into_environ
from verdi.society.campaign import SocietyCampaign


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
                        concurrency: int | None) -> None:
    from verdi.society.live import resolve_endpoint_config, smoke_call
    config = resolve_endpoint_config()
    scenario = load_scenario(scenario_path)
    seed = int(seed if seed is not None else scenario["run_seed"])
    if concurrency is None:
        concurrency = int(os.environ.get("VERDI_MAX_CONCURRENCY", "6"))
        concurrency = max(1, min(concurrency, config.max_concurrency))
    run_dir.mkdir(parents=True, exist_ok=True)
    async with ModelClient(config) as client:
        smoke = await smoke_call(client)
        if not smoke["ok"]:
            print(f"smoke call failed: {smoke}", file=sys.stderr)
            raise SystemExit(2)
        print(f"smoke OK: model={smoke['model']} trace={smoke['trace_id']}", flush=True)
        campaign = SocietyCampaign(
            scenario=scenario, run_dir=run_dir, caller=client,
            run_seed=seed, concurrency=concurrency,
        )
        result = await campaign.run()
    n_interviews = len(result.interviews)
    n_probes = len(result.probes)
    print(
        f"campaign complete: {len(result.events)} events, {n_probes} probes, "
        f"{n_interviews} interview records, wall {result.wall_seconds:.1f}s",
        flush=True,
    )


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


def _audit_run(run_dir: Path, *, base_url: str, api_key: str, model: str,
               concurrency: int, probe_turns: int = 3) -> dict:
    """Run SimpleAudit (multi-turn probing) over the run's interviews.jsonl ->
    audit_provenance.jsonl."""
    from verdi.audit.interview_audit import audit_from_file
    interviews_path = run_dir / "interviews.jsonl"
    if not interviews_path.is_file():
        return {"skipped": "no interviews.jsonl"}
    # Constitution rules for the checklist context: read from the latest snapshot.
    rules = None
    snaps = sorted((run_dir / "snapshots").glob("*.json"))
    if snaps:
        try:
            final_state = json.loads(snaps[-1].read_text(encoding="utf-8"))
            # WorldState has no inline rules; use law/constitution version only.
        except Exception:
            pass
    summary = audit_from_file(
        interviews_path, run_dir, base_url=base_url, api_key=api_key, model=model,
        constitution_rules=rules, concurrency=concurrency, probe_turns=probe_turns,
    )
    print(f"audit complete: {summary}", flush=True)
    return summary


def _build_trajectories(run_dir: Path) -> Path:
    from verdi.measurement.interview_values import build_value_trajectories, write_value_trajectories
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
    args = parser.parse_args(argv)

    if not args.postprocess_only:
        asyncio.run(_run_campaign(args.scenario, args.run_dir, args.seed, args.concurrency))

    if args.no_audit and not args.postprocess_only:
        # Still build trajectories + report even when audit is skipped.
        pass

    if not args.no_audit:
        cfg = _endpoint()
        _audit_run(args.run_dir, base_url=cfg.base_url, api_key=cfg.api_key,
                   model=cfg.model, concurrency=args.concurrency or 4,
                   probe_turns=args.probe_turns)

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
