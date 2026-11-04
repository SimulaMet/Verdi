"""Offline tests for the live society-run entrypoint (mocked transport).

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

from verdi_agents.agents.client import ModelClient
from verdi_agents.config import ModelEndpointConfig
from verdi_agents.society import live as live_module

SCENARIO = Path("tests/fixtures/scenarios/society_v2_stageA.json")


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
    # report.html is now auto-generated by default.
    report = run_dir / "report.html"
    assert report.exists(), "report.html should be generated by default"
    # Core evidence artifacts (everything except the derived report) must be
    # free of key material under the broad patterns.
    for path in run_dir.rglob("*"):
        if path.is_file() and path != report:
            from verdi_agents.audit.judge import _assert_no_key_material
            _assert_no_key_material(path.read_text(encoding="utf-8", errors="replace"), str(path))
    # The derived report embeds the plotly.js open-source bundle (which trips
    # the broad sk-*/api_key= patterns), so it is guarded PRECISELY: it must
    # never contain the actual endpoint secret from the environment.
    live_module._guard_report_clean(report.read_text(encoding="utf-8"))
    assert "test-key-not-real" not in report.read_text(encoding="utf-8")
    out = capsys.readouterr().out
    assert "run complete" in out and "replay_matches" in out


def test_guard_report_clean_catches_real_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The precise report guard raises only when the ACTUAL endpoint key
    appears in the HTML, not on plotly.js library boilerplate."""
    monkeypatch.setenv("VERDI_VLLM_API_KEY", "sk-secret-endpoint-abc12345")
    with pytest.raises(AssertionError, match="report.html would contain"):
        live_module._guard_report_clean("<div>leaked sk-secret-endpoint-abc12345 here</div>")
    # plotly.js boilerplate (the false-positive triggers) must NOT raise
    live_module._guard_report_clean("mask-position:50%; tiles.../x/{z}.png?api_key=")
    # no key configured -> guard is a no-op (offline / test context)
    monkeypatch.delenv("VERDI_VLLM_API_KEY", raising=False)
    live_module._guard_report_clean("anything at all")


def test_no_report_flag_skips_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """--no-report suppresses the auto-generated report.html."""
    transport = _mock_transport()
    real_client = ModelClient

    def factory(config: ModelEndpointConfig, **kwargs: Any) -> ModelClient:
        kwargs["transport"] = transport
        return real_client(config, **kwargs)

    monkeypatch.setattr(live_module, "ModelClient", factory)
    run_dir = tmp_path / "live-run"
    code = live_module.main(["--scenario", str(SCENARIO), "--run-dir", str(run_dir),
                             "--concurrency", "2", "--no-report"])
    assert code == 0
    assert not (run_dir / "report.html").exists()


def test_export_html_report_never_raises(tmp_path: Path):
    """Report generation is a convenience step: a broken/absent run must
    return a SKIPPED line, never raise into the run flow."""
    (tmp_path / "events.jsonl").write_text("")
    (tmp_path / "run_meta.json").write_text('{"status": "complete"}')
    result = live_module._export_html_report(tmp_path)
    assert isinstance(result, str) and result.startswith("report:")
    assert "SKIPPED" in result, "a run missing required artifacts should be skipped, not crash the run"


def test_main_fails_cleanly_without_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("VERDI_VLLM_BASE_URL", raising=False)
    monkeypatch.delenv("VERDI_VLLM_MODEL", raising=False)
    monkeypatch.setenv("VERDI_DOTENV_PATH", str(tmp_path / "does-not-exist.env"))
    with pytest.raises(SystemExit) as excinfo:
        live_module.main(["--scenario", str(SCENARIO), "--run-dir", str(tmp_path)])
    assert "not configured" in str(excinfo.value)


def test_stall_watchdog_writes_heartbeat_and_survives_progress(tmp_path: Path):
    """Heartbeat is written on an interval, and progress resets the deadline
    (no stall recorded while events keep growing)."""
    import asyncio
    import time as _t

    (tmp_path / "events.jsonl").write_text("")
    live_module._HEARTBEAT_INTERVAL_SECONDS = 0.05  # fast for test

    async def scenario():
        watchdog = asyncio.create_task(live_module._stall_watchdog(tmp_path, 0.2))
        deadline = _t.monotonic() + 0.5
        with (tmp_path / "events.jsonl").open("a") as fh:
            while _t.monotonic() < deadline:
                fh.write('{"kind": "action_accepted"}\n')
                fh.flush()
                await asyncio.sleep(0.05)
        watchdog.cancel()
    asyncio.run(scenario())

    meta = json.loads((tmp_path / "run_meta.json").read_text())
    assert "heartbeat_utc" in meta, "heartbeat never written"
    assert meta["events_logged"] > 0
    assert meta.get("status") != "stalled"


def test_stall_watchdog_aborts_on_hung_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """No event growth past the window -> stall.json in run_meta and hard exit
    code 3 (the Tranche 12a hung-socket recovery path)."""
    import asyncio

    (tmp_path / "events.jsonl").write_text('{"kind": "action_accepted"}\n')
    monkeypatch.setattr(live_module, "_HEARTBEAT_INTERVAL_SECONDS", 0.05)
    exited = {}

    def fake_exit(code: int) -> None:
        exited["code"] = code
        raise SystemExit(code)

    monkeypatch.setattr("os._exit", fake_exit)

    async def scenario():
        watchdog = asyncio.create_task(live_module._stall_watchdog(tmp_path, 0.2))
        try:
            await asyncio.sleep(0.5)
        finally:
            watchdog.cancel()

    with pytest.raises(SystemExit):
        asyncio.run(scenario())
    assert exited.get("code") == 3, f"expected exit code 3, got {exited}"
    meta = json.loads((tmp_path / "run_meta.json").read_text())
    assert meta["status"] == "stalled"
    assert meta["events_logged"] == 1
