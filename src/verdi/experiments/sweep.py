"""Sweeps: rerun one scenario many times, changing only what you list.

Researcher-facing. One small YAML file (or a few CLI flags) describes the
sweep; the runner shows the plan, asks before spending model calls, runs the
grid (several runs at a time), and ends with a spreadsheet and a comparison
page::

    python -m verdi.sweep examples/sweeps/model_comparison.yaml

Sweep file::

    name: model_comparison
    scenario: examples/full.yaml       # the baseline; never edited
    vary:                              # every combination is run (grid)
      model.name: [qwen3.8-27b, deepseek-v4]
      constitution.quorum: [0.5, 0.75]
    seeds: [42, 43, 44]                # or a count, e.g. `seeds: 3`
    parallel_runs: 2                   # simulations at the same time
    agent_concurrency: 4               # model calls at the same time, per simulation
    ticks: 10                          # optional: shorten every run (quick pilot)
    output: runs/sweeps/model_comparison

``vary`` values are a list (labels are made from the values) or a mapping of
``label: value``. Dotted keys reach nested fields. A mapping value applied to
a mapping field is merged, so ``model: {deepseek: {name: deepseek-v4,
base_url: ..., api_key_env: DEEPSEEK_API_KEY}}`` switches endpoints too.

Guarantees:

* Every override path must already exist in the baseline scenario (typos fail
  with a "did you mean" hint instead of being silently ignored).
* Every resolved scenario is validated BEFORE any model call.
* Each run directory gets the exact ``scenario.yaml`` it ran (frozen input).
* Each run is its own process: one crash or stall never takes down the rest.
* ``sweep_manifest.json`` is rewritten atomically after every run; rerunning
  the same command resumes, skipping runs already complete.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import csv
import difflib
import hashlib
import html
import json
import os
import re
import statistics
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import Any

ALLOWED_KEYS = frozenset({
    "name", "scenario", "vary", "seeds", "parallel_runs", "agent_concurrency",
    "ticks", "output", "description",
})
MANIFEST_NAME = "sweep_manifest.json"
SCENARIO_NAME = "scenario.yaml"
LOG_NAME = "run.log"
_MODEL_OPTIONAL = frozenset({"api_key_env", "timeout_seconds", "max_retries"})

# Headline metrics shown on the comparison page (column -> plain label).
HEADLINE = {
    "completion_rate.overall": "Task completion rate",
    "resource_sharing.final_inequality.gini": "Final inequality (Gini)",
    "exclusion_episodes.totals.sanctions_imposed": "Sanctions imposed",
    "exclusion_episodes.totals.appeals_filed": "Appeals filed",
    "exclusion_episodes.totals.restorations": "Rights restored",
    "institutional_mechanism.turnout.mean_turnout": "Mean assembly turnout",
    "institutional_mechanism.votes.for": "Votes for amendments",
    "institutional_mechanism.votes.against": "Votes against amendments",
    "accepted_mutating_actions": "Accepted actions",
    "rejected_actions": "Rejected actions",
    "model_calls": "Model calls",
    "prompt_tokens": "Prompt tokens",
    "completion_tokens": "Completion tokens",
    "wall_seconds": "Wall time (s)",
}


class SweepError(SystemExit):
    """User error: readable message, no traceback."""

    def __init__(self, message: str) -> None:
        super().__init__(f"sweep error: {message}")


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #

@dataclass
class Factor:
    path: str
    labels: list[str]
    values: list[Any]


@dataclass
class SweepConfig:
    name: str
    scenario_path: Path
    base: dict[str, Any]
    factors: list[Factor]
    seeds: list[int]
    parallel_runs: int = 1
    agent_concurrency: int = 4
    ticks: int | None = None
    output: Path = Path("runs/sweeps/sweep")


@dataclass
class RunSpec:
    run_id: str
    condition: str
    labels: dict[str, str]
    seed: int
    scenario: dict[str, Any]
    run_dir: Path


def _label(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(text)).strip("-")[:40]
    # Labels become path components: "." / ".." would escape the sweep folder.
    if not text.strip("."):
        return "value"
    return text


def _factor(path: str, raw: Any) -> Factor:
    if isinstance(raw, dict):
        if not raw:
            raise SweepError(f"vary.{path}: give at least one value")
        labels = [_label(k) for k in raw]
        values = list(raw.values())
    elif isinstance(raw, list):
        if not raw:
            raise SweepError(f"vary.{path}: give at least one value")
        if any(isinstance(v, (dict, list)) for v in raw):
            raise SweepError(
                f"vary.{path}: complex values need names, e.g.\n"
                f"  {path}:\n    option_a: {{...}}\n    option_b: {{...}}")
        labels = [_label(v) for v in raw]
        values = list(raw)
    else:
        labels, values = [_label(raw)], [raw]
    if len(set(labels)) != len(labels):
        raise SweepError(f"vary.{path}: two values get the same label {labels}; "
                         "use the `label: value` form")
    return Factor(path=path, labels=labels, values=values)


def _seeds(raw: Any, base_seed: int) -> list[int]:
    if raw is None:
        return [base_seed]
    if isinstance(raw, int) and not isinstance(raw, bool):
        if raw < 1:
            raise SweepError("seeds: a count must be at least 1")
        return [base_seed + i for i in range(raw)]
    if isinstance(raw, list) and raw and all(
            isinstance(s, int) and not isinstance(s, bool) for s in raw):
        if len(set(raw)) != len(raw):
            raise SweepError("seeds: values must be unique")
        return list(raw)
    raise SweepError("seeds: give a list of whole numbers (e.g. [42, 43]) or a count (e.g. 5)")


def _positive_int(raw: Any, key: str, default: int | None) -> int | None:
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise SweepError(f"{key}: must be a whole number >= 1")
    return raw


def build_config(raw: dict[str, Any]) -> SweepConfig:
    """Validate a sweep mapping (from a file or the CLI) into a SweepConfig."""
    from verdi.analysis.scenario import build_t0_state, load_scenario

    unknown = sorted(set(raw) - ALLOWED_KEYS)
    if unknown:
        hints = [f"{k!r} (did you mean {m[0]!r}?)" if (m := difflib.get_close_matches(
            k, ALLOWED_KEYS, n=1)) else repr(k) for k in unknown]
        raise SweepError(f"unknown key(s): {', '.join(hints)}\n"
                         f"allowed: {', '.join(sorted(ALLOWED_KEYS))}")
    if not raw.get("scenario"):
        raise SweepError("`scenario:` is required (the baseline scenario file)")
    scenario_path = Path(raw["scenario"])
    if not scenario_path.is_file():
        raise SweepError(f"scenario file not found: {scenario_path}")
    base = load_scenario(scenario_path)
    try:
        # Fill engine defaults (constitution, law, ...) so they can be varied
        # and are written out explicitly in every frozen run scenario.
        build_t0_state(base)
    except Exception as exc:  # noqa: BLE001 - surface schema errors readably
        raise SweepError(f"baseline scenario {scenario_path} is invalid:\n{exc}") from None
    name = str(raw.get("name") or scenario_path.stem)
    vary = raw.get("vary") or {}
    if not isinstance(vary, dict):
        raise SweepError("`vary:` must map field names to values")
    factors = [_factor(str(p), v) for p, v in vary.items()]
    for f in factors:
        _check_path(base, f.path)
    return SweepConfig(
        name=name,
        scenario_path=scenario_path,
        base=base,
        factors=factors,
        seeds=_seeds(raw.get("seeds"), int(base.get("run_seed", 0))),
        parallel_runs=_positive_int(raw.get("parallel_runs"), "parallel_runs", 1),
        agent_concurrency=_positive_int(raw.get("agent_concurrency"), "agent_concurrency", 4),
        ticks=_positive_int(raw.get("ticks"), "ticks", None),
        output=Path(raw.get("output") or f"runs/sweeps/{_label(name)}"),
    )


def load_sweep(path: str | Path) -> SweepConfig:
    import yaml

    path = Path(path)
    if not path.is_file():
        raise SweepError(f"sweep file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SweepError(f"{path} is not valid YAML:\n{exc}") from None
    if not isinstance(raw, dict):
        raise SweepError(f"{path} must be a YAML mapping")
    return build_config(raw)


# --------------------------------------------------------------------------- #
# overrides
# --------------------------------------------------------------------------- #

def _check_path(base: dict[str, Any], path: str) -> None:
    node: Any = base
    parts = path.split(".")
    for i, part in enumerate(parts):
        here = ".".join(parts[: i + 1])
        if isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
            continue
        if not isinstance(node, dict):
            raise SweepError(f"vary.{path}: {'.'.join(parts[:i]) or 'scenario'} "
                             "is not a mapping, cannot go deeper")
        if part not in node:
            if parts[0] == "model" and i == 1 and part in _MODEL_OPTIONAL:
                return
            close = difflib.get_close_matches(part, [str(k) for k in node], n=1)
            hint = f" Did you mean {'.'.join(parts[:i] + [close[0]])!r}?" if close else ""
            raise SweepError(f"vary.{path}: {here!r} is not in the baseline scenario.{hint}\n"
                             "Add it to the scenario first so the baseline is explicit.")
        node = node[part]


def _deep_merge(dst: dict[str, Any], src: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(dst)
    for k, v in src.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(
            out.get(k), dict) else copy.deepcopy(v)
    return out


def apply_override(scenario: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    node: Any = scenario
    for part in parts[:-1]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    leaf = parts[-1]
    if isinstance(node, list):
        node[int(leaf)] = copy.deepcopy(value)
    elif isinstance(value, dict) and isinstance(node.get(leaf), dict):
        node[leaf] = _deep_merge(node[leaf], value)
    else:
        node[leaf] = copy.deepcopy(value)


def _short(path: str) -> str:
    """Folder-name prefix for a factor: last segment, or its parent for `.name`."""
    parts = [p for p in path.split(".") if not p.isdigit()]
    return parts[-2] if len(parts) > 1 and parts[-1] == "name" else parts[-1]


def expand(cfg: SweepConfig) -> list[RunSpec]:
    """Every (condition, seed) combination as a resolved, frozen scenario."""
    combos = list(product(*[list(zip(f.labels, f.values, strict=True)) for f in cfg.factors])) or [()]
    runs: list[RunSpec] = []
    for combo in combos:
        labels = {f.path: lab for f, (lab, _) in zip(cfg.factors, combo, strict=True)}
        short = [f"{_short(f.path)}-{lab}" if len(cfg.factors) > 1 else lab
                 for f, (lab, _) in zip(cfg.factors, combo, strict=True)]
        condition = "__".join(short) or "baseline"
        for seed in cfg.seeds:
            sc = copy.deepcopy(cfg.base)
            for f, (_, value) in zip(cfg.factors, combo, strict=True):
                apply_override(sc, f.path, value)
            sc["run_seed"] = seed
            if cfg.ticks is not None:
                sc["total_ticks"] = cfg.ticks
            runs.append(RunSpec(
                run_id=f"{condition}/seed-{seed}", condition=condition, labels=labels,
                seed=seed, scenario=sc, run_dir=cfg.output / condition / f"seed-{seed}"))
    return runs


def validate_runs(runs: list[RunSpec]) -> list[str]:
    """Check every resolved scenario offline. Returns warnings; raises on errors."""
    from verdi.analysis.scenario import build_t0_state
    from verdi.config import API_KEY_ENV, model_endpoint_from_env
    from verdi.experiments.g5_judge_live import _load_dotenv_into_environ

    _load_dotenv_into_environ()
    keyless: dict[str, set[str]] = {}
    for run in runs:
        try:
            build_t0_state(copy.deepcopy(run.scenario))
            block = run.scenario.get("model")
            endpoint = model_endpoint_from_env(block)
        except SweepError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface any schema error readably
            raise SweepError(f"{run.run_id}: scenario is invalid after overrides:\n{exc}") from None
        if endpoint is None:
            raise SweepError(f"{run.run_id}: no model endpoint (add `model:` with "
                             "base_url and name to the scenario)")
        if endpoint.api_key == "EMPTY":
            key_env = (block or {}).get("api_key_env") or API_KEY_ENV
            keyless.setdefault(key_env, set()).add(run.condition)
    return [f"{env} is not set (used by {len(conds)} condition(s)); "
            "fine only for a keyless local server" for env, conds in sorted(keyless.items())]


def preflight(runs: list[RunSpec]) -> list[str]:
    """One tiny model call per distinct endpoint+model. Returns problems found.

    Catches wrong model names, unauthorized keys (401/403) and unreachable
    servers before any simulation spends calls.
    """
    import asyncio
    import dataclasses

    from verdi.agents.client import ModelClient
    from verdi.config import model_endpoint_from_env
    from verdi.society.live import smoke_call

    seen: dict[tuple[str, str, str], list[str]] = {}
    configs = {}
    for run in runs:
        ep = model_endpoint_from_env(run.scenario.get("model"))
        key = (ep.base_url, ep.model, ep.api_key)
        seen.setdefault(key, []).append(run.condition)
        configs[key] = dataclasses.replace(ep, max_retries=0, timeout_seconds=90.0)

    async def check(cfg) -> str | None:
        try:
            async with ModelClient(cfg) as client:
                result = await smoke_call(client)
        except Exception as exc:  # noqa: BLE001 - report any failure readably
            return str(exc).split("\n")[0][:200]
        return None if result["ok"] else "endpoint answered but not with valid JSON"

    problems = []
    for key, cfg in configs.items():
        err = asyncio.run(check(cfg))
        if err:
            conditions = ", ".join(sorted(set(seen[key])))
            problems.append(f"model {cfg.model!r} at {cfg.base_url} ({conditions}): {err}")
    return problems


# --------------------------------------------------------------------------- #
# plan
# --------------------------------------------------------------------------- #

def describe(cfg: SweepConfig, runs: list[RunSpec]) -> str:
    n_cond = len({r.condition for r in runs})
    agents = len(cfg.base.get("agents", []))
    ticks = cfg.ticks or int(cfg.base.get("total_ticks", 0))
    lines = [f"Sweep:    {cfg.name}", f"Baseline: {cfg.scenario_path}"]
    if cfg.factors:
        lines.append("Varying:")
        width = max(len(f.path) for f in cfg.factors)
        lines += [f"  {f.path.ljust(width)}  {' | '.join(f.labels)}" for f in cfg.factors]
    else:
        lines.append("Varying:  nothing (seed replication only)")
    if len(cfg.seeds) == 1:
        lines.append(f"Seed:     {cfg.seeds[0]} (add `seeds:` to repeat each condition)")
    else:
        lines.append(f"Seeds:    {', '.join(map(str, cfg.seeds))}")
    if cfg.ticks:
        lines.append(f"Ticks:    {cfg.ticks} (shortened from {cfg.base.get('total_ticks')})")
    lines += [
        f"Runs:     {len(runs)} = {n_cond} condition(s) x {len(cfg.seeds)} seed(s), "
        f"{cfg.parallel_runs} at a time, {cfg.agent_concurrency} model calls at a time each",
        f"Cost:     at least {agents * ticks * len(runs):,} model calls "
        f"({agents} agents x {ticks} ticks x {len(runs)} runs, plus probes/meetings)",
        f"Output:   {cfg.output}",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# execution
# --------------------------------------------------------------------------- #

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _set_aside_previous_attempt(run_dir: Path) -> Path | None:
    """Move a non-empty run dir from an earlier attempt out of the way.

    The ledger and model_calls files are opened in append mode, so relaunching
    into an old directory would concatenate two runs (two event_id 0 lines,
    broken hash chain). The old attempt is renamed, never deleted.
    """
    if not run_dir.is_dir() or not any(run_dir.iterdir()):
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    aside = run_dir.with_name(f"{run_dir.name}.prev-{stamp}")
    run_dir.rename(aside)
    return aside


def _scenario_sha256(run: RunSpec) -> str:
    blob = json.dumps(run.scenario, sort_keys=True, default=str, ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _write_scenario(run: RunSpec) -> Path:
    import yaml

    path = run.run_dir / SCENARIO_NAME
    _atomic_write(path, yaml.safe_dump(run.scenario, sort_keys=False, allow_unicode=True))
    return path


def subprocess_launcher(run: RunSpec, agent_concurrency: int) -> int:
    """Run one simulation as its own process; output goes to run.log."""
    cmd = [sys.executable, "-m", "verdi.society.live",
           "--scenario", str(run.run_dir / SCENARIO_NAME),
           "--run-dir", str(run.run_dir),
           "--seed", str(run.seed),
           "--concurrency", str(agent_concurrency)]
    with (run.run_dir / LOG_NAME).open("w", encoding="utf-8") as log:
        return subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=False).returncode


def _status(code: int) -> str:
    return {0: "complete", 3: "stalled"}.get(code, "failed")


def _code_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                             check=False, cwd=Path(__file__).resolve().parent)
        return out.stdout.strip() or None
    except OSError:
        return None


def _fmt_secs(s: float) -> str:
    m, s = divmod(int(s), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


@dataclass
class SweepResult:
    output: Path
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        return 0 if all(e.get("status") == "complete" for e in self.entries.values()) else 1


def run_sweep(cfg: SweepConfig, runs: list[RunSpec],
              launcher: Callable[[RunSpec, int], int] = subprocess_launcher,
              out: Callable[[str], None] = print) -> SweepResult:
    cfg.output.mkdir(parents=True, exist_ok=True)
    manifest_path = cfg.output / MANIFEST_NAME
    manifest: dict[str, Any] = {}
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.setdefault("name", cfg.name)
    manifest.setdefault("created_at_utc", _utc_now())
    manifest.update({
        "baseline_scenario": str(cfg.scenario_path),
        "factors": {f.path: dict(zip(f.labels, f.values, strict=True)) for f in cfg.factors},
        "seeds": cfg.seeds,
        "ticks_override": cfg.ticks,
        "code_commit": _code_commit(),
        "note": "Identical seeds do not guarantee identical model output; "
                "compare conditions across seeds, not single runs.",
    })
    entries: dict[str, dict[str, Any]] = manifest.setdefault("runs", {})
    lock = threading.Lock()

    def save() -> None:
        _atomic_write(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    def reusable(r: RunSpec) -> bool:
        e = entries.get(r.run_id, {})
        if e.get("status") != "complete":
            return False
        # A finished run is only reused if it ran this exact scenario; a run_id
        # (condition/seed) alone does not capture e.g. a changed --ticks.
        # Entries from before hashes were recorded are trusted as before.
        recorded = e.get("scenario_sha256")
        return recorded is None or recorded == _scenario_sha256(r)

    todo = [r for r in runs if not reusable(r)]
    if len(todo) < len(runs):
        out(f"Resuming: {len(runs) - len(todo)} run(s) already complete, {len(todo)} to go.")
    for r in runs:
        entries.setdefault(r.run_id, {})
        entries[r.run_id].update({"condition": r.condition, "labels": r.labels,
                                  "seed": r.seed, "run_dir": str(r.run_dir)})
        if r in todo:
            entries[r.run_id]["status"] = "pending"
            entries[r.run_id]["scenario_sha256"] = _scenario_sha256(r)
    # Runs left over from an earlier invocation with other seeds/factors stay
    # on disk but are excluded from this sweep's results and exit code.
    manifest["current_runs"] = sorted(r.run_id for r in runs)
    save()

    done = len(runs) - len(todo)

    def one(run: RunSpec) -> tuple[RunSpec, int, float]:
        t0 = time.monotonic()
        try:
            # Setup I/O is inside the guard too: one bad run dir must not
            # abort the whole sweep.
            _set_aside_previous_attempt(run.run_dir)
            run.run_dir.mkdir(parents=True, exist_ok=True)
            _write_scenario(run)
            with lock:
                entries[run.run_id].update({"status": "running", "started_at_utc": _utc_now()})
                save()
            t0 = time.monotonic()
            code = launcher(run, cfg.agent_concurrency)
        except Exception as exc:  # noqa: BLE001 - one run must never stop the sweep
            with lock:
                entries[run.run_id]["error"] = repr(exc)
            code = 1
        return run, code, time.monotonic() - t0

    def record(run: RunSpec, code: int, secs: float) -> str:
        status = _status(code)
        with lock:
            entries[run.run_id].update({"status": status, "exit_code": code,
                                        "wall_seconds": round(secs, 1),
                                        "finished_at_utc": _utc_now()})
            save()
        return status

    pool = ThreadPoolExecutor(max_workers=cfg.parallel_runs)
    futures: list[Future] = []
    recorded: set[int] = set()
    try:
        futures = [pool.submit(one, r) for r in todo]
        for fut in as_completed(futures):
            run, code, secs = fut.result()
            recorded.add(id(fut))
            status = record(run, code, secs)
            done += 1
            tail = "" if status == "complete" else f"  (see {run.run_dir / LOG_NAME})"
            out(f"[{done}/{len(runs)}] {status:<8} {run.run_id}  {_fmt_secs(secs)}{tail}")
    except KeyboardInterrupt:
        # Do not let the executor drain its queue (a `with` block would wait
        # for, and so START, every queued run after Ctrl-C).
        pool.shutdown(wait=False, cancel_futures=True)
        # Keep results of runs that already finished but were not yet recorded.
        for fut in futures:
            if id(fut) in recorded or not fut.done() or fut.cancelled():
                continue
            with contextlib.suppress(BaseException):
                record(*fut.result())
                done += 1
        with lock:
            for e in entries.values():
                if e.get("status") in ("running", "pending"):
                    e["status"] = "interrupted"
            save()
        out("Interrupted. Run the same command again to resume.")
        raise
    pool.shutdown(wait=True)
    current = {r.run_id for r in runs}
    return SweepResult(output=cfg.output,
                       entries={k: v for k, v in entries.items() if k in current})


# --------------------------------------------------------------------------- #
# results
# --------------------------------------------------------------------------- #

def _flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            flat.update(_flatten(v, f"{prefix}{k}."))
    elif isinstance(obj, (int, float, str)) or obj is None:
        flat[prefix[:-1]] = obj
    return flat


def _run_row(run_dir: Path) -> dict[str, Any]:
    from verdi.analysis.outcome_metrics import summarize_run

    row: dict[str, Any] = {}
    metrics_path = run_dir / "metrics.json"
    if metrics_path.is_file():
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        for k in ("accepted_mutating_actions", "rejected_actions", "sanctions", "appeals",
                  "restorations", "assembly_outcome", "model_calls"):
            if k in metrics:
                row[k] = metrics[k]
    try:
        summary = summarize_run(run_dir)
        summary.pop("run_dir", None)
        for k, v in _flatten(summary).items():
            if ".holdings." not in k and ".agents." not in k:
                row[k] = v
    except Exception:  # noqa: BLE001 - incomplete runs still get a row
        pass
    calls = run_dir / "model_calls.jsonl"
    if calls.is_file():
        served: set[str] = set()
        prompt = completion = 0
        for line in calls.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            prompt += int(rec.get("prompt_tokens") or 0)
            completion += int(rec.get("completion_tokens") or 0)
            if rec.get("model"):
                served.add(str(rec["model"]))
        row.update({"prompt_tokens": prompt, "completion_tokens": completion,
                    "model_served": ";".join(sorted(served))})
    return row


def collect(output: Path) -> list[dict[str, Any]]:
    manifest = json.loads((output / MANIFEST_NAME).read_text(encoding="utf-8"))
    current = manifest.get("current_runs")
    rows = []
    for run_id, e in sorted(manifest.get("runs", {}).items()):
        if current is not None and run_id not in current:
            continue  # left over from an earlier invocation of this sweep
        run_dir = Path(e["run_dir"])
        row: dict[str, Any] = {"run": run_id, "condition": e.get("condition"),
                               "seed": e.get("seed"), "status": e.get("status")}
        row.update({k: v for k, v in (e.get("labels") or {}).items()})
        sc_path = run_dir / SCENARIO_NAME
        if sc_path.is_file():
            import yaml
            sc = yaml.safe_load(sc_path.read_text(encoding="utf-8")) or {}
            row["model_requested"] = (sc.get("model") or {}).get("name")
        row["wall_seconds"] = e.get("wall_seconds")
        if e.get("status") == "complete":
            row.update(_run_row(run_dir))
        row["report"] = str(run_dir / "report.html") if (run_dir / "report.html").is_file() else ""
        rows.append(row)
    return rows


def write_summary_csv(rows: list[dict[str, Any]], path: Path) -> Path:
    cols: list[str] = []
    for row in rows:
        cols += [k for k in row if k not in cols]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def write_comparison_html(rows: list[dict[str, Any]], path: Path, title: str) -> Path:
    e = html.escape
    by_cond: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_cond.setdefault(str(r["condition"]), []).append(r)
    metrics = [m for m in HEADLINE if any(_num(r.get(m)) is not None for r in rows)]

    def cell(vals: list[float]) -> str:
        if not vals:
            return "<td class=na>n/a</td>"
        mean = statistics.fmean(vals)
        sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
        return f"<td>{mean:,.3g}<span class=sd> ± {sd:,.2g}</span></td>"

    head = "".join(f"<th>{e(c)}</th>" for c in by_cond)
    body = []
    for m in metrics:
        tds = "".join(cell([v for r in by_cond[c] if r.get("status") == "complete"
                            and (v := _num(r.get(m))) is not None]) for c in by_cond)
        body.append(f"<tr><th class=metric>{e(HEADLINE[m])}</th>{tds}</tr>")
    done_row = "".join(
        f"<td>{sum(r.get('status') == 'complete' for r in rs)}/{len(rs)}</td>"
        for rs in by_cond.values())
    run_rows = []
    for r in rows:
        link = ""
        if r.get("report"):
            link = f'<a href="{e(os.path.relpath(r["report"], path.parent))}">report</a>'
        status = e(str(r["status"]))
        model = e(str(r.get("model_served") or r.get("model_requested") or ""))
        run_rows.append(f"<tr><td>{e(str(r['run']))}</td><td class=st-{status}>{status}</td>"
                        f"<td>{model}</td><td>{link}</td></tr>")
    page = f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{e(title)}</title><style>
:root{{--bg:#fff;--fg:#1d1d1f;--mut:#6e6e73;--line:#e5e5ea;--ok:#1a7f37;--bad:#c62828}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151517;--fg:#f2f2f7;--mut:#98989f;--line:#2c2c2e;--ok:#4cc26e;--bad:#ff6b6b}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0;padding:24px 16px;max-width:1100px;margin:auto}}
h1{{font-size:22px;margin:0 0 4px}}p{{color:var(--mut);margin:0 0 20px}}
.wrap{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;margin-bottom:32px}}
th,td{{text-align:right;padding:6px 10px;border-bottom:1px solid var(--line);white-space:nowrap}}
th.metric,td:first-child{{text-align:left}}.sd,.na{{color:var(--mut)}}
.st-complete{{color:var(--ok)}}.st-failed,.st-stalled,.st-interrupted{{color:var(--bad)}}
a{{color:inherit}}</style></head><body>
<h1>{e(title)}</h1>
<p>Mean ± standard deviation across seeds, per condition. Only completed runs are counted.
Full per-run numbers: summary.csv (opens in Excel / Sheets).</p>
<div class=wrap><table><thead><tr><th class=metric>Metric</th>{head}</tr></thead><tbody>
<tr><th class=metric>Runs completed</th>{done_row}</tr>{''.join(body)}</tbody></table></div>
<h1>Runs</h1><div class=wrap><table><thead><tr><th class=metric>Run</th><th>Status</th><th>Model</th><th>Report</th></tr></thead>
<tbody>{''.join(run_rows)}</tbody></table></div></body></html>"""
    _atomic_write(path, page)
    return path


def write_results(output: Path, title: str) -> tuple[Path, Path]:
    rows = collect(output)
    return (write_summary_csv(rows, output / "summary.csv"),
            write_comparison_html(rows, output / "comparison.html", title))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _parse_vary(items: list[str]) -> dict[str, Any]:
    import yaml

    vary: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise SweepError(f"--vary {item!r}: use field=value1,value2 "
                             "(e.g. --vary model.name=qwen,deepseek)")
        key, values = item.split("=", 1)
        parsed = [yaml.safe_load(v) for v in values.split(",") if v.strip()]
        vary[key.strip()] = [v if not isinstance(v, (dict, list)) else str(v) for v in parsed]
    return vary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m verdi.sweep",
        description="Rerun a scenario many times, changing only what you list.",
        epilog="Examples:\n"
               "  python -m verdi.sweep examples/sweeps/model_comparison.yaml\n"
               "  python -m verdi.sweep --scenario examples/full.yaml "
               "--vary model.name=qwen3.8-27b,deepseek-v4 --seeds 42,43,44\n"
               "  python -m verdi.sweep examples/sweeps/model_comparison.yaml --results",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sweep_file", nargs="?", help="sweep YAML file")
    parser.add_argument("--scenario", help="quick sweep: baseline scenario (instead of a file)")
    parser.add_argument("--vary", action="append", default=[], metavar="FIELD=A,B",
                        help="quick sweep: a field and its values (repeatable)")
    parser.add_argument("--seeds", help="seeds to run, e.g. 42,43,44")
    parser.add_argument("--name", help="quick sweep: name (default: scenario file name)")
    parser.add_argument("--parallel-runs", type=int, help="simulations at the same time")
    parser.add_argument("--agent-concurrency", type=int,
                        help="model calls at the same time inside each simulation")
    parser.add_argument("--ticks", type=int, help="shorten every run (quick pilot)")
    parser.add_argument("--output", help="output folder")
    parser.add_argument("--dry-run", action="store_true",
                        help="show the plan, check the models, and stop (no simulation runs)")
    parser.add_argument("--skip-check", action="store_true",
                        help="do not test each model endpoint before starting")
    parser.add_argument("--yes", "-y", action="store_true", help="do not ask before starting")
    parser.add_argument("--results", action="store_true",
                        help="only rebuild summary.csv and comparison.html")
    args = parser.parse_args(argv)
    with contextlib.suppress(AttributeError, ValueError):
        sys.stdout.reconfigure(line_buffering=True)  # keep order when piped to a file

    if bool(args.sweep_file) == bool(args.scenario):
        parser.error("give a sweep file OR --scenario (not both)")
    if args.sweep_file:
        import yaml
        path = Path(args.sweep_file)
        if not path.is_file():
            raise SweepError(f"sweep file not found: {path}")
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise SweepError(f"{path} is not valid YAML:\n{exc}") from None
        if not isinstance(raw, dict):
            raise SweepError(f"{path} must be a non-empty YAML mapping")
        if args.vary:
            vary = raw.get("vary") or {}
            if not isinstance(vary, dict):
                raise SweepError(f"{path}: `vary:` must be a mapping")
            raw["vary"] = {**vary, **_parse_vary(args.vary)}
    else:
        raw = {"scenario": args.scenario, "vary": _parse_vary(args.vary)}
        if args.name:
            raw["name"] = args.name
    if args.seeds:
        try:
            raw["seeds"] = [int(s) for s in args.seeds.split(",") if s.strip()]
        except ValueError:
            raise SweepError(f"--seeds {args.seeds!r}: use whole numbers, e.g. 42,43,44") from None
    for key, val in (("parallel_runs", args.parallel_runs), ("agent_concurrency",
                     args.agent_concurrency), ("ticks", args.ticks), ("output", args.output)):
        if val is not None:
            raw[key] = val

    cfg = build_config(raw)
    if args.results:
        if not (cfg.output / MANIFEST_NAME).is_file():
            raise SweepError(f"no sweep found in {cfg.output}")
        csv_path, html_path = write_results(cfg.output, cfg.name)
        print(f"Wrote {csv_path}\nWrote {html_path}")
        return 0

    runs = expand(cfg)
    print(describe(cfg, runs))
    warnings = validate_runs(runs)
    print(f"Check:    all {len(runs)} scenario(s) valid")
    for w in warnings:
        print(f"Warning:  {w}")
    if not args.skip_check:
        n_models = len({json.dumps(r.scenario.get("model"), sort_keys=True) for r in runs})
        print(f"Check:    testing {n_models} model endpoint(s) with one small call each...")
        problems = preflight(runs)
        if problems:
            raise SweepError("these models cannot be used, nothing was started:\n  "
                             + "\n  ".join(problems)
                             + "\nFix the model name/key, or remove it from `vary:`.")
        print("Check:    all model endpoints answered")
    if args.dry_run:
        return 0
    if not args.yes:
        if not sys.stdin.isatty():
            raise SweepError("not starting without confirmation; pass --yes")
        if input(f"\nStart {len(runs)} run(s)? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Not started.")
            return 0
    print()
    result = run_sweep(cfg, runs)
    csv_path, html_path = write_results(cfg.output, cfg.name)
    n_ok = sum(e.get("status") == "complete" for e in result.entries.values())
    print(f"\nDone: {n_ok}/{len(result.entries)} runs complete.")
    print(f"  Compare:     {html_path}")
    print(f"  Spreadsheet: {csv_path}")
    if result.exit_code:
        print("  Some runs did not complete. Run the same command again to retry them.")
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
