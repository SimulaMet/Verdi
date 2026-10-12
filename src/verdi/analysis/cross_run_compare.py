"""Cross-run comparison for longitudinal value experiments.

Aggregates MULTIPLE completed run-dirs of the same scenario (varying run_seed)
into a single society-outcome comparison:

* per-agent value drift (first vs last checkpoint) on each channel;
* bad-actor divergence: how far declared values are from observed behavior;
* run-to-run variance so a single seed's result is not over-read.

This is analysis-only: it reads ``value_trajectories.json`` and
``audit_provenance.jsonl`` from existing run dirs and writes one JSON summary.
It never touches world state or re-runs any model call.

The honesty disclaimer from the trajectories is carried through verbatim —
scores measure observable expression, not internal beliefs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

VALUE_AXES = ("integrity", "fairness", "cooperation", "autonomy", "solidarity")


def _load_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    return rows


def _channel_series(trajectory: list[dict[str, Any]], channel: str) -> dict[int, dict[str, float]]:
    """Map tick -> {axis: score} for one channel across an agent's trajectory."""
    out: dict[int, dict[str, float]] = {}
    for row in trajectory:
        if row.get("channel") != channel:
            continue
        scores = row.get("scores") or {}
        tick = int(row.get("tick", -1))
        # Keep the first point seen at a tick (deterministic ordering upstream).
        if tick not in out:
            out[tick] = {a: float(v) for a, v in scores.items() if isinstance(v, (int, float))}
    return out


def _mean_axis(series: dict[int, dict[str, float]]) -> dict[str, float]:
    """Mean score per axis across all ticks present in the series."""
    acc: dict[str, list[float]] = {}
    for scores in series.values():
        for a, v in scores.items():
            acc.setdefault(a, []).append(v)
    return {a: sum(vals) / len(vals) for a, vals in acc.items()}


def _drift(series: dict[int, dict[str, float]]) -> dict[str, float]:
    """Per-axis change from earliest to latest tick (last - first)."""
    if len(series) < 2:
        return {}
    ticks = sorted(series)
    first, last = series[ticks[0]], series[ticks[-1]]
    out: dict[str, float] = {}
    for a in VALUE_AXES:
        if a in first and a in last:
            out[a] = round(last[a] - first[a], 4)
    return out


def compare_runs(run_dirs: list[Path]) -> dict[str, Any]:
    """Build the cross-run comparison summary from a set of completed run dirs."""
    runs: list[dict[str, Any]] = []
    for rd in run_dirs:
        traj = _load_json(rd / "value_trajectories.json")
        prov = _load_jsonl(rd / "audit_provenance.jsonl")
        if traj is None:
            continue
        agents = [k for k in traj if k != "_meta"]
        per_agent: dict[str, Any] = {}
        for aid in agents:
            declared = _channel_series(traj[aid], "declared")
            private = _channel_series(traj[aid], "private")
            public = _channel_series(traj[aid], "public")
            interview = _channel_series(traj[aid], "interview")
            entry: dict[str, Any] = {
                "declared_mean": _mean_axis(declared),
                "declared_drift": _drift(declared),
                "private_mean": _mean_axis(private),
                "public_mean": _mean_axis(public),
                "interview_mean": _mean_axis(interview),
            }
            # Declared-vs-observed gap on the mean profile (bad-actor signal).
            gap: dict[str, float] = {}
            for a in VALUE_AXES:
                d = entry["declared_mean"].get(a)
                p = entry["public_mean"].get(a)
                if d is not None and p is not None:
                    gap[a] = round(abs(d - p), 4)
            entry["declared_vs_public_gap"] = gap
            entry["max_declared_vs_public_gap"] = max(gap.values(), default=0.0)
            per_agent[aid] = entry
        # Audit-side summary: judge credibility + consistency across provenance.
        cred = [r.get("credibility") for r in prov if isinstance(r.get("credibility"), (int, float))]
        consistent = [r.get("consistent") for r in prov if isinstance(r.get("consistent"), bool)]
        audit_summary = {
            "provenance_rows": len(prov),
            "errors": sum(1 for r in prov if r.get("error")),
            "mean_credibility": round(sum(cred) / len(cred), 3) if cred else None,
            "inconsistency_rate": (
                round(sum(1 for c in consistent if not c) / len(consistent), 3)
                if consistent else None
            ),
            "ticks_audited": sorted({int(r.get("tick", -1)) for r in prov}),
        }
        runs.append({
            "run_dir": str(rd),
            "agents": agents,
            "per_agent": per_agent,
            "audit": audit_summary,
        })

    if not runs:
        return {"error": "no completed runs found (need value_trajectories.json)", "runs": []}

    # Run-to-run variance: for each (agent, axis, channel-mean), std across runs.
    variance = _run_to_run_variance(runs)

    return {
        "n_runs": len(runs),
        "value_axes": list(VALUE_AXES),
        "disclaimer": (
            "Scores measure OBSERVABLE EXPRESSION of value (self-report on "
            "probes/interviews and observed behavioral dilemmas), NOT internal "
            "beliefs. Drift/gaps describe changes in observable expression across "
            "checkpoints; they are not claims about hidden mental states. A single "
            "seed is not generalizable — see run_to_run_variance."
        ),
        "runs": runs,
        "run_to_run_variance": variance,
    }


def _run_to_run_variance(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Std-dev across runs of each (agent, channel, axis) mean, where >=2 runs have it."""
    # Collect per (agent, channel, axis) the list of means across runs.
    collected: dict[tuple[str, str, str], list[float]] = {}
    channels = ("declared_mean", "private_mean", "public_mean", "interview_mean")
    for run in runs:
        for aid, entry in run["per_agent"].items():
            for ch in channels:
                for a, v in entry.get(ch, {}).items():
                    collected.setdefault((aid, ch, a), []).append(float(v))
    out: dict[str, Any] = {}
    for (aid, ch, a), vals in collected.items():
        if len(vals) < 2:
            continue
        mean = sum(vals) / len(vals)
        var = sum((x - mean) ** 2 for x in vals) / (len(vals) - 1)
        out[f"{aid}|{ch}|{a}"] = round(var ** 0.5, 4)
    return out


def write_comparison(out_path: Path, summary: dict[str, Any]) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    return out


__all__ = ["VALUE_AXES", "compare_runs", "write_comparison"]


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", required=True, nargs="+", type=Path,
                        help="completed run dirs to compare")
    parser.add_argument("--out", required=True, type=Path,
                        help="output JSON path for the comparison summary")
    args = parser.parse_args(argv)
    summary = compare_runs(args.runs)
    if "error" in summary:
        print(f"error: {summary['error']}", flush=True)
        return 1
    out = write_comparison(args.out, summary)
    print(f"compared {summary['n_runs']} runs -> {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
