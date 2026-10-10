"""Shared fixtures for the G5-close tests (offline only, no network, no .env).

The REAL run root constant is resolved against the MAIN repo root ONLY here
in tests (per worker contract); src code never hardcodes it.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

#: Main checkout (integrator-owned; tests read the gitignored run root only).
MAIN_REPO_ROOT = Path("/Users/sushantgautam/Documents/verdi")
REAL_G5_RUN_ROOT = MAIN_REPO_ROOT / "runs" / "live" / "g5_pairs" / "20261010T162928Z"

requires_real_run_root = pytest.mark.skipif(
    not REAL_G5_RUN_ROOT.is_dir(),
    reason=f"real G5 run root {REAL_G5_RUN_ROOT} not present",
)


def _mini_prompt(actor: str, tick: int, task: str, kind: str = "transport") -> str:
    return (
        '[prompt:verdi.agent-policy-prompt/1]\n'
        f'You are agent "{actor}" in a simulated society at tick {tick}.\n'
        "Role: engineer\n"
        f"Task {task} (action_type={kind}):\n"
        f'{{"action_type": "{kind}", "actor_id": "{actor}", '
        f'"constraints": {{"units": 1}}, "task_id": "{task}", "tick_created": {tick}}}\n'
        "Respond with JSON ONLY, an object with exactly these keys:\n"
        '{"action_kind": str, "arguments": object, "reason": str}\n'
        "The proposed action will be validated by a deterministic engine.\n"
    )


def _mini_response(task: str, actor: str, seq: int) -> str:
    return json.dumps(
        {
            "action_kind": "transport",
            "arguments": {"task_id": task, "units": 1},
            "reason": f"accept {task} for {actor} row {seq}",
        }
    )


def _mini_row(
    pair: int,
    seq: int,
    actor: str,
    tick: int,
    task: str,
    kind: str = "transport",
    quarantined: bool = False,
    quarantine_reason: str | None = None,
) -> dict:
    prompt = _mini_prompt(actor, tick, task, kind)
    return {
        "actor_id": actor,
        "lifecycle_tick": tick,
        "model": "Qwen3.8-27B",
        "parsed_action": {
            "action_kind": kind,
            "actor_id": actor,
            "arguments": {"task_id": task, "units": 1},
            "task_id": task,
            "schema_version": 1,
            "reason": "mini",
        },
        "prompt": prompt,
        "quarantined": quarantined,
        "quarantine_reason": quarantine_reason,
        "reasoning_content": "",
        "request_id": f"mini-{pair}-{seq}",
        "response_raw": _mini_response(task, actor, seq),
        "seq": seq,
        "task_id": task,
        "timestamp_utc": "2026-01-01T00:00:00+00:00",
        "trace_id": f"mini-trace-{pair}-{seq}",
        "usage": {"total_tokens": 1},
    }


def _mini_event(event_id: int, actor: str, task: str, kind: str = "transport") -> dict:
    return {
        "event_id": event_id,
        "actor_id": actor,
        "kind": "action_accepted",
        "decision": "accepted",
        "tick": 1,
        "payload": {"action_kind": kind, "task_id": task, "arguments": {}},
    }


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))


def make_mini_run_root(root: Path) -> Path:
    """2-pair synthetic G5 run root: 3 tape rows per pair (ticks 1, 2, 13).

    Clean by construction: B1..B4 all pass. Suffix row (tick 13) is
    IDENTICAL across pairs (B4). Each pair also carries a
    ``treatment/event_log.jsonl`` with one accepted action event per row
    so the engine outcome maps (decision "accepted").
    """
    rows_by_pair: list[list[dict]] = []
    for pair in range(2):
        rows = [
            _mini_row(pair, 0, "a-01", 1, "pilotv1-t01-01"),
            _mini_row(pair, 1, "a-02", 2, "pilotv1-t02-01"),
            _mini_row(pair, 2, "a-03", 13, "pilotv1-t13-01"),
        ]
        rows_by_pair.append(rows)
        write_jsonl(root / f"pair_{pair}" / "live_tape.jsonl", rows)
        write_jsonl(
            root / f"pair_{pair}" / "treatment" / "event_log.jsonl",
            [_mini_event(i, r["actor_id"], r["task_id"]) for i, r in enumerate(rows)],
        )
        # Arm run dicts for the identification diagnostics: identical
        # six-estimand vectors across pairs, with a single non-zero
        # treatment-minus-placebo delta (false_punishment_rate +0.1) so the
        # permutation-null math is non-trivial.
        estimates_t = {
            "false_punishment_rate": 0.10,
            "inequality_gini_food": 0.0,
            "safety_rate": 0.0,
            "sustained_recovery": 0.0,
            "time_under_deprivation": 0.0,
            "welfare_mean_food": 0.0,
        }
        estimates_p = dict(estimates_t)
        estimates_p["false_punishment_rate"] = 0.0
        (root / f"pair_{pair}" / "treatment_run_dict.json").write_text(
            json.dumps({"arm": "treatment", "estimates": estimates_t}, sort_keys=True) + "\n"
        )
        (root / f"pair_{pair}" / "placebo_run_dict.json").write_text(
            json.dumps({"arm": "placebo", "estimates": estimates_p}, sort_keys=True) + "\n"
        )
    return root


def clone_run_root(src: Path, dst: Path) -> Path:
    """Deep copy a run root's jsonl/json files into dst (for corruption tests)."""
    for path in src.rglob("*"):
        if path.is_file():
            rel = path.relative_to(src)
            out = dst / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix == ".jsonl":
                rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
                write_jsonl(out, copy.deepcopy(rows))
            else:
                out.write_text(path.read_text())
    return dst


def mutate_tape(root: Path, pair: int, seq: int, **replacements) -> None:
    path = root / f"pair_{pair}" / "live_tape.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    for r in rows:
        if r["seq"] == seq:
            r.update(copy.deepcopy(replacements))
            break
    write_jsonl(path, rows)


def tape_rows(root: Path, pair: int) -> list[dict]:
    path = root / f"pair_{pair}" / "live_tape.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
