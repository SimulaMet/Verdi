"""Offline tests for verdi.experiments.g5_judge_live (ADR-014 §3/§6).

All network is mocked via :class:`MockJudgeClient` (ADR-012
``_RecordingJudgeClient`` pattern). No network, no .env reads.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from tests.experiments.conftest import make_mini_run_root

from verdi.experiments.g5_close import build_sample_manifest
from verdi.experiments.g5_judge_live import (
    JUDGE_SYSTEM_PROMPT,
    MockJudgeClient,
    engine_outcome_for_row,
    parse_g5_judge_verdict,
    run_g5_judge,
)


def _ok_verdict(stance: str = "agree") -> str:
    return json.dumps(
        {"stance": stance, "rationale": "conforms to the task", "confidence": "high"}
    )


@pytest.fixture()
def mini_run_root(tmp_path):
    return make_mini_run_root(tmp_path / "mini_run")


# --------------------------------------------------------------------------- #
# Verdict parsing (frozen schema; quarantine, never drop)
# --------------------------------------------------------------------------- #


def test_parse_verdict_round_trips():
    parsed = parse_g5_judge_verdict(_ok_verdict())
    assert parsed["quarantined"] is False
    assert parsed["verdict"] == {
        "stance": "agree",
        "rationale": "conforms to the task",
        "confidence": "high",
    }
    # tolerant: leading spaces / fence
    assert parse_g5_judge_verdict(f'   ```json\n{_ok_verdict("disagree")}\n```')["verdict"]["stance"] == "disagree"
    # rationale is capped at 400 chars
    long = json.dumps(
        {"stance": "agree", "rationale": "x" * 500, "confidence": "low"}
    )
    assert len(parse_g5_judge_verdict(long)["verdict"]["rationale"]) == 400


@pytest.mark.parametrize(
    "raw",
    [
        "{not json",
        '{"stance": "maybe", "rationale": "x", "confidence": "high"}',
        '{"stance": "agree", "rationale": "x", "confidence": "certain"}',
        "",
        "[1, 2, 3]",
    ],
)
def test_parse_verdict_quarantines_bad_payload(raw):
    parsed = parse_g5_judge_verdict(raw)
    assert parsed["quarantined"] is True
    assert parsed["verdict"] is None
    assert parsed["raw"] == raw


# --------------------------------------------------------------------------- #
# Engine outcome mapping (shown to the judge)
# --------------------------------------------------------------------------- #


def test_engine_outcome_for_row_maps_accepted(mini_run_root):
    outcome = engine_outcome_for_row(mini_run_root, 0, 0)
    assert outcome["decision"] == "accepted"
    assert outcome["event_id"] == 0
    assert outcome["violations"] == []


# --------------------------------------------------------------------------- #
# run_g5_judge with a recording/mock client (offline replay)
# --------------------------------------------------------------------------- #


def _mock_judge(tmp_path, root: Path, *, smoke: bool = True) -> tuple:
    responses = [_ok_verdict()]
    if smoke:
        responses = [_ok_verdict()] + [_ok_verdict()] * 4
    client = MockJudgeClient(responses)
    out_dir = root.parent / "judge_out"
    run = run_g5_judge(
        root, judge_client=client, out_dir=out_dir,
        smoke_first=smoke, hard_call_cap=700, expected_rows=4,
    )
    return client, out_dir, run


def test_run_g5_judge_mock_tape_and_meta(mini_run_root):
    client, out_dir, run = _mock_judge(mini_run_root, mini_run_root)
    # 1 smoke + 4 sample rows (pair_0: 3 rows, pair_1: 1 suffix row).
    assert run["n_calls"] == 5
    assert len(run["verdicts"]) == 4
    assert run["smoke"] is not None
    assert run["smoke"]["parsed"]["verdict"]["stance"] == "agree"

    tape = [
        json.loads(line)
        for line in (out_dir / "judge_tape.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert len(tape) == 5
    assert [t["kind"] for t in tape] == ["smoke", "sample", "sample", "sample", "sample"]
    sample_rows = [t for t in tape if t["kind"] == "sample"]
    # one tape row per sealed sample row, verbatim prompt/response/model/usage
    assert {t["row_id"] for t in sample_rows} == {
        "pair_0:seq_0",
        "pair_0:seq_1",
        "pair_0:seq_2",
        "pair_1:seq_2",
    }
    first = sample_rows[0]
    assert first["prompt"][0]["content"] == JUDGE_SYSTEM_PROMPT
    # judge is SHOWN the engine outcome (ADR-014 §3 disclosure)
    assert "engine_outcome: decision=accepted" in first["prompt"][1]["content"]
    assert "MODEL RESPONSE (sealed, verbatim)" in first["prompt"][1]["content"]
    assert first["model"] == "mock-judge"
    assert first["response_content"]
    assert first["usage"] == {"total_tokens": 0}
    assert first["row"]["row_sha256"]

    meta = json.loads((out_dir / "g5judge_run_meta.json").read_text())
    manifest = build_sample_manifest(mini_run_root)
    assert meta["n_calls"] == 5
    assert meta["cap"] == 700
    assert meta["smoke_calls"] == 1
    assert meta["sample_calls"] == 4
    assert meta["rows_quarantined"] == 0
    assert meta["sample_manifest_sha256"] == manifest["manifest_sha256"]
    assert meta["concurrency"] == 1
    assert meta["advisor_only"] is True
    assert meta["git_sha"]


def test_run_g5_judge_quarantine_on_bad_json(mini_run_root):
    # smoke ok, rows 0..1 ok, rows 2..3 bad JSON -> quarantined, audited.
    responses = [_ok_verdict(), _ok_verdict(), _ok_verdict(), "{nope", "{nope"]
    client = MockJudgeClient(responses)
    out_dir = mini_run_root.parent / "judge_out"
    run = run_g5_judge(
        mini_run_root, judge_client=client, out_dir=out_dir,
        smoke_first=True, hard_call_cap=700, expected_rows=4,
    )
    by_id = {v["row_id"]: v for v in run["verdicts"]}
    assert by_id["pair_0:seq_2"]["quarantined"] is True
    assert by_id["pair_0:seq_2"]["verdict"] is None
    assert by_id["pair_0:seq_2"]["raw"] == "{nope"
    assert by_id["pair_1:seq_2"]["quarantined"] is True
    assert by_id["pair_0:seq_0"]["quarantined"] is False
    assert by_id["pair_0:seq_0"]["verdict"]["stance"] == "agree"
    assert run["n_calls"] == 5
    meta = json.loads((out_dir / "g5judge_run_meta.json").read_text())
    assert meta["rows_quarantined"] == 2


def test_run_g5_judge_cap_abort(mini_run_root):
    with pytest.raises(Exception, match="hard call cap"):
        run_g5_judge(
            mini_run_root,
            judge_client=MockJudgeClient(),
            out_dir=mini_run_root.parent / "judge_out",
            smoke_first=True,
            hard_call_cap=2,  # planned 5 > 2
            expected_rows=4,
        )


def test_run_g5_judge_cap_abort_records_meta(mini_run_root):
    # Reviewer MED-4 / F6: on cap breach the run meta is written best-effort
    # with status "cap_aborted" BEFORE the abort raises.
    out_dir = mini_run_root.parent / "judge_out_cap"
    with pytest.raises(Exception, match="hard call cap"):
        run_g5_judge(
            mini_run_root,
            judge_client=MockJudgeClient(),
            out_dir=out_dir,
            smoke_first=True,
            hard_call_cap=2,  # planned 5 > 2
            expected_rows=4,
        )
    meta = json.loads((out_dir / "g5judge_run_meta.json").read_text())
    assert meta["status"] == "cap_aborted"
    assert meta["hard_call_cap"] == 2
    assert meta["n_calls"] == 0
    assert "timestamp_utc" in meta


def test_mock_client_max_calls_enforced():
    # Reviewer MED-3: MockJudgeClient.max_calls is the tripwire that would
    # have caught a client-internal double-call. The 1st call succeeds;
    # the 2nd raises.
    import asyncio

    client = MockJudgeClient(max_calls=1)

    async def _two_calls():
        await client.generate_structured([{"role": "user", "content": "1"}])
        try:
            await client.generate_structured([{"role": "user", "content": "2"}])
        except Exception as exc:  # noqa: BLE001 - the asserted tripwire
            return exc
        return None

    exc = asyncio.run(_two_calls())
    assert isinstance(exc, Exception)
    assert "mock judge call cap" in str(exc)
    assert len(client.calls) == 2


def test_endpoint_config_zero_retries(monkeypatch):
    # Reviewer HIGH-1: the effective endpoint config MUST carry
    # max_retries=0 so client-internal retries cannot blow the hard cap.
    from verdi.config import ModelEndpointConfig
    from verdi.experiments.g5_judge_live import _resolve_endpoint_config

    monkeypatch.setenv("VERDI_DOTENV_PATH", "/nonexistent/.env")
    monkeypatch.setenv("VERDI_VLLM_BASE_URL", "http://localhost:9999/v1")
    monkeypatch.setenv("VERDI_VLLM_MODEL", "Qwen3.8-27B")
    monkeypatch.setenv("VERDI_VLLM_API_KEY", "sk-testfake123")
    monkeypatch.setenv("VERDI_MAX_RETRIES", "5")  # default that would leak in

    config = _resolve_endpoint_config()
    assert isinstance(config, ModelEndpointConfig)
    assert config.max_retries == 0


def test_run_g5_judge_sample_count_assertion(mini_run_root):
    # The mini run has 4 sample rows, not the frozen production 504: the
    # default assert fires with a clear message (run_g5_judge is built for
    # the sealed production sample).
    from verdi.experiments.g5_judge_live import G5JudgeError

    with pytest.raises(G5JudgeError, match="expected 504"):
        run_g5_judge(
            mini_run_root,
            judge_client=MockJudgeClient(),
            out_dir=mini_run_root.parent / "judge_out",
            smoke_first=False,
        )


def test_load_dotenv_handles_export_prefix(tmp_path, monkeypatch):
    """The program .env uses `export KEY="value"`; parser must accept it."""
    fake_key = "sk-" + "test" + "-" + "fake123"  # built from parts; not a real key
    env_file = tmp_path / ".env"
    env_file.write_text(
        "export VERDI_TEST_BASE_URL=\"https://example/api/v1\"\n"
        "export VERDI_TEST_MODEL=\"Model-1\"\n"
        f'export VERDI_TEST_API_KEY="{fake_key}"\n'
        "# comment line\n"
    )
    monkeypatch.delenv("VERDI_TEST_BASE_URL", raising=False)
    monkeypatch.setenv("VERDI_DOTENV_PATH", str(env_file))
    from verdi.experiments.g5_judge_live import _load_dotenv_into_environ

    _load_dotenv_into_environ()
    assert os.environ["VERDI_TEST_BASE_URL"] == "https://example/api/v1"
    assert os.environ["VERDI_TEST_MODEL"] == "Model-1"
    assert os.environ["VERDI_TEST_API_KEY"] == fake_key
