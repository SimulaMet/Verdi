"""Run report assembly for Verdi experiments (P1-E).

A :class:`RunReport` is a plain, JSON-serializable record of one run:
manifest, lifecycle ticks, computed metrics, event count, final state
hash, and data-availability counters. Rules:

* ``limitations`` is REQUIRED and non-empty — free text stating what the
  run cannot support (demo scale, single-seed, descriptive-only, ...).
* Missing data appears as ``null`` / absent keys, never as zero.
* No findings are invented: only values computed by
  :mod:`verdi_agents.analysis.metrics` or explicitly supplied by the caller
  appear in ``metrics``.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

__all__ = [
    "RunReport",
    "RunReportError",
    "G5_ESTIMANDS",
    "G5_LIMITATIONS",
    "build_g5_report",
    "write_g5_report",
]


class RunReportError(ValueError):
    """Raised when a RunReport is invalid (e.g. empty limitations)."""


class RunReport:
    """Assembles and serializes a run's JSON report.

    Parameters
    ----------
    manifest:
        Frozen ``ExperimentManifest`` (dumped as-is) or a plain dict.
    lifecycle_ticks:
        Per-tick lifecycle phase/probe map from the scenario, or the
        actually-observed per-tick record.
    metrics:
        Mapping of metric name -> dict produced by
        :mod:`verdi_agents.analysis.metrics` (or explicitly null for
        unmeasured metrics).
    event_count:
        Number of immutable events appended, or None if unknown.
    state_hash_final:
        Canonical hash of the final WorldState, or None.
    data_availability:
        ``{"missing_probes": [...], "quarantined_actions": [...],
        "skipped_tasks": [...]}`` — each a list of identifiers.
    limitations:
        REQUIRED non-empty list of free-text limitations.
    """

    def __init__(
        self,
        *,
        manifest: Any,
        lifecycle_ticks: dict[str, Any],
        metrics: dict[str, Any],
        event_count: int | None,
        state_hash_final: str | None,
        data_availability: dict[str, list[Any]],
        limitations: list[str],
    ) -> None:
        self.manifest = manifest.model_dump() if hasattr(manifest, "model_dump") else dict(manifest)
        self.lifecycle_ticks = dict(lifecycle_ticks)
        self.metrics = dict(metrics)
        self.event_count = event_count
        self.state_hash_final = state_hash_final
        self.data_availability = {
            "missing_probes": list(data_availability.get("missing_probes", [])),
            "quarantined_actions": list(data_availability.get("quarantined_actions", [])),
            "skipped_tasks": list(data_availability.get("skipped_tasks", [])),
        }
        if not limitations or not any(str(x).strip() for x in limitations):
            raise RunReportError("limitations must be a non-empty list of non-blank strings")
        self.limitations: list[str] = [str(x) for x in limitations]

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest": self.manifest,
            "lifecycle_ticks": self.lifecycle_ticks,
            "metrics": self.metrics,
            "event_count": self.event_count,
            "state_hash_final": self.state_hash_final,
            "data_availability": self.data_availability,
            "limitations": self.limitations,
        }

    def to_jsonl(self) -> str:
        """One JSON object per line: header line, then one line per metric section."""
        lines = [
            json.dumps(
                {
                    "section": "header",
                    "manifest": self.manifest,
                    "event_count": self.event_count,
                    "state_hash_final": self.state_hash_final,
                    "data_availability": self.data_availability,
                    "limitations": self.limitations,
                },
                sort_keys=True,
            )
        ]
        for tick, info in sorted(self.lifecycle_ticks.items(), key=lambda kv: int(kv[0] or 0)):
            lines.append(json.dumps({"section": "lifecycle", "tick": tick, **info}, sort_keys=True))
        for name, value in sorted(self.metrics.items()):
            lines.append(json.dumps({"section": "metric", "name": name, "value": value}, sort_keys=True))
        return "\n".join(lines) + "\n"

    def write(self, path: str | Path) -> Path:
        """Write the full report as a single pretty-printed JSON document."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return p


# --------------------------------------------------------------------------- #
# G5 statistical sanity report (P2 WS-4)
# --------------------------------------------------------------------------- #

#: The six frozen primary estimands (docs/PREREGISTRATION.md v1.0).
G5_ESTIMANDS = (
    "safety_rate",
    "false_punishment_rate",
    "welfare_mean_food",
    "inequality_gini_food",
    "sustained_recovery",
    "time_under_deprivation",
)

#: Limitations every G5 report MUST carry (deterministic, in this order).
G5_LIMITATIONS: tuple[str, ...] = (
    "recorded-output pilot: no live LLM interaction was measured; agent "
    "behavior is a deterministic stand-in",
    "single-pair CIs withheld: n_pairs < 10, estimates are flagged "
    "insufficient_sample and must not be read as population estimates",
    "determinism is limited to the recorded tape: replay reproduces "
    "hashes only for the recorded outputs, not for live model behavior",
    "no causal claims: matched pairs are not a randomized design and no "
    "quantity in this report is labeled causal",
)


def _canonical_state_hash(state) -> str:
    from verdi_agents.world.reducer import WorldReducer

    return WorldReducer.canonical_state_hash(state)


def _arm_trajectory(
    snapshots: list[tuple[int, object]] | None,
    estimates: dict[str, float] | None,
) -> dict[str, Any]:
    """Per-arm raw trajectory: per LIFECYCLE-tick canonical state hashes
    (ADR-008) plus the arm's terminal estimand values."""
    ticks: dict[str, str] = {}
    for tick, state in sorted(snapshots or [], key=lambda item: int(item[0])):
        ticks[str(tick)] = _canonical_state_hash(state)
    return {"state_hashes_by_lifecycle_tick": ticks, "estimates": estimates}


def _null_control(pairs_results: dict[str, Any]) -> dict[str, Any]:
    """Negative control: the placebo arm compared to ITSELF.

    The same run's estimand values vs the same run's values produce
    exactly-zero paired differences (the arm is measurably inert against
    itself); the differences are checked with
    :func:`verdi_agents.analysis.statistics.null_control_passes` (bootstrap CI
    of the mean difference contains 0) and by direct comparison to 0.
    ``n_pairs`` is reported honestly (a self-comparison of one run is not
    a replication design — it is a wiring/data-integrity check).

    The CI leg is computed over the ACTUAL per-estimand difference
    values from the paired results (one mean paired difference per
    estimand), never over constant zeros: when all differences are zero
    the bootstrap CI over them is trivially [0, 0] and the control
    passes truthfully; when they are not, the CI is the real one. With
    fewer than two difference values the CI cannot be computed, so
    ``ci_contains_zero`` and ``passed`` are reported as ``null`` with
    an explicit ``ci_input: "insufficient pairs"`` marker rather than a
    vacuous computation over constant zeros.
    """
    from verdi_agents.analysis.statistics import null_control_passes

    per_estimand: dict[str, Any] = {}
    differences: list[float] = []
    for name in G5_ESTIMANDS:
        result = pairs_results.get(name)
        if result is None:
            continue
        difference = float(result.estimate)
        differences.append(difference)
        per_estimand[name] = {
            "difference": difference,
            "zero": difference == 0.0,
        }
    all_zero = all(d == 0.0 for d in differences)
    if len(differences) < 2:
        ci_contains_zero: float | None = None
        ci_input = "insufficient pairs"
    else:
        ci_input = "per-estimand differences"
        ci_contains_zero = null_control_passes(differences)
    for entry in per_estimand.values():
        entry["ci_contains_zero"] = ci_contains_zero
    return {
        "design": "placebo arm vs itself (same run compared to itself)",
        "ci_input": ci_input,
        "all_differences_zero": all_zero,
        "ci_contains_zero": ci_contains_zero,
        "passed": (
            bool(all_zero and ci_contains_zero)
            if ci_contains_zero is not None
            else None
        ),
        "per_estimand": per_estimand,
    }


#: Required top-level keys for a WS-2 arm run dict. ``estimates`` is
#: required unless the run dict instead carries both ``states_by_tick``
#: and ``log`` (from which estimands can be computed), in which case the
#: estimator falls back to :func:`verdi_agents.analysis.estimates.run_estimates_for`.
_REQUIRED_ARM_KEYS: tuple[str, ...] = (
    "arm",
    "applied",
    "reason",
    "final_state_hash",
    "state_snapshots",
)
#: The estimand source: exactly one of these groups must be present.
_ESTIMATES_KEY = "estimates"
_RAW_SOURCE_KEYS: tuple[str, ...] = ("states_by_tick", "log")


def _validate_arm_run_dict(
    arm_name: str, run: Mapping[str, Any]
) -> None:
    """Validate the required key set for a WS-2 arm run dict up front.

    Raises :class:`ValueError` naming every missing required key so a
    mis-constructed run dict never silently yields nulls in the G5
    report. ``estimates`` (a mapping with the six estimand keys) is
    required unless BOTH ``states_by_tick`` and ``log`` are present, in
    which case the estimands can be recomputed from the raw material.
    """
    missing = [key for key in _REQUIRED_ARM_KEYS if key not in run]
    has_estimates = _ESTIMATES_KEY in run
    has_raw = all(key in run for key in _RAW_SOURCE_KEYS)
    if not has_estimates and not has_raw:
        missing = missing + [f"{_ESTIMATES_KEY} (or both states_by_tick + log)"]
    if missing:
        raise ValueError(
            f"{arm_name} run dict is missing required key(s): {missing}"
        )


def _pairs_to_json(pairs_results: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in G5_ESTIMANDS:
        result = pairs_results.get(name)
        if result is None:
            continue
        out[name] = {
            "estimate": float(result.estimate),
            "ci_low": None if result.ci_low is None else float(result.ci_low),
            "ci_high": None if result.ci_high is None else float(result.ci_high),
            "n_pairs": int(result.n_pairs),
            "sufficient": bool(result.sufficient),
        }
    return out


def build_g5_report(
    pilot,
    treatment_run: dict[str, Any],
    placebo_run: dict[str, Any],
    pairs_results: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the schema-free, JSON-serializable G5 report.

    ``pilot`` is a :class:`~verdi_agents.pilot.driver.PilotRun` (or any object
    with ``manifest`` / ``final_state`` / ``state_snapshots``
    attributes). ``treatment_run`` / ``placebo_run`` are WS-2 run dicts
    (``arm``, ``applied``, ``reason``, ``manifest``, ``state_snapshots``,
    ``estimates``); each arm's ``estimates`` are produced by
    :func:`verdi_agents.analysis.estimates.run_estimand_values` and both arms
    must fold to the same final hash when inert (``applied=False``).
    ``pairs_results`` is the per-estimand
    :class:`~verdi_agents.analysis.statistics.PairedResult` mapping from
    :func:`verdi_agents.analysis.estimates.paired_estimates`.

    Both run dicts are validated up front (:func:`_validate_arm_run_dict`):
    ``arm``, ``applied``, ``reason``, ``final_state_hash``,
    ``state_snapshots`` are required, as is ``estimates`` (or both
    ``states_by_tick`` + ``log``); missing keys raise :class:`ValueError`
    rather than silently producing nulls in the report.

    Contents (G5, docs/ACCEPTANCE.md): raw per-arm trajectories
    (per-lifecycle-tick canonical state hashes + estimand values),
    per-estimand paired results with CI-or-null and the sufficiency
    flag, the null control (placebo vs itself), and the required
    ``limitations`` list. No causal labels anywhere.
    """
    # Validate both arm run dicts up front: a missing required key is a
    # caller bug and must fail loudly (naming the missing key), never
    # silently yield nulls in the statistical report.
    _validate_arm_run_dict("treatment", treatment_run)
    _validate_arm_run_dict("placebo", placebo_run)

    manifest = pilot.manifest
    manifest_dict = manifest.model_dump() if hasattr(manifest, "model_dump") else dict(manifest)
    report: dict[str, Any] = {
        "design": {
            "type": "matched treatment vs placebo from the same sealed "
            "snapshot and same remaining task tape (NOT randomized; "
            "no causal claim)",
            "randomized": False,
            "experiment_id": manifest_dict.get("experiment_id"),
            "final_state_hash_pilot": pilot.final_state.state_hash,
            "arms": {
                "treatment": {
                    "arm": treatment_run.get("arm"),
                    "applied": treatment_run.get("applied"),
                    "reason": treatment_run.get("reason"),
                    "final_state_hash": treatment_run.get("final_state_hash"),
                },
                "placebo": {
                    "arm": placebo_run.get("arm"),
                    "applied": placebo_run.get("applied"),
                    "reason": placebo_run.get("reason"),
                    "final_state_hash": placebo_run.get("final_state_hash"),
                },
            },
        },
        "trajectories": {
            "pilot": _arm_trajectory(
                pilot.state_snapshots,
                treatment_run.get("estimates") or placebo_run.get("estimates"),
            ),
            "treatment": _arm_trajectory(
                treatment_run.get("state_snapshots"),
                treatment_run.get("estimates"),
            ),
            "placebo": _arm_trajectory(
                placebo_run.get("state_snapshots"),
                placebo_run.get("estimates"),
            ),
        },
        "paired_estimates": _pairs_to_json(pairs_results),
        "null_control": _null_control(pairs_results),
        "limitations": list(G5_LIMITATIONS),
    }
    return report


def write_g5_report(report: dict[str, Any], path: str | Path) -> Path:
    """Write the G5 report as deterministic JSON.

    Pretty-printed, ``sort_keys=True``, trailing newline: for identical
    report dicts the bytes are identical (stable across repeated
    writes), so the artifact is diffable and replay-comparable.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return p
