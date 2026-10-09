"""Frozen regime configuration for P2 controlled recovery experiments (WS-2).

A *regime* fully describes one arm of the experiment: the adversary
population size, the scheduler identity and the reflection/memory/detection
toggles that distinguish the G0..G4 comparator arms (PREREGISTRATION.md
"Comparators and controls").

The schema is frozen: :class:`~verdi.schemas.ExperimentManifest` (schema
version 1) declares ``extra="forbid"`` and has **no** ``metadata`` field,
so a :class:`RegimeConfig` can never be a top-level manifest field without
a schema change. The contract therefore carries regime data in a *metadata*
mapping that the run/report layer attaches alongside the manifest;
:func:`manifest_metadata` produces exactly that mapping (schema-free,
round-trips through :meth:`RegimeConfig.model_validate`).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

REGIME_LABELS = ("G0", "G1", "G2", "G3", "G4")


class RegimeConfig(BaseModel):
    """Immutable description of one experiment arm.

    ``regime``:
        Arm label (e.g. "G0".."G4").
    ``adversary_count``:
        Number of adversarial agents injected into the arm (0, 1 or 3).
    ``scheduler_id``:
        Identity of the deterministic scheduler (must match the
        seeded-scheduler seed alias recorded in the manifest).
    ``reflection`` / ``memory`` / ``detection``:
        Toggle flags for the three treatment capabilities under study.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    regime: str
    adversary_count: Literal[0, 1, 3]
    scheduler_id: str
    reflection: bool
    memory: bool
    detection: bool


def manifest_metadata(config: RegimeConfig) -> dict[str, Any]:
    """Return the schema-free metadata mapping that carries ``config``.

    The frozen :class:`~verdi.schemas.ExperimentManifest` forbids extra
    top-level fields, so the regime rides along in a ``metadata`` mapping
    produced here (and consumed by the integrator-owned run/report layer).
    The mapping round-trips: ``RegimeConfig.model_validate(m["regime"])``
    returns an equal :class:`RegimeConfig` and no schema field is added or
    modified.
    """
    return {
        "regime_label": config.regime,
        "regime": config.model_dump(mode="json"),
    }


def load_regime(path: str | Path) -> RegimeConfig:
    """Load and validate a fixture regime config from ``path``.

    Raises :class:`pydantic.ValidationError` for a malformed file (unknown
    keys, non-literal adversary count, wrong types).
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return RegimeConfig.model_validate(data)


__all__ = ["REGIME_LABELS", "RegimeConfig", "load_regime", "manifest_metadata"]
