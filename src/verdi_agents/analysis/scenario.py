"""Deterministic scenario + task-tape loading into frozen v1 schemas.

The scenario JSON describes the world at tick 0 (agents, resources,
versions, seed) and the pilot lifecycle map. The task tape is a separate,
content-addressed file: its ``tape_hash`` is the sha256 of the canonical
(sorted-key, compact) JSON of the tape *excluding* the hash field itself,
and is what ``ExperimentManifest.task_tape_hash`` must carry.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from verdi_agents.schemas import Task, WorldState

__all__ = [
    "LoadedScenario",
    "ScenarioLoadError",
    "ScenarioLoader",
    "ScenarioMeta",
    "build_t0_state",
    "canonical_tape_hash",
    "load_scenario",
]


def load_scenario(path: str | Path) -> dict[str, Any]:
    """Load a scenario definition from a ``.json`` or ``.yml``/``.yaml`` file.

    Tranche 12l: YAML is a superset of JSON, so a single authoring format
    works for humans (comments, no braces) while JSON stays fully supported
    for existing scenario files. ``.json`` files are parsed with the stdlib
    JSON parser (no behavior change); ``.yml``/``.yaml`` use PyYAML.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")
    if suffix in (".yml", ".yaml"):
        try:
            import yaml  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - env-specific
            raise ScenarioLoadError(
                "PyYAML is required to load .yml/.yaml scenarios; "
                "install it or save the scenario as .json"
            ) from exc
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ScenarioLoadError(f"scenario file must contain a mapping/object: {path}")
    return data


class ScenarioLoadError(RuntimeError):
    """Raised when a scenario/tape file is malformed or fails verification."""


class ScenarioMeta(BaseModel):
    """Scenario-level metadata that does not belong to WorldState or Task."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario_id: str
    description: str
    regime: str
    law_version: str
    constitution_version: str
    total_ticks: int = Field(ge=1)
    assemblies: list[int] = Field(default_factory=list)
    value_axes: list[str] = Field(default_factory=list)
    lifecycle: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # Optional DETERMINISTIC world shocks: [{tick, resource, delta}] applied at
    # the start of that tick before any agent acts. An external/deterministic
    # world event (like sanction expiry), NOT an LLM mutation — used to induce
    # value-relevant pressure (e.g. a food-scarcity crisis) so we can test
    # whether declared/observed values drift in response. Empty by default.
    shocks: list[dict[str, Any]] = Field(default_factory=list)


def canonical_tape_hash(payload: dict[str, Any]) -> str:
    """sha256 over the canonical JSON (sorted keys, compact) of the tape.

    The ``tape_hash`` field itself is excluded before hashing, so the
    hash is stable regardless of whether the stored copy contains it.
    """
    body = {k: v for k, v in payload.items() if k != "tape_hash"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_t0_state(scenario: dict[str, Any]) -> WorldState:
    """Build the frozen tick-0 WorldState from a scenario dict.

    Tranche 12k: the scenario is the single source of truth for the world's
    rules. Optional ``constitution`` and ``law`` blocks are normalized into
    the returned state (with defaults identical to the historical hardcoded
    values), so a scenario file fully defines the simulation it drives.
    """
    agent_ids = [a["agent_id"] for a in scenario["agents"]]
    if not agent_ids:
        raise ScenarioLoadError("scenario has no agents")
    if len(agent_ids) != len(set(agent_ids)):
        raise ScenarioLoadError("scenario contains duplicate agent ids")
    _check_values(scenario, set(agent_ids))
    _normalize_governance(scenario)
    # Tranche 12m: an agent entry may carry a `sanction: {duration_ticks: N}`
    # block (a scenario-declared t0 sanction with auto-expiry). That is campaign
    # metadata, not frozen world state, so strip it before building WorldState.
    # The campaign reads it from the scenario dict directly.
    sanitized_agents = [
        {k: v for k, v in a.items() if k != "sanction"} for a in scenario["agents"]
    ]
    state = WorldState(
        schema_version=1,
        world_id=scenario["world_id"],
        tick=0,
        law_version=scenario["law_version"],
        constitution_version=scenario["constitution_version"],
        agents={a["agent_id"]: a for a in sanitized_agents},
        world_resources=dict(scenario.get("world_resources", {})),
        event_cursor=0,
        state_hash=None,
        seed=scenario["run_seed"],
    )
    return state


def _finite_nonneg(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


def _check_values(scenario: dict[str, Any], agent_ids: set[str]) -> None:
    """Load-time checks for values that would otherwise load fine and then
    crash (or silently misbehave) many model calls into a run."""
    for a in scenario["agents"]:
        for res, val in (a.get("resources") or {}).items():
            if not _finite_nonneg(val):
                raise ScenarioLoadError(
                    f"agent {a['agent_id']!r} resource {res!r} must be a finite number >= 0, got {val!r}")
    for res, val in (scenario.get("world_resources") or {}).items():
        if not _finite_nonneg(val):
            raise ScenarioLoadError(f"world_resources.{res} must be a finite number >= 0, got {val!r}")
    for i, shock in enumerate(scenario.get("shocks") or []):
        if not isinstance(shock, dict):
            raise ScenarioLoadError(f"shocks[{i}] must be a mapping")
        tick = shock.get("tick")
        if isinstance(tick, bool) or not isinstance(tick, int) or tick < 0:
            raise ScenarioLoadError(f"shocks[{i}].tick must be an integer >= 0, got {tick!r}")
        delta = shock.get("delta")
        if isinstance(delta, bool) or not isinstance(delta, (int, float)) or not math.isfinite(delta):
            raise ScenarioLoadError(f"shocks[{i}].delta must be a finite number, got {delta!r}")
    tasks = scenario.get("tasks")
    deliveries = tasks.get("deliveries") if isinstance(tasks, dict) else tasks
    for i, entry in enumerate(deliveries or []):
        target = entry.get("target_pool", "world") if isinstance(entry, dict) else "world"
        if target != "world" and target not in agent_ids:
            # Otherwise this only raises at the due tick, deep into a live run.
            raise ScenarioLoadError(
                f"tasks.deliveries[{i}].target_pool {target!r} is neither 'world' nor an agent id")


def _normalize_governance(scenario: dict[str, Any]) -> None:
    """Fill in the scenario's ``constitution`` and ``law`` blocks in place.

    Defaults reproduce the historical hardcoded values exactly, so existing
    scenario files (without these blocks) behave byte-identically. The
    normalized blocks are what :class:`~verdi_agents.society.campaign.SocietyCampaign`
    builds its GovernanceEngine from — one file, one definition.
    """
    constitution = scenario.get("constitution") or {}
    scenario["constitution"] = {
        "rules": constitution.get("rules", {"quorum_rule": "simple_majority_of_eligible"}),
        "quorum": constitution.get("quorum", 0.5),
        "amendment_threshold": constitution.get("amendment_threshold", 0.67),
        "sanction_policy": constitution.get(
            "sanction_policy", {"max_duration_ticks": 10}),
        "appeal_policy": constitution.get("appeal_policy", {"deadline_ticks": 3}),
    }
    law = scenario.get("law") or {}
    scenario["law"] = {
        "invariants": law.get(
            "invariants",
            ["no_overriding_universal_invariants", "no_double_spend", "no_negative_balances"]),
    }
    # Version identity strings are part of WorldState and drive the checksums.
    # Give them standard defaults so a minimal scenario need not spell them out.
    scenario.setdefault("law_version", "universal-law")
    scenario.setdefault("constitution_version", "base-constitution")
    _normalize_economy(scenario)


def _normalize_economy(scenario: dict[str, Any]) -> None:
    """Validate the optional versioned ``economy`` and ``tasks`` blocks.

    Both are OPT-IN: absent (or empty) they stay absent and every legacy run
    keeps today's semantics exactly (unlimited produce, no demand, no costs,
    no objectives). When present they are parsed through the pure economy
    module up front so a malformed block is a config error at load time,
    never a silent degenerate run. The campaign reads the raw blocks back
    from the scenario dict — this function only validates, it does not add
    keys, so existing scenarios' normalized dicts are unchanged.
    """
    from verdi_agents.world.economy import parse_economy_spec, parse_task_objectives

    if "economy" in scenario and scenario["economy"]:
        parse_economy_spec(scenario["economy"])  # raises on malformed
    if "tasks" in scenario and scenario["tasks"]:
        parse_task_objectives(scenario["tasks"])  # raises on malformed


@dataclass(frozen=True)
class LoadedScenario:
    """A verified scenario + tape pair, ready to drive a deterministic run."""

    meta: ScenarioMeta
    t0_state: WorldState
    tape: list[list[Task]]  # tape[tick] = ordered task batch for that tick
    tape_hash: str
    tape_file: Path


class ScenarioLoader:
    """Loads scenario + tape files into frozen schemas and verifies linkage.

    Deterministic: the same files always produce the same objects and the
    same ``tape_hash``. No network, no LLM.
    """

    def __init__(self, scenario_path: str | Path, tape_path: str | Path) -> None:
        self.scenario_path = Path(scenario_path)
        self.tape_path = Path(tape_path)

    def load(self, manifest: Any | None = None) -> LoadedScenario:
        try:
            scenario = json.loads(self.scenario_path.read_text(encoding="utf-8"))
            tape_file = json.loads(self.tape_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ScenarioLoadError(f"cannot read scenario/tape files: {exc}") from exc

        self._check_shape(scenario, "scenario")
        self._check_shape(tape_file, "tape")

        # Cross-file linkage.
        if scenario["world_id"] != tape_file["world_id"]:
            raise ScenarioLoadError(
                f"world_id mismatch: scenario={scenario['world_id']!r} "
                f"tape={tape_file['world_id']!r}"
            )
        if scenario["run_seed"] != tape_file["run_seed"]:
            raise ScenarioLoadError(
                f"run_seed mismatch: scenario={scenario['run_seed']} "
                f"tape={tape_file['run_seed']}"
            )
        if scenario["total_ticks"] != tape_file["total_ticks"]:
            raise ScenarioLoadError("total_ticks mismatch between scenario and tape")

        entries = tape_file["ticks"]
        if len(entries) != scenario["total_ticks"]:
            raise ScenarioLoadError(
                f"tape has {len(entries)} tick entries, expected {scenario['total_ticks']}"
            )
        tape: list[list[Task]] = []
        for i, entry in enumerate(entries):
            if entry["tick"] != i:
                raise ScenarioLoadError(f"tape ticks must be 0..N-1 in order; got {entry['tick']} at {i}")
            tape.append([Task.model_validate(t) for t in entry["tasks"]])

        expected = canonical_tape_hash(tape_file)
        stored = tape_file.get("tape_hash")
        if stored is None:
            raise ScenarioLoadError("tape is missing its tape_hash field")
        if stored != expected:
            raise ScenarioLoadError(
                f"tape_hash mismatch: stored={stored} recomputed={expected} (tape was tampered with)"
            )

        # Optional manifest linkage: task_tape_hash and world_id must match.
        if manifest is not None:
            if getattr(manifest, "task_tape_hash", None) and manifest.task_tape_hash != expected:
                raise ScenarioLoadError(
                    f"manifest.task_tape_hash={manifest.task_tape_hash} != recomputed {expected}"
                )
            if manifest.world_id != scenario["world_id"]:
                raise ScenarioLoadError(
                    f"manifest.world_id={manifest.world_id!r} != scenario {scenario['world_id']!r}"
                )
            if manifest.run_seed != scenario["run_seed"]:
                raise ScenarioLoadError(
                    f"manifest.run_seed={manifest.run_seed} != scenario {scenario['run_seed']}"
                )

        meta = ScenarioMeta(
            scenario_id=scenario["scenario_id"],
            description=scenario["description"],
            regime=scenario["regime"],
            law_version=scenario["law_version"],
            constitution_version=scenario["constitution_version"],
            total_ticks=scenario["total_ticks"],
            assemblies=list(scenario["assemblies"]),
            value_axes=list(scenario["value_axes"]),
            lifecycle=dict(scenario["lifecycle"]),
        )
        return LoadedScenario(
            meta=meta,
            t0_state=build_t0_state(scenario),
            tape=tape,
            tape_hash=expected,
            tape_file=self.tape_path,
        )

    @staticmethod
    def _check_shape(doc: Any, label: str) -> None:
        if not isinstance(doc, dict):
            raise ScenarioLoadError(f"{label} must be a JSON object")
