"""Scenario loading, deterministic metric definitions and run reporting.

Public API
----------
* :mod:`verdi_agents.analysis.scenario` -- :class:`ScenarioLoader`,
  :func:`canonical_tape_hash`, :func:`build_t0_state`, :class:`LoadedScenario`.
* :mod:`verdi_agents.analysis.metrics` -- pure, stdlib+pydantic-only metric
  functions (descriptive estimands; no LLM, no network).
* :mod:`verdi_agents.analysis.report` -- :class:`RunReport` JSON/JSONL assembly plus
  the paired-arm report (:func:`build_g5_report`, :func:`write_g5_report`).
* :mod:`verdi_agents.analysis.statistics` -- stdlib bootstrap/paired-effect
  estimand helpers (descriptive; never causal labels).
* :mod:`verdi_agents.analysis.estimates` -- the six pre-registered estimands
  mapped to metric functions and matched-pair estimation.
"""
from verdi_agents.analysis.estimates import (
    ESTIMANDS,
    paired_estimates,
    run_estimand_values,
    run_estimates_for,
)
from verdi_agents.analysis.metrics import (
    attempted_vs_executed_violations,
    false_sanction_rate,
    participation,
    sustained_recovery,
    time_under_deprivation,
    value_drift_summary,
    welfare_and_inequality,
)
from verdi_agents.analysis.report import (
    G5_ESTIMANDS,
    G5_LIMITATIONS,
    RunReport,
    RunReportError,
    build_g5_report,
    write_g5_report,
)
from verdi_agents.analysis.scenario import (
    LoadedScenario,
    ScenarioLoader,
    ScenarioLoadError,
    ScenarioMeta,
    build_t0_state,
    canonical_tape_hash,
)
from verdi_agents.analysis.statistics import (
    PairedResult,
    bootstrap_ci,
    null_control_passes,
    paired_effect,
)

__all__ = [
    "ESTIMANDS",
    "G5_ESTIMANDS",
    "G5_LIMITATIONS",
    "PairedResult",
    "RunReport",
    "RunReportError",
    "LoadedScenario",
    "ScenarioLoadError",
    "ScenarioLoader",
    "ScenarioMeta",
    "build_t0_state",
    "canonical_tape_hash",
    "attempted_vs_executed_violations",
    "bootstrap_ci",
    "build_g5_report",
    "null_control_passes",
    "paired_effect",
    "paired_estimates",
    "run_estimand_values",
    "run_estimates_for",
    "write_g5_report",
    "false_sanction_rate",
    "participation",
    "sustained_recovery",
    "time_under_deprivation",
    "value_drift_summary",
    "welfare_and_inequality",
]
