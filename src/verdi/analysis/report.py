"""Run report assembly for Verdi experiments (P1-E).

A :class:`RunReport` is a plain, JSON-serializable record of one run:
manifest, lifecycle ticks, computed metrics, event count, final state
hash, and data-availability counters. Rules:

* ``limitations`` is REQUIRED and non-empty — free text stating what the
  run cannot support (demo scale, single-seed, descriptive-only, ...).
* Missing data appears as ``null`` / absent keys, never as zero.
* No findings are invented: only values computed by
  :mod:`verdi.analysis.metrics` or explicitly supplied by the caller
  appear in ``metrics``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = ["RunReport", "RunReportError"]


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
        :mod:`verdi.analysis.metrics` (or explicitly null for
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
