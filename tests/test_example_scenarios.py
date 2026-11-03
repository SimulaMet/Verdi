"""examples/starter.yaml and examples/full.yaml must load, normalize and run
end-to-end offline (scripted caller, no endpoint) so they cannot drift from
the engine."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from verdi.agents.client import ModelResult
from verdi.analysis.scenario import build_t0_state, load_scenario
from verdi.society.campaign import SocietyCampaign

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ["starter.yaml", "full.yaml"]


class _SignalCaller:
    """Every agent emits a signal; assemblies abstain/vote against."""

    def __init__(self) -> None:
        self.n = 0

    async def generate_structured(self, messages, **kwargs):
        self.n += 1
        content = messages[0]["content"]
        if "assembly:deliberation" in content:
            out = json.dumps({"abstain": True})
        elif "assembly:vote" in content:
            out = json.dumps({"choice": "against"})
        elif "verdi.consolidation" in content or "verdi.reflection" in content:
            out = "(offline)"
        elif re.search(r'agent "[a-z_]+"', content) and "probe" not in content.lower():
            out = json.dumps({"action_kind": "emit_signal",
                              "arguments": {"text": "working together"},
                              "reason": "cooperation"})
        else:
            out = "yes"
        return ModelResult(content=out, request_id=f"r{self.n}", model="fake",
                           trace_id=f"trace-{self.n}")


@pytest.mark.parametrize("name", EXAMPLES)
def test_example_has_model_block(name):
    sc = load_scenario(REPO_ROOT / "examples" / name)
    assert sc["model"]["base_url"] and sc["model"]["name"]
    assert "api_key" not in sc["model"]


@pytest.mark.parametrize("name", EXAMPLES)
def test_example_runs_offline(name, tmp_path):
    sc = load_scenario(REPO_ROOT / "examples" / name)
    build_t0_state(sc)
    res = asyncio.run(SocietyCampaign(
        scenario=sc, run_dir=tmp_path, caller=_SignalCaller(),
        run_seed=int(sc["run_seed"]), concurrency=4).run())
    assert res.replay_matches is True
    assert res.replay_log_identical is True


def test_full_is_superset_of_starter_keys():
    starter = load_scenario(REPO_ROOT / "examples" / "starter.yaml")
    full = load_scenario(REPO_ROOT / "examples" / "full.yaml")
    assert set(starter) <= set(full)
