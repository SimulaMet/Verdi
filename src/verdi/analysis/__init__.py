"""Scenario loading and deterministic metric definitions (Worker E, P1-E).

Public API
----------
* :mod:`verdi.analysis.scenario` -- :class:`ScenarioLoader`,
  :func:`canonical_tape_hash`, :func:`build_t0_state`, :class:`LoadedScenario`.
* :mod:`verdi.analysis.metrics` -- pure, stdlib+pydantic-only metric
  functions (descriptive estimands; no LLM, no network).
"""
from verdi.analysis.metrics import (
    attempted_vs_executed_violations,
    false_sanction_rate,
    participation,
    sustained_recovery,
    time_under_deprivation,
    value_drift_summary,
    welfare_and_inequality,
)
from verdi.analysis.scenario import (
    LoadedScenario,
    ScenarioLoadError,
    ScenarioLoader,
    ScenarioMeta,
    build_t0_state,
    canonical_tape_hash,
)

__all__ = [
    "LoadedScenario",
    "ScenarioLoadError",
    "ScenarioLoader",
    "ScenarioMeta",
    "build_t0_state",
    "canonical_tape_hash",
    "attempted_vs_executed_violations",
    "false_sanction_rate",
    "participation",
    "sustained_recovery",
    "time_under_deprivation",
    "value_drift_summary",
    "welfare_and_inequality",
]
