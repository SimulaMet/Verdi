"""Live entrypoint for a Society V2 run (SimulaChat default Qwen, vLLM config).

Wires the real pooled :class:`~verdi.agents.client.ModelClient` into
:class:`~verdi.society.campaign.SocietyCampaign`. Credentials come
exclusively from the process environment (``VERDI_VLLM_*``); a ``.env`` file
is loaded via the canonical loader when required (no python-dotenv).
Concurrency bootstrap is 4 (``VERDI_MAX_CONCURRENCY``); step-up is manual and
measured (protocol §4).

Usage::

    python -m verdi.society.live --scenario experiments/scenarios/society_v2_stageA.json \
        --run-dir runs/society-v2/stageA-20261011 [--seed 20261011] [--concurrency 4]

No artifact ever contains key material (hard-guarded before write).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from verdi.agents.client import ModelClient
from verdi.audit.judge_live import _assert_no_key_material
from verdi.config import ModelEndpointConfig, model_endpoint_from_env
from verdi.experiments.g5_judge_live import _load_dotenv_into_environ
from verdi.society.campaign import PROTOCOL_VERSION, SocietyCampaign

DEFAULT_CONCURRENCY = 4


def resolve_endpoint_config() -> ModelEndpointConfig:
    """Load .env (if any) then build the endpoint config; fail loudly if unset."""
    _load_dotenv_into_environ()
    config = model_endpoint_from_env()
    if config is None:
        raise SystemExit(
            "model endpoint not configured: set VERDI_VLLM_BASE_URL and VERDI_VLLM_MODEL "
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


async def _run(scenario_path: Path, run_dir: Path, seed: int | None, concurrency: int | None) -> int:
    config = resolve_endpoint_config()
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    seed = int(seed if seed is not None else scenario["run_seed"])
    if concurrency is None:
        concurrency = int(os.environ.get("VERDI_MAX_CONCURRENCY", str(DEFAULT_CONCURRENCY)))
        concurrency = max(1, min(concurrency, config.max_concurrency))

    run_dir.mkdir(parents=True, exist_ok=True)
    _write_run_meta(run_dir, {
        "protocol": PROTOCOL_VERSION,
        "scenario": str(scenario_path),
        "world_id": scenario["world_id"],
        "run_seed": seed,
        "model": config.model,
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

        campaign = SocietyCampaign(
            scenario=scenario,
            run_dir=run_dir,
            caller=client,
            run_seed=seed,
            concurrency=concurrency,
        )
        result = await campaign.run()

    # Hard guard: no key material may reach the artifact tree.
    _guard_artifacts_clean(run_dir)

    _write_run_meta(run_dir, {
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
    print(f"  sanctions/appeals  = {len(result.sanction_events)}/{len(result.appeal_events)}")
    print(f"  replay_matches     = {result.replay_matches}")
    print(f"  replay_log_identical = {result.replay_log_identical}")
    print(f"  wall_seconds       = {result.wall_seconds:.1f}")
    return 0


def _guard_artifacts_clean(run_dir: Path) -> None:
    for path in sorted(run_dir.rglob("*")):
        if path.is_file():
            _assert_no_key_material(path.read_text(encoding="utf-8", errors="replace"), str(path))


def _write_run_meta(run_dir: Path, obj: dict) -> None:
    path = run_dir / "run_meta.json"
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    existing.update(obj)
    _assert_no_key_material(json.dumps(existing), "run_meta")
    path.write_text(json.dumps(existing, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a Society V2 live campaign.")
    parser.add_argument("--scenario", required=True, type=Path, help="scenario JSON path")
    parser.add_argument("--run-dir", required=True, type=Path, help="artifact output directory")
    parser.add_argument("--seed", type=int, default=None, help="run seed (default: scenario run_seed)")
    parser.add_argument("--concurrency", type=int, default=None,
                        help="proposal concurrency (default: VERDI_MAX_CONCURRENCY or 4)")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.scenario, args.run_dir, args.seed, args.concurrency))


if __name__ == "__main__":
    raise SystemExit(main())
