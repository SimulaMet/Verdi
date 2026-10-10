"""Offline tests for the Society V2 live entrypoint (mocked transport).

No network: the pooled ModelClient runs against httpx.MockTransport.
Verifies env resolution, the pre-run smoke call, end-to-end live wiring,
and the no-key-material artifact guard.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from verdi.agents.client import ModelClient
from verdi.config import ModelEndpointConfig
from verdi.society import live as live_module

SCENARIO = Path("experiments/scenarios/society_v2_stageA.json")


class _ActionRouter:
    """Maps request bodies to scripted JSON responses (deterministic)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        message = body["messages"][-1]["content"]
        self.calls.append(message)

        if 'Respond with JSON ONLY: {"ok": true}' in message:
            content = '{"ok": true}'
        elif "verdi.reflection" in message:
            content = json.dumps({"text": "Adjusted based on committed outcomes."})
        elif "assembly:deliberation" in message:
            content = json.dumps({"abstain": True})
        elif "assembly:vote" in message:
            content = json.dumps({"choice": "abstain"})
        elif "probe" in message.lower():
            content = "yes"
        else:
            actor = None
            for aid in ("s01", "s02", "s03", "s04", "s05", "s06"):
                if f'agent "{aid}"' in message:
                    actor = aid
                    break
            if actor == "s05":
                content = json.dumps({"action_kind": "transfer",
                                      "arguments": {"to": "s01", "resource": "credits", "units": 999},
                                      "reason": "overreach"})
            else:
                content = json.dumps({"action_kind": "emit_signal",
                                      "arguments": {"text": "coordinating"}, "reason": "cooperation"})
        payload = {
            "id": f"chatcmpl-{len(self.calls)}",
            "object": "chat.completion",
            "created": 0,
            "model": "Qwen3.8-27B",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                          "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        return httpx.Response(200, json=payload)


def _mock_transport() -> httpx.MockTransport:
    router = _ActionRouter()
    transport = httpx.MockTransport(router.handle)
    transport.router = router  # type: ignore[attr-defined]
    return transport


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VERDI_VLLM_BASE_URL", "http://mock.invalid/v1")
    monkeypatch.setenv("VERDI_VLLM_MODEL", "Qwen3.8-27B")
    monkeypatch.setenv("VERDI_VLLM_API_KEY", "test-key-not-real")
    monkeypatch.setenv("VERDI_MAX_CONCURRENCY", "4")
    monkeypatch.setenv("VERDI_REQUEST_TIMEOUT", "30")
    monkeypatch.setenv("VERDI_MAX_RETRIES", "2")
    yield


def test_resolve_endpoint_config_reads_env():
    config = live_module.resolve_endpoint_config()
    assert config.model == "Qwen3.8-27B"
    assert config.max_concurrency == 4


def test_smoke_call_via_mock_transport():
    config = live_module.resolve_endpoint_config()
    transport = _mock_transport()
    import asyncio

    async def _go():
        async with ModelClient(config, transport=transport) as client:
            return await live_module.smoke_call(client)

    outcome = asyncio.run(_go())
    assert outcome["ok"] is True
    assert outcome["model"] == "Qwen3.8-27B"


def test_main_end_to_end_with_mock_transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                             capsys: pytest.CaptureFixture[str]):
    transport = _mock_transport()

    real_client = ModelClient

    def factory(config: ModelEndpointConfig, **kwargs: Any) -> ModelClient:
        kwargs["transport"] = transport
        return real_client(config, **kwargs)

    monkeypatch.setattr(live_module, "ModelClient", factory)

    run_dir = tmp_path / "live-run"
    code = live_module.main(["--scenario", str(SCENARIO), "--run-dir", str(run_dir), "--concurrency", "2"])
    assert code == 0

    for name in ("manifest.json", "events.jsonl", "model_calls.jsonl", "probes.jsonl",
                 "metrics.json", "health.json", "checkpoint.json", "run_meta.json", "timeline.md"):
        assert (run_dir / name).exists(), name
    meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
    assert meta["smoke"]["ok"] is True
    assert meta["model"] == "Qwen3.8-27B"
    assert meta["concurrency"] == 2
    checkpoint = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["status"] == "complete"
    # No key material anywhere in the artifact tree.
    for path in run_dir.rglob("*"):
        if path.is_file():
            from verdi.audit.judge_live import _assert_no_key_material
            _assert_no_key_material(path.read_text(encoding="utf-8", errors="replace"), str(path))
    out = capsys.readouterr().out
    assert "run complete" in out and "replay_matches" in out


def test_main_fails_cleanly_without_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("VERDI_VLLM_BASE_URL", raising=False)
    monkeypatch.delenv("VERDI_VLLM_MODEL", raising=False)
    monkeypatch.setenv("VERDI_DOTENV_PATH", str(tmp_path / "does-not-exist.env"))
    with pytest.raises(SystemExit) as excinfo:
        live_module.main(["--scenario", str(SCENARIO), "--run-dir", str(tmp_path)])
    assert "not configured" in str(excinfo.value)
