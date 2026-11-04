"""Live entrypoint for a society run (real Qwen, vLLM endpoint).

Wires the real pooled :class:`~verdi.agents.client.ModelClient` into
:class:`~verdi.society.campaign.SocietyCampaign`. Credentials come
exclusively from the process environment (``VERDI_VLLM_*``); a ``.env`` file
is loaded via the canonical loader when required (no python-dotenv).
Concurrency bootstrap is 4 (``VERDI_MAX_CONCURRENCY``); step-up is manual and
measured (protocol §4).

Usage::

    python -m verdi.society.live --scenario examples/starter.yaml \
        --run-dir runs/my-run [--seed 42] [--concurrency 4]

On completion the run auto-generates ``<run-dir>/report.html`` (a local,
model-free HTML rendering of the run — open it in a browser to see the
society). Disable with ``--no-report``; report generation is a convenience
step and a failure there never fails the run. No artifact ever contains key
material (hard-guarded before write, including report.html).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from verdi.agents.client import _BACKOFF_CAP, _CONNECT_TIMEOUT, _POOL_TIMEOUT, ModelClient
from verdi.analysis.scenario import load_scenario
from verdi.audit.judge_live import _assert_no_key_material
from verdi.config import ModelEndpointConfig, api_key_values, model_endpoint_from_env
from verdi.experiments.g5_judge_live import _load_dotenv_into_environ
from verdi.society.campaign import PROTOCOL_VERSION, SocietyCampaign

DEFAULT_CONCURRENCY = 4
# Per-model-call liveness watchdog. Calibrated per Tranche 12d review F1:
# per-call beats (model_calls.jsonl, written after every COMPLETED model
# call) reset the deadline continuously, so the window only needs to outlast
# a SINGLE stuck call: VERDI_REQUEST_TIMEOUT (180s) x (max_retries+1) (6) =
# 1080s worst-case retry sequence, plus one heartbeat interval. 1200s covers
# that with margin; shorter windows could trip while the client is still
# legitimately retrying a dead call. The old events-only 600s design
# false-fired on whole assembly ticks (zero ledger writes until finalize)
# and killed the live C-1 run at t15 — see state/LESSONS.md Tranche 12d.
DEFAULT_STALL_TIMEOUT_SECONDS = 1200
_HEARTBEAT_INTERVAL_SECONDS = 20
#: Tranche 12i: model_calls.jsonl is the single per-call log (timestamps +
#: tokens + duration + context). The old progress.jsonl beat file is gone;
#: liveness is measured from model_calls.jsonl line growth.
_MODEL_CALLS_FILE = "model_calls.jsonl"


def _file_line_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as fh:
        return sum(1 for _ in fh)


def _progress_count(run_dir: Path) -> int:
    """Liveness signal: world events PLUS per-model-call beats.

    A full assembly tick appends nothing to events.jsonl until its very end
    (deliberation + voting + finalize all precede the ledger commit), so
    event count alone false-fires on legitimately long ticks (Tranche 12d
    review F1: killed the live C-1 run at its first assembly, t15).
    model_calls.jsonl gains a line after every COMPLETED model call (all
    purposes: action, reflection, assembly, probe), so the deadline now
    resets continuously during long ticks while still tripping on a stuck
    socket where no call ever finishes.
    """
    return _file_line_count(run_dir / "events.jsonl") + _file_line_count(
        run_dir / _MODEL_CALLS_FILE
    )


async def _stall_watchdog(run_dir: Path, stall_timeout_seconds: float,
                          extra_progress: Callable[[], int] | None = None) -> None:
    """Independent liveness task for a live run.

    A hung endpoint can hold a socket open without ever responding, which
    parks the whole campaign on one in-flight call while httpx's timeout
    (which only fires on read activity) never triggers (Tranche 12a lesson).
    This watchdog watches wall-clock progress of the COMBINED event +
    per-model-call beat count: it writes a heartbeat to run_meta on a short
    interval, and if that count does not grow for ``stall_timeout_seconds``
    it records ``status=stalled`` and exits the process with code 3 so a
    partial run is preserved. It runs as a SEPARATE asyncio task so it
    survives a hung in-flight call.
    """
    def progress() -> int:
        # extra_progress: in-process completed-call counter, so a long batch
        # whose file lines are written only at the end still counts as alive.
        return _progress_count(run_dir) + (extra_progress() if extra_progress else 0)

    deadline = time.monotonic() + stall_timeout_seconds
    last_count = progress()
    while True:
        await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
        now = time.monotonic()
        count = progress()
        _write_run_meta(run_dir, {
            "heartbeat_utc": datetime.now(timezone.utc).isoformat(),
            "events_logged": _file_line_count(run_dir / "events.jsonl"),
            "model_calls_beat": _file_line_count(run_dir / _MODEL_CALLS_FILE),
        })
        if count > last_count:
            last_count = count
            deadline = now + stall_timeout_seconds
            continue
        if now >= deadline:
            _write_run_meta(run_dir, {
                "status": "stalled",
                "stalled_at_utc": datetime.now(timezone.utc).isoformat(),
                "stall_timeout_seconds": stall_timeout_seconds,
                "events_logged": _file_line_count(run_dir / "events.jsonl"),
                "model_calls_beat": _file_line_count(run_dir / _MODEL_CALLS_FILE),
            })
            print(
                f"STALL: no events or completed model calls for "
                f"{stall_timeout_seconds:.0f}s (last count {count}); "
                "preserving run dir and exiting (code 3).",
                file=sys.stderr, flush=True,
            )
            os._exit(3)


def resolve_endpoint_config(scenario: dict | None = None) -> ModelEndpointConfig:
    """Load .env (if any) then build the endpoint config; fail loudly if unset.

    ``scenario`` supplies the optional ``model:`` block (base_url, name, ...).
    """
    _load_dotenv_into_environ()
    block = (scenario or {}).get("model")
    config = model_endpoint_from_env(block)
    if config is not None and scenario is not None and not block:
        print("WARNING: scenario has no `model:` block; using legacy VERDI_VLLM_* env "
              "(add `model:` so the run records its model).", file=sys.stderr, flush=True)
    if config is None:
        raise SystemExit(
            "model endpoint not configured: add a `model:` block (base_url, name) to the "
            "scenario, or set VERDI_VLLM_BASE_URL and VERDI_VLLM_MODEL "
            "(or provide a .env file / VERDI_DOTENV_PATH)"
        )
    return config


async def smoke_call(client: ModelClient) -> dict:
    """One minimal structured call to verify reachability before the run."""
    result = await client.generate_structured(
        [{"role": "user", "content": 'Respond with JSON ONLY: {"ok": true}'}],
        response_format_json=True,
        temperature=0.0,
    )
    try:
        data = json.loads(result.content)
    except json.JSONDecodeError:
        data = None
    return {
        "ok": isinstance(data, dict) and data.get("ok") is True,
        "model": result.model,
        "request_id": result.request_id,
        "trace_id": result.trace_id,
        "content_chars": len(result.content),
    }


async def _run(scenario_path: Path, run_dir: Path, seed: int | None, concurrency: int | None,
               stall_timeout_seconds: float | None = None, export_report: bool = True) -> int:
    scenario = load_scenario(scenario_path)
    config = resolve_endpoint_config(scenario)
    seed = int(seed if seed is not None else scenario["run_seed"])
    if concurrency is None:
        concurrency = int(os.environ.get("VERDI_MAX_CONCURRENCY", str(DEFAULT_CONCURRENCY)))
        concurrency = max(1, min(concurrency, config.max_concurrency))

    run_dir.mkdir(parents=True, exist_ok=True)
    # Frozen input: the exact scenario file this run was started from.
    shutil.copyfile(scenario_path, run_dir / f"scenario_input{Path(scenario_path).suffix}")
    _write_run_meta(run_dir, {
        "protocol": PROTOCOL_VERSION,
        "scenario": str(scenario_path),
        "world_id": scenario["world_id"],
        "run_seed": seed,
        "model": config.model,
        "base_url": config.base_url,
        "endpoint_alias": "simula",
        "concurrency": concurrency,
        "timeout_seconds": config.timeout_seconds,
        "max_retries": config.max_retries,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
    })

    async with ModelClient(config) as client:
        smoke = await smoke_call(client)
        _write_run_meta(run_dir, {"smoke": smoke})
        if not smoke["ok"]:
            print(f"smoke call failed: {smoke}", file=sys.stderr)
            return 2
        print(f"smoke OK: model={smoke['model']} trace={smoke['trace_id']}")

        # Tranche 12d F1 / 12i: model_calls.jsonl (written per completed
        # call by the campaign) is the liveness signal, so long assembly
        # ticks (zero event writes until finalize) don't false-fire the
        # watchdog. No wrapper needed — the campaign writes the file.
        campaign = SocietyCampaign(
            scenario=scenario,
            run_dir=run_dir,
            caller=client,
            run_seed=seed,
            concurrency=concurrency,
        )
        # Never let a (possibly user-supplied) stall window trip while the
        # client is still legitimately retrying one dead call: floor it at
        # timeout_seconds x (max_retries+1) + one heartbeat interval.
        # Per attempt: pool wait + connect + read timeout + max backoff.
        per_attempt = config.timeout_seconds + _POOL_TIMEOUT + _CONNECT_TIMEOUT + _BACKOFF_CAP
        retry_window = per_attempt * (config.max_retries + 1) + _HEARTBEAT_INTERVAL_SECONDS
        stall_timeout = stall_timeout_seconds if stall_timeout_seconds is not None \
            else DEFAULT_STALL_TIMEOUT_SECONDS
        if stall_timeout < retry_window:
            print(
                f"note: raising --stall-timeout {stall_timeout:.0f}s -> {retry_window:.0f}s "
                f"(client retry window)", flush=True,
            )
            stall_timeout = retry_window
        watchdog = asyncio.create_task(_stall_watchdog(
            run_dir, stall_timeout, extra_progress=lambda: getattr(client, "completed_calls", 0)))
        try:
            result = await campaign.run()
        except Exception:
            watchdog.cancel()
            _write_run_meta(run_dir, {
                "status": "failed",
                "failed_at_utc": datetime.now(timezone.utc).isoformat(),
            })
            raise
        finally:
            watchdog.cancel()

    # Hard guard: no key material may reach the artifact tree.
    _guard_artifacts_clean(run_dir)

    _write_run_meta(run_dir, {
        "status": "complete",
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "final_state_hash": result.final_state_hash,
        "replay_matches": result.replay_matches,
        "replay_log_identical": result.replay_log_identical,
        "model_calls": len(result.model_calls),
        "wall_seconds": round(result.wall_seconds, 2),
    })
    print(f"run complete: {run_dir}")
    print(f"  final_state_hash   = {result.final_state_hash}")
    print(f"  model_calls        = {len(result.model_calls)}")
    print(f"  events             = {len(result.events)}")
    print(f"  probes             = {len(result.probes)}")
    print(f"  assembly           = {(result.assembly_outcome or {}).get('outcome')}")
    n_sanctions = sum(1 for e in result.sanction_events
                      if e.get("kind") == "sanction_imposed"
                      and e.get("decision", "accepted") == "accepted")
    n_appeals = sum(1 for e in result.appeal_events if e.get("phase") == "filed")
    print(f"  sanctions/appeals  = {n_sanctions}/{n_appeals}")
    print(f"  replay_matches     = {result.replay_matches}")
    print(f"  replay_log_identical = {result.replay_log_identical}")
    print(f"  wall_seconds       = {result.wall_seconds:.1f}")

    if export_report:
        report_line = _export_html_report(run_dir)
        print(f"  {report_line}")
        _write_run_meta(run_dir, {"report_html": str(run_dir / "report.html")})
    return 0


def _export_html_report(run_dir: Path) -> str:
    """Write ``report.html`` from the run's on-disk artifacts.

    Pure local file read + one HTML write — NO model calls, so it is cheap and
    deterministic. It is a convenience for the user to view the run in a
    browser; a failure here NEVER fails the run (the evidence is the
    JSONL/JSON artifacts). The report is derived from artifacts that already
    passed the hard key-material guard, plus the plotly.js open-source bundle
    (a fixed asset, not run data), so we guard precisely against the actual
    endpoint secret rather than the broad sk-*/api_key= patterns, which
    false-positive on plotly.js boilerplate (e.g. 'mask-position', '?api_key='
    tile URLs).
    """
    try:
        from verdi.analysis.html_report import write_report
        out = write_report(run_dir, run_dir / "report.html")
        _guard_report_clean(out.read_text(encoding="utf-8"))
        return f"report: {out} ({out.stat().st_size // 1024} KB) — open in a browser"
    except Exception as exc:  # noqa: BLE001 - convenience artifact, not a gate
        return f"report: SKIPPED ({type(exc).__name__}) — run artifacts are unaffected"


def _guard_report_clean(html: str) -> None:
    """Guard the derived report against the run's actual endpoint secret.

    The broad KEY_MATERIAL_PATTERNS (sk-*/api_key=*) match plotly.js library
    boilerplate, so they are the wrong tool for an HTML report that embeds
    that bundle. This checks for the REAL secret value instead: if the
    endpoint API key from the environment appears in the rendered HTML, that
    is a genuine leak and we refuse to write it. Skips cleanly when the key is
    unset (offline / test context).
    """
    for key in api_key_values():
        if key in html:
            raise AssertionError("report.html would contain the endpoint API key")


def _guard_artifacts_clean(run_dir: Path) -> None:
    for path in sorted(run_dir.rglob("*")):
        if path.is_file():
            _assert_no_key_material(path.read_text(encoding="utf-8", errors="replace"), str(path))


def _write_run_meta(run_dir: Path, obj: dict) -> None:
    """Merge ``obj`` into run_meta.json atomically.

    Tranche 12d review F2: the watchdog and the main flow are two writers; a
    temp-file + os.replace keeps the file whole under any interleave (a crash
    mid-write can no longer truncate it) even though, on a single event loop,
    the synchronous read-modify-write is already GIL-atomic per call.
    """
    path = run_dir / "run_meta.json"
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    existing.update(obj)
    _assert_no_key_material(json.dumps(existing), "run_meta")
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(existing, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a Society V2 live campaign.")
    parser.add_argument("--scenario", required=True, type=Path, help="scenario JSON path")
    parser.add_argument("--run-dir", required=True, type=Path, help="artifact output directory")
    parser.add_argument("--seed", type=int, default=None, help="run seed (default: scenario run_seed)")
    parser.add_argument("--concurrency", type=int, default=None,
                        help="proposal concurrency (default: VERDI_MAX_CONCURRENCY or 4)")
    parser.add_argument("--stall-timeout", type=float, default=None,
                        help="abort if no events OR completed model calls for "
                             "this many seconds (default "
                             f"{DEFAULT_STALL_TIMEOUT_SECONDS}; guards against hung "
                             "endpoint sockets and never fires during long "
                             "assembly ticks because per-call beats reset the clock)")
    parser.add_argument("--no-report", action="store_true",
                        help="skip auto-generating report.html at the end "
                             "(on by default; report generation is a local, "
                             "model-free convenience step and never fails the run)")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.scenario, args.run_dir, args.seed, args.concurrency,
                            args.stall_timeout, export_report=not args.no_report))


if __name__ == "__main__":
    raise SystemExit(main())
