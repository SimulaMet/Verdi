"""P0-C: researcher-facing PAIRED experiment runner.

Runs the fixed arm and the revisable arm of an institutional-recovery
experiment under IDENTICAL exogenous circumstances, driven by ONE config
file — no Python editing::

    python -m verdi.experiments.paired_run --config examples/paired_institutional_recovery.yaml

Config format (YAML)::

    experiment_id: institutional-recovery-smoke
    base_scenario: examples/institutional_recovery_constrained.yaml
    arms: [fixed, revisable]          # order defines run naming
    seeds: [42]                       # each seed -> one pair of runs
    total_ticks_override: null        # optional; default = base scenario value
    output_root: runs/paired-institutional-recovery

Guarantees and behavior:

* The base scenario is loaded ONCE; for each arm a deep copy is built with
  ONLY ``institutional_condition`` changed. After construction the two dicts
  are compared programmatically (minus that key) and any other difference
  raises — the arms MUST be byte-identical in every other respect.
* Each (arm, seed) run dir gets a FROZEN ``scenario_resolved.yaml`` so the
  run is reproducible from artifacts alone.
* Arms of a seed run SEQUENTIALLY (never concurrently beyond the campaign's
  own internal concurrency). A failed arm is recorded as status "failed" in
  the manifest and the runner CONTINUES with the next arm/seed — partial
  failure must not silently bias the comparison.
* ``<output_root>/pair_manifest.json`` is written atomically (tmp +
  os.replace) after every arm and at the end, listing per (seed, arm):
  run_dir, status (complete|failed|stalled), final_state_hash, replay_matches,
  and error when failed.
* RESUME: if the manifest exists, pairs already marked "complete" are
  skipped; failed/missing pairs re-run into the SAME run dirs.
* User errors (unknown keys, missing base scenario, bad arm names) raise
  SystemExit with a readable multi-line message — no tracebacks.

Testability: :func:`run_pair` takes a ``caller_factory(scenario, run_dir)``
so offline tests inject a scripted fake caller; the CLI path uses the real
:class:`~verdi.agents.client.ModelClient` factory (reusing live.py's
endpoint resolution). This module does NOT modify ``live.py`` or
``campaign.py``.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import sys
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from verdi.analysis.scenario import load_scenario
from verdi.society.campaign import SocietyCampaign

ALLOWED_CONFIG_KEYS = frozenset({
    "experiment_id",
    "base_scenario",
    "arms",
    "seeds",
    "total_ticks_override",
    "output_root",
})
VALID_ARMS = ("fixed", "revisable")
MANIFEST_NAME = "pair_manifest.json"
RESOLVED_SCENARIO_NAME = "scenario_resolved.yaml"


# --------------------------------------------------------------------------- #
# config loading / validation
# --------------------------------------------------------------------------- #

def _fail(message: str) -> None:
    """Raise SystemExit with a readable multi-line message (no traceback)."""
    raise SystemExit(f"paired-run config error:\n{message}")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_paired_config(path: str | Path) -> dict[str, Any]:
    """Load and validate the paired-experiment config file.

    Raises SystemExit (readable, multi-line, no traceback) on any user error.
    """
    import yaml

    cfg_path = Path(path)
    if not cfg_path.exists():
        _fail(f"paired config file not found: {cfg_path}\n"
              f"Pass --config pointing at your paired experiment YAML.")
    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        _fail(f"paired config is not valid YAML ({cfg_path}):\n{exc}")
    if not isinstance(raw, dict):
        _fail(f"paired config must be a YAML mapping: {cfg_path}")

    unknown = sorted(set(raw) - ALLOWED_CONFIG_KEYS)
    if unknown:
        _fail(
            f"unknown key(s) in paired config: {', '.join(repr(k) for k in unknown)}\n"
            f"allowed keys: {', '.join(sorted(ALLOWED_CONFIG_KEYS))}"
        )

    missing = sorted({"experiment_id", "base_scenario", "arms", "seeds"} - set(raw))
    if missing:
        _fail(f"missing required key(s): {', '.join(missing)}\n"
              f"required keys: experiment_id, base_scenario, arms, seeds")

    base = raw["base_scenario"]
    if not isinstance(base, str) or not str(base).strip():
        _fail("base_scenario must be a non-empty path string")
    if not Path(base).exists():
        _fail(f"base_scenario file not found: {base}\n"
              f"(path is resolved relative to the current working directory)")

    arms = raw["arms"]
    if (not isinstance(arms, list) or not arms
            or not all(isinstance(a, str) for a in arms)):
        _fail("arms must be a non-empty list of strings")
    bad_arms = [a for a in arms if a not in VALID_ARMS]
    if bad_arms:
        _fail(f"invalid arm(s): {', '.join(repr(a) for a in bad_arms)}\n"
              f"valid arms: {', '.join(VALID_ARMS)}")
    if len(set(arms)) != len(arms):
        _fail("arms must be unique (each arm appears once per seed)")

    seeds = raw["seeds"]
    if not isinstance(seeds, list) or not seeds:
        _fail("seeds must be a non-empty list of integers")
    norm_seeds: list[int] = []
    for s in seeds:
        if isinstance(s, bool) or not isinstance(s, int):
            _fail(f"seeds must be integers, got: {s!r}")
        norm_seeds.append(int(s))

    override = raw.get("total_ticks_override")
    if override is not None and (isinstance(override, bool) or not isinstance(override, int)
                                 or override < 1):
        _fail("total_ticks_override must be a positive integer (or null)")

    out_root = raw.get("output_root")
    if out_root is not None and (not isinstance(out_root, str) or not str(out_root).strip()):
        _fail("output_root must be a non-empty path string (or omitted)")

    return {
        "experiment_id": str(raw["experiment_id"]),
        "base_scenario": str(base),
        "arms": list(arms),
        "seeds": norm_seeds,
        "total_ticks_override": int(override) if override is not None else None,
        "output_root": str(out_root) if out_root is not None else None,
    }


# --------------------------------------------------------------------------- #
# arm scenario construction (identity assertion)
# --------------------------------------------------------------------------- #

def build_arm_scenarios(base: dict[str, Any], arms: list[str],
                        total_ticks_override: int | None = None) -> dict[str, dict[str, Any]]:
    """Deep-copy the base scenario per arm, changing ONLY institutional_condition.

    If ``total_ticks_override`` is given it is applied to EVERY arm identically
    (an exogenous parameter, not an arm-specific one). After construction the
    arm scenarios are compared pairwise (minus institutional_condition) and a
    RuntimeError is raised on ANY other difference.
    """
    built: dict[str, dict[str, Any]] = {}
    for arm in arms:
        sc = copy.deepcopy(base)
        sc["institutional_condition"] = arm
        if total_ticks_override is not None:
            sc["total_ticks"] = int(total_ticks_override)
        built[arm] = sc

    reference_arm = arms[0]
    ref_minus = {k: v for k, v in built[reference_arm].items() if k != "institutional_condition"}
    for arm in arms[1:]:
        other_minus = {k: v for k, v in built[arm].items() if k != "institutional_condition"}
        if ref_minus != other_minus:
            diff_keys = sorted(
                {k for k in set(ref_minus) | set(other_minus)
                 if ref_minus.get(k) != other_minus.get(k)}
            )
            raise RuntimeError(
                "internal error: arm scenarios diverged outside "
                f"institutional_condition (keys: {', '.join(diff_keys)}); "
                "refusing to run a biased comparison"
            )
    return built


# --------------------------------------------------------------------------- #
# atomic manifest helpers
# --------------------------------------------------------------------------- #

def _atomic_write_json(path: Path, obj: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _write_frozen_scenario(run_dir: Path, scenario: dict[str, Any]) -> None:
    """Write the exact effective scenario used, so the run reproduces alone."""
    import yaml

    path = run_dir / RESOLVED_SCENARIO_NAME
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(scenario, sort_keys=True, allow_unicode=True),
                   encoding="utf-8")
    os.replace(tmp, path)


def _load_manifest(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _manifest_entry(manifest: dict[str, Any], seed: int, arm: str) -> dict[str, Any] | None:
    for entry in manifest.get("runs", []):
        if entry.get("seed") == seed and entry.get("arm") == arm:
            return entry
    return None


# --------------------------------------------------------------------------- #
# result object
# --------------------------------------------------------------------------- #

@dataclass
class PairRunResult:
    """Outcome of one :func:`run_pair` invocation.

    ``exit_code`` is 0 only when every (seed, arm) pair completed; 1 when at
    least one pair failed/stalled. Partial failure is REPORTED, never raised.
    """

    experiment_id: str
    output_root: Path
    manifest_path: Path
    entries: list[dict[str, Any]] = field(default_factory=list)
    exit_code: int = 0

    @property
    def complete(self) -> bool:
        return self.exit_code == 0


# --------------------------------------------------------------------------- #
# single-arm execution
# --------------------------------------------------------------------------- #

async def _run_one_arm(*, arm: str, seed: int, scenario: dict[str, Any],
                       run_dir: Path, caller_factory: Callable[[dict[str, Any], Path], Any],
                       concurrency: int) -> dict[str, Any]:
    """Drive one SocietyCampaign for (arm, seed); return its manifest entry."""
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_frozen_scenario(run_dir, scenario)
    caller = caller_factory(scenario, run_dir)
    try:
        if hasattr(caller, "__aenter__"):
            caller = await caller.__aenter__()
        campaign = SocietyCampaign(
            scenario=scenario,
            run_dir=run_dir,
            caller=caller,
            run_seed=seed,
            concurrency=max(1, int(concurrency)),
        )
        result = await campaign.run()
    finally:
        # Teardown must not mask the run outcome.
        with suppress(Exception):
            if hasattr(caller, "__aexit__"):
                await caller.__aexit__(None, None, None)
    return {
        "status": "complete",
        "final_state_hash": result.final_state_hash,
        "replay_matches": bool(result.replay_matches),
        "replay_log_identical": bool(result.replay_log_identical),
        "model_calls": len(result.model_calls),
        "wall_seconds": round(result.wall_seconds, 2),
    }


# --------------------------------------------------------------------------- #
# the public API
# --------------------------------------------------------------------------- #

def run_pair(config: dict[str, Any],
             caller_factory: Callable[[dict[str, Any], Path], Any],
             *,
             seeds: list[int] | None = None,
             output_root: str | Path | None = None,
             concurrency: int = 4) -> PairRunResult:
    """Run every (seed, arm) pair sequentially; write the pair manifest.

    * ``config``: validated dict from :func:`load_paired_config`.
    * ``caller_factory(scenario, run_dir) -> ModelCaller``: injected so tests
      can use scripted fakes; the CLI passes the real ModelClient factory.
    * Pairs already marked "complete" in an existing manifest are SKIPPED
      (resume); failed/missing pairs re-run into the same run dirs.
    * A failing arm records status "failed" (+ error) and the loop continues.
    Never raises for a failed arm; returns a :class:`PairRunResult` whose
    ``exit_code`` is nonzero when any pair did not complete.
    """
    root = Path(output_root if output_root is not None else config["output_root"])
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / MANIFEST_NAME
    manifest = _load_manifest(manifest_path)
    if manifest is None:
        manifest = {
            "experiment_id": config["experiment_id"],
            "created_at_utc": _utc_now(),
            "base_scenario": config["base_scenario"],
            "arms": list(config["arms"]),
            "runs": [],
        }
    manifest.setdefault("runs", [])

    base = load_scenario(config["base_scenario"])
    arm_scenarios = build_arm_scenarios(
        base, config["arms"], config["total_ticks_override"]
    )
    run_seeds = list(seeds) if seeds is not None else list(config["seeds"])

    entries: dict[tuple[int, str], dict[str, Any]] = {}
    for entry in manifest["runs"]:
        try:
            entries[(int(entry["seed"]), str(entry["arm"]))] = entry
        except (KeyError, TypeError, ValueError):
            continue

    async def _drive() -> None:
        for seed in run_seeds:
            for arm in config["arms"]:
                run_dir = root / f"{arm}-seed{seed}"
                prior = entries.get((seed, arm))
                if prior is not None and prior.get("status") == "complete":
                    print(f"[skip] {arm}-seed{seed}: already complete (resume)")
                    continue
                print(f"[run ] {arm}-seed{seed}: {run_dir}", flush=True)
                try:
                    outcome = await _run_one_arm(
                        arm=arm, seed=seed, scenario=arm_scenarios[arm],
                        run_dir=run_dir, caller_factory=caller_factory,
                        concurrency=concurrency,
                    )
                except BaseException as exc:  # noqa: BLE001 - record, don't abort the pair loop
                    outcome = {
                        "status": "failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                    print(f"[fail] {arm}-seed{seed}: {outcome['error']}", file=sys.stderr,
                          flush=True)
                entry = {
                    "seed": seed,
                    "arm": arm,
                    "run_dir": str(run_dir),
                    "updated_at_utc": _utc_now(),
                    **outcome,
                }
                entries[(seed, arm)] = entry
                manifest["runs"] = [entries[k] for k in sorted(entries)]
                manifest["updated_at_utc"] = _utc_now()
                _atomic_write_json(manifest_path, manifest)
                print(f"[done] {arm}-seed{seed}: status={entry['status']} "
                      f"replay_matches={entry.get('replay_matches')}", flush=True)

    asyncio.run(_drive())

    ordered = [entries[k] for k in sorted(entries)]
    exit_code = 0
    for entry in ordered:
        if entry.get("status") != "complete":
            exit_code = 1
            break
    return PairRunResult(
        experiment_id=config["experiment_id"],
        output_root=root,
        manifest_path=manifest_path,
        entries=ordered,
        exit_code=exit_code,
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _real_caller_factory():
    """Build the live ModelClient factory (endpoint resolved per arm scenario)."""
    from verdi.agents.client import ModelClient
    from verdi.society.live import resolve_endpoint_config

    def factory(scenario: dict[str, Any], run_dir: Path) -> ModelClient:
        # One pooled client per arm run; closed in _run_one_arm's finally.
        return ModelClient(resolve_endpoint_config(scenario))

    return factory


def _print_summary(result: PairRunResult) -> None:
    print()
    print(f"paired experiment: {result.experiment_id}")
    print(f"{'arm':<10} {'seed':>6} {'status':<10} {'replay':>7}  run_dir")
    for e in result.entries:
        print(f"{e['arm']:<10} {e['seed']:>6} {e.get('status', '?'):<10} "
              f"{str(e.get('replay_matches')):>7}  {e.get('run_dir')}")
    if not result.complete:
        print("\nWARNING: one or more pairs did not complete — the comparison "
              "is PARTIAL and must not be interpreted as unbiased.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a paired fixed-vs-revisable institutional experiment "
                    "from ONE config file (no Python editing).")
    parser.add_argument("--config", required=True, type=Path,
                        help="paired experiment config YAML")
    parser.add_argument("--seeds", default=None,
                        help="comma-separated seed override (default: config seeds)")
    parser.add_argument("--output-root", default=None, type=Path,
                        help="output root override (default: config output_root)")
    parser.add_argument("--concurrency", type=int, default=None,
                        help="per-campaign model-call concurrency "
                             "(default: VERDI_MAX_CONCURRENCY or 4)")
    args = parser.parse_args(argv)

    config = load_paired_config(args.config)

    seeds = None
    if args.seeds is not None:
        try:
            seeds = [int(s.strip()) for s in args.seeds.split(",") if s.strip()]
            if not seeds:
                raise ValueError("empty")
        except ValueError:
            _fail(f"--seeds must be comma-separated integers, got: {args.seeds!r}")

    # Endpoint resolution happens up front so a misconfigured environment fails
    # BEFORE any arm is launched (and resume-skip decisions stay free).
    caller_factory = _real_caller_factory()
    concurrency = args.concurrency if args.concurrency is not None else 4

    result = run_pair(config, caller_factory, seeds=seeds,
                      output_root=args.output_root, concurrency=concurrency)
    _print_summary(result)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
