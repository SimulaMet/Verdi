"""Offline tests for the ADR-012 live judge pass (synthetic tape + ledger).

Fully offline: no network, no real sleeps. The judge model is faked with a
recorded ``acompletion`` double (including one unparseable response and one
disagreement row) exercising:

- sample construction (all rows in-sample, sole DISPATCH rejection
  in-sample, determinism across re-reads);
- judge-tape capture (verbatim rows, no key material in any byte written);
- deterministic report byte-stability (two builds identical);
- advisor-only invariant (``build_judge_report`` takes only plain data —
  tape rows + judgment dicts — no world state / ledger / scorer objects).
"""
from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path

import pytest

import verdi_agents.audit.judge_live as jl
from verdi_agents.audit.judge_live import (
    JUDGE_SYSTEM_PROMPT,
    SAMPLE_SEED,
    JudgeTapeCapture,
    agreement_ci,
    build_judge_report,
    build_judge_rows,
    build_judge_sample,
    finalize_judge_run,
    parse_judge_verdict,
    run_live_judge,
    wilson_ci_95,
    write_judge_report,
)
from verdi_agents.audit.live_bridge import (
    LiveLedgerError,
    correlate_tape_to_ledger,
    load_ledger_events,
)
from verdi_agents.schemas import WorldEvent

FAKE_KEY = "sk-abc1234567890testkey"
FAKE_URL = "https://judge.example.invalid/api/v1"
MODEL = "qwen-fake-27b"

# ---------------------------------------------------------------------------
# Synthetic fixtures (same shape as the real L1 tape / ledger)
# ---------------------------------------------------------------------------


def _event(
    event_id: int,
    *,
    actor_id: str,
    task_id: str,
    kind: str = "action_accepted",
    tick: int = 1,
    decision: str = "accepted",
    violations: list[str] | None = None,
) -> WorldEvent:
    payload: dict = {"action_kind": "transport", "task_id": task_id}
    if violations:
        payload["attempted_violation_codes"] = violations
    return WorldEvent(
        schema_version=1,
        event_id=event_id,
        tick=tick,
        cause_event_id=None,
        actor_id=actor_id,
        kind=kind,
        payload=payload,
        before_hash=f"before-{event_id}",
        after_hash=f"after-{event_id}",
        decision=decision,
        prev_hash=f"prev-{event_id}",
        event_hash=f"hash-{event_id}",
    )


def _rec(
    seq: int,
    *,
    actor_id: str,
    task_id: str,
    tick: int = 1,
    action_kind: str = "transport",
    quarantined: bool = False,
    response_raw: str | None = None,
) -> dict:
    if response_raw is None:
        response_raw = json.dumps(
            {"action_kind": action_kind, "arguments": {}, "reason": "accept"}
        )
    return {
        "seq": seq,
        "actor_id": actor_id,
        "task_id": task_id,
        "lifecycle_tick": tick,
        "trace_id": f"trace-{seq}",
        "model": MODEL,
        "request_id": f"req-{seq}",
        "prompt": (
            f"[prompt:verdi.agent-policy-prompt/1]\nYou are agent \"{actor_id}\" "
            f"in a simulated society at tick {tick}.\nRole: engineer\n"
            f"Persona: builds bridges\n"
            f"Task {task_id} (action_type={action_kind}):\n{{}}\n"
        ),
        "response_raw": response_raw,
        "parsed_action": (
            None
            if quarantined
            else {"action_kind": action_kind, "arguments": {}, "reason": "accept"}
        ),
        "quarantined": quarantined,
        "usage": {"total_tokens": 10 + seq},
        "timestamp_utc": "2026-10-10T00:00:00+00:00",
    }


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


@pytest.fixture()
def judge_tape_ledger(tmp_path):
    """6 dispatches; the sole engine DISPATCH rejection is event 42 (tick 11) —
    mirroring the L1 run's shape in miniature."""
    events = [
        _event(0, actor_id="a-x", task_id="task0", kind="task_assigned", decision="rejected"),
        _event(2, actor_id="a-1", task_id="t1", tick=1),
        _event(5, actor_id="a-2", task_id="t2", tick=2),
        _event(9, actor_id="a-1", task_id="t3", tick=3),
        _event(42, actor_id="a-3", task_id="t4", tick=11, kind="action_rejected",
               decision="rejected", violations=["quota_exceeded"]),
        _event(43, actor_id="a-4", task_id="t5", tick=11),
        _event(60, actor_id="a-5", task_id="t6", tick=12),
    ]
    tape = [
        _rec(0, actor_id="a-4", task_id="t5", tick=11),
        _rec(1, actor_id="a-1", task_id="t1", tick=1),
        _rec(2, actor_id="a-2", task_id="t2", tick=2),
        _rec(3, actor_id="a-3", task_id="t4", tick=11),
        _rec(4, actor_id="a-1", task_id="t3", tick=3),
        _rec(5, actor_id="a-5", task_id="t6", tick=12),
    ]
    tape_path = tmp_path / "tape.jsonl"
    ledger_path = tmp_path / "event_log.jsonl"
    _write(tape_path, tape)
    _write(ledger_path, [e.model_dump() for e in events])
    return tape_path, ledger_path, events


# ---------------------------------------------------------------------------
# Fake judge client (recorded responses; counts calls; no network)
# ---------------------------------------------------------------------------


class _FakeMessage:
    def __init__(self, content: str, reasoning: str = "") -> None:
        self.content = content
        self.reasoning_content = reasoning


class _FakeChoice:
    def __init__(self, content: str, reasoning: str = "") -> None:
        self.message = _FakeMessage(content, reasoning)


class _FakeUsage:
    def model_dump(self) -> dict:
        return {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "completion_tokens_details": {"reasoning_tokens": 12, "text_tokens": 8},
        }


class _FakeResponse:
    def __init__(self, content: str, reasoning: str = "") -> None:
        self.choices = [_FakeChoice(content, reasoning)]
        self.usage = _FakeUsage()


class FakeJudgeClient:
    """Recorded judge double: per-event responses, call counting, one
    unparseable + one disagreement row, and endpoint-failure simulation."""

    def __init__(self, responses: dict[int, list[str]]) -> None:
        self._responses = responses  # event_id -> response sequence
        self.calls: list[dict] = []

    async def acompletion(self, **kwargs):
        meta = kwargs.get("metadata") or {}
        event_id = int(meta.get("sealed_event_id") or 0)
        seq = self.calls.count(event_id)
        self.calls.append(event_id)
        queue = self._responses[event_id]
        content = queue[seq] if seq < len(queue) else queue[-1]
        if content.startswith("!ERROR!"):
            raise RuntimeError(content[len("!ERROR!"):])
        return _FakeResponse(content, reasoning=f"reasoning for event {event_id} #{seq}")


def _make_auditor_spy(monkeypatch, fake: FakeJudgeClient) -> None:
    """Patch _build_auditor so no real ModelAuditor/AnyLLM is constructed."""

    class _SpyAuditor:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self._target = None

        def set_target(self, target) -> None:
            self._target = target

        async def run_async(self, scenarios, **kwargs):
            sc = scenarios[0]
            # The real run_async merges judge_params into the judge call's
            # kwargs (metadata + sampling params) — the spy does the same.
            jp = kwargs.get("judge_params") or {}
            meta = jp.get("metadata") or sc["metadata"]
            assert int(meta["sealed_event_id"]) >= 0
            # Verify the sealed target replays the captured response verbatim.
            assert self._target is not None
            resp = await self._target.send(
                system=None, user=sc["test_prompt"], history=[], context=None
            )
            assert resp.raw == {"sealed_replay": True}
            # Route through the capture-wrapped client (set in _spy_factory)
            # so the judge tape records every call verbatim, like the real
            # ModelAuditor does with its judge client.
            response = await self.judge_client.acompletion(
                metadata=meta,
                model=MODEL,
                messages=[
                    {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                    {"role": "user", "content": sc["test_prompt"]},
                ],
                temperature=jp.get("temperature", 0.1),
                max_tokens=jp.get("max_tokens", 1024),
                stream=False,
            )
            # ModelAuditor's own parse path (same code the real path uses).
            content = response.choices[0].message.content
            judgment = {"severity": "pass", "summary": "ok"}
            if content.startswith("ERROR:"):
                judgment = {"severity": "ERROR"}

            class _Result:
                def __init__(self) -> None:
                    self.severity = judgment["severity"]
                    self.summary = judgment.get("summary", "error")

            class _Results:
                def __init__(self) -> None:
                    self.results = [_Result()]

            return _Results()

    def _spy_factory(**kw):
        # The spy mirrors the real _build_auditor: same sealed target and
        # capture-wrapped judge client (set here because the monkeypatch
        # replaces the whole function).
        spy = _SpyAuditor(**kw)
        spy.set_target(jl.SealedTapeTarget(kw["replay"]))
        spy.judge_client = kw["capture"].wrap(fake)
        return spy

    monkeypatch.setattr(jl, "_build_auditor", _spy_factory)


# ---------------------------------------------------------------------------
# (1) sample construction: all rows, rejection in-sample, deterministic
# ---------------------------------------------------------------------------


def test_sample_includes_all_rows_and_the_rejection(judge_tape_ledger) -> None:
    tape_path, ledger_path, _ = judge_tape_ledger
    sample = build_judge_sample(tape_path, ledger_path)
    assert len(sample) == 6
    assert {s["event_id"] for s in sample} == {2, 5, 9, 42, 43, 60}
    # The sole engine DISPATCH rejection is in the sample (stratification guarantee).
    rejected = [s for s in sample if s["engine_rejection"]]
    assert [s["event_id"] for s in rejected] == [42]
    # Sorted by (tick, actor_id, task_id).
    key = [(s["tick"], s["actor_id"], s["task_id"]) for s in sample]
    assert key == sorted(key)
    # Sealed event_id + hash carried (correlation is never positional).
    assert all(s["event_hash"] for s in sample)


def test_sample_is_deterministic_across_rebuilds(judge_tape_ledger) -> None:
    tape_path, ledger_path, _ = judge_tape_ledger
    s1 = build_judge_sample(tape_path, ledger_path)
    s2 = build_judge_sample(tape_path, ledger_path)
    assert s1 == s2
    assert SAMPLE_SEED


def test_sample_missing_rejection_row_is_an_error(judge_tape_ledger) -> None:
    tape_path, ledger_path, _ = judge_tape_ledger
    # Drop the tape record for event 42: the engine rejection stays in the
    # ledger but is no longer in the sample, so the stratification guard
    # raises (no silent fallback to a smaller sample).
    tape = [json.loads(line) for line in tape_path.read_text().splitlines() if line.strip()]
    shrunk = [r for r in tape if r["task_id"] != "t4"]
    p2 = tape_path.parent / "tape2.jsonl"
    _write(p2, shrunk)
    with pytest.raises(LiveLedgerError, match="engine-rejection row"):
        build_judge_sample(p2, ledger_path)


# ---------------------------------------------------------------------------
# (2) parse_judge_verdict: yes/no/quarantine
# ---------------------------------------------------------------------------


def test_parse_judge_verdict_variants() -> None:
    ok = parse_judge_verdict('{"compliant": "yes", "reason": "fine"}')
    assert ok == {"quarantined": False, "verdict": "yes", "reason": "fine",
                  "raw": '{"compliant": "yes", "reason": "fine"}'}
    fenced = parse_judge_verdict('```json\n{"compliant": "no", "reason": "bad"}\n```')
    assert fenced["quarantined"] is False and fenced["verdict"] == "no"
    prose = parse_judge_verdict("I think {\"compliant\": \"yes\"} because...")
    assert prose["verdict"] == "yes"
    garbage = parse_judge_verdict("not json at all")
    assert garbage["quarantined"] is True and garbage["verdict"] is None
    wrong = parse_judge_verdict('{"compliant": "maybe", "reason": "r"}')
    assert wrong["quarantined"] is True and wrong["verdict"] is None


# ---------------------------------------------------------------------------
# (3) full run with fake judge: capture, quarantine, disagreement, budget
# ---------------------------------------------------------------------------


def test_run_captures_verbatim_and_no_key_in_bytes(
    judge_tape_ledger, tmp_path, monkeypatch
) -> None:
    # Responses: event 9 unparseable once then valid (quarantine->retry),
    # event 42 (engine REJECTED) judged "yes" -> DISAGREEMENT row,
    # the rest judged in agreement with the engine.
    responses = {
        2: ['{"compliant": "yes", "reason": "accepted and well-formed"}'],
        5: ['{"compliant": "yes", "reason": "ok"}'],
        9: ["garbage not json", '{"compliant": "yes", "reason": "ok"}'],
        42: ['{"compliant": "yes", "reason": "looks fine to me"}'],
        43: ['{"compliant": "yes", "reason": "ok"}'],
        60: ['{"compliant": "yes", "reason": "ok"}'],
    }
    fake = FakeJudgeClient(responses)
    _make_auditor_spy(monkeypatch, fake)
    tape_path, ledger_path, _ = judge_tape_ledger
    out_dir = tmp_path / "judge" / "20261010T000000Z"
    monkeypatch.setattr(jl, "_sleep_backoff", _fake_sleep)

    run = run_live_judge(
        tape_path,
        ledger_path,
        out_dir,
        base_url=FAKE_URL,
        api_key=FAKE_KEY,
        model=MODEL,
        judge_model=MODEL,
        max_calls=96,
    )
    # 6 rows + 1 retry (event 9) = 7 calls.
    assert fake.calls.count(9) == 2
    assert run["meta"]["judge_calls_used"] == 7
    assert run["meta"]["initial_judge_calls"] == 6
    assert run["meta"]["retry_judge_calls"] == 1
    assert run["meta"]["same_model_judge"] is True
    assert run["meta"]["judge_is_advisor"] is True

    # Verbatim capture: reasoning_content + usage + correlation present.
    row9 = [r for r in run["judge_tape"] if r["correlation"]["sealed_event_id"] == 9]
    assert [r["attempt"] for r in row9] == [1, 2]
    assert "reasoning for event 9 #1" in row9[1]["reasoning_content"]
    assert row9[1]["usage"]["completion_tokens_details"]["reasoning_tokens"] == 12
    assert row9[1]["endpoint"] == FAKE_URL
    assert row9[1]["quarantined"] is False

    # NO key material in any bytes written to disk.
    for f in sorted(out_dir.iterdir()):
        assert FAKE_KEY not in f.read_text(), f"key leaked into {f.name}"
    meta = json.loads((out_dir / "judge_run_meta.json").read_text())
    assert "api_key" not in meta and "authorization" not in meta

    # Quarantined judge responses audited, not dropped.
    by_event = {v["event_id"]: v for v in run["verdicts"]}
    assert by_event[9]["judge_status"] == "judged"  # retry succeeded
    assert by_event[42]["judge_status"] == "judged"
    assert by_event[42]["judge_verdict"] == "yes"  # disagrees with engine (no)


async def _fake_sleep():  # no real sleep
    return None


def test_persistent_failure_is_audited_not_dropped(
    judge_tape_ledger, tmp_path, monkeypatch
) -> None:
    responses = {
        2: ['{"compliant": "yes", "reason": "ok"}'],
        5: ['{"compliant": "yes", "reason": "ok"}'],
        9: ["!ERROR!HTTP 503"],  # fails both attempts
        42: ['{"compliant": "no", "reason": "violation"}'],
        43: ['{"compliant": "yes", "reason": "ok"}'],
        60: ['{"compliant": "yes", "reason": "ok"}'],
    }
    fake = FakeJudgeClient(responses)
    _make_auditor_spy(monkeypatch, fake)
    tape_path, ledger_path, _ = judge_tape_ledger
    out_dir = tmp_path / "judge" / "20261010T000000Z"
    monkeypatch.setattr(jl, "_sleep_backoff", _fake_sleep)
    run = run_live_judge(
        tape_path, ledger_path, out_dir,
        base_url=FAKE_URL, api_key=FAKE_KEY, model=MODEL, judge_model=MODEL,
        max_calls=96,
    )
    by_event = {v["event_id"]: v for v in run["verdicts"]}
    assert by_event[9]["judge_status"] == "judge_error"
    assert by_event[9]["judge_quarantined"] is True
    assert "HTTP 503" in by_event[9]["judge_reason"]
    # The failed calls are in the judge tape (audited).
    failed = [r for r in run["judge_tape"] if r["correlation"]["sealed_event_id"] == 9]
    assert len(failed) == 2 and all(r["quarantined"] for r in failed)
    # Other rows still judged.
    assert by_event[42]["judge_status"] == "judged"


def test_budget_cap_records_remaining_rows(
    judge_tape_ledger, tmp_path, monkeypatch
) -> None:
    responses = {
        e: ['{"compliant": "yes", "reason": "ok"}'] for e in (2, 5, 9, 42, 43, 60)
    }
    fake = FakeJudgeClient(responses)
    _make_auditor_spy(monkeypatch, fake)
    tape_path, ledger_path, _ = judge_tape_ledger
    out_dir = tmp_path / "judge" / "20261010T000000Z"
    monkeypatch.setattr(jl, "_sleep_backoff", _fake_sleep)
    run = run_live_judge(
        tape_path, ledger_path, out_dir,
        base_url=FAKE_URL, api_key=FAKE_KEY, model=MODEL, judge_model=MODEL,
        max_calls=3,  # budget exhausted after 3 rows
    )
    assert run["meta"]["judge_calls_used"] == 3
    over = [v for v in run["verdicts"] if v["judge_status"] == "budget_exceeded"]
    assert len(over) == 3
    # Budget rows are captured in the tape as audited failures.
    budget_rows = [
        r for r in run["judge_tape"] if "budget_exceeded" in (r["error"] or "")
    ]
    assert len(budget_rows) == 3


def test_max_tokens_floor_enforced(judge_tape_ledger, tmp_path) -> None:
    tape_path, ledger_path, _ = judge_tape_ledger
    with pytest.raises(ValueError, match="max_tokens"):
        run_live_judge(
            tape_path, ledger_path, tmp_path / "j",
            base_url=FAKE_URL, api_key=FAKE_KEY, model=MODEL, judge_model=MODEL,
            max_tokens=512,
        )


# ---------------------------------------------------------------------------
# (4) report: pure function, byte-stable, disagreement enumerated
# ---------------------------------------------------------------------------


def _run_and_report(judge_tape_ledger, tmp_path, monkeypatch, responses):
    fake = FakeJudgeClient(responses)
    _make_auditor_spy(monkeypatch, fake)
    tape_path, ledger_path, _ = judge_tape_ledger
    out_dir = tmp_path / "judge" / "20261010T000000Z"
    monkeypatch.setattr(jl, "_sleep_backoff", _fake_sleep)
    run = run_live_judge(
        tape_path, ledger_path, out_dir,
        base_url=FAKE_URL, api_key=FAKE_KEY, model=MODEL, judge_model=MODEL,
        max_calls=96,
    )
    report_path = tmp_path / "report.json"
    report = finalize_judge_run(tape_path, ledger_path, out_dir, run, report_path)
    return run, report, report_path


def test_report_disagreement_enumerated_and_rates(
    judge_tape_ledger, tmp_path, monkeypatch
) -> None:
    responses = {
        2: ['{"compliant": "yes", "reason": "ok"}'],
        5: ['{"compliant": "no", "reason": "the proposal is malformed"}'],
        9: ['{"compliant": "yes", "reason": "ok"}'],
        42: ['{"compliant": "yes", "reason": "looks fine to me"}'],
        43: ['{"compliant": "yes", "reason": "ok"}'],
        60: ['{"compliant": "yes", "reason": "ok"}'],
    }
    _, report, _ = _run_and_report(judge_tape_ledger, tmp_path, monkeypatch, responses)
    s = report["summary"]
    assert s["judged_rows"] == 6
    # Disagreements: event 42 (judge yes vs engine no) and event 5
    # (judge no vs engine yes).
    assert s["disagreement_count"] == 2
    assert sorted(d["event_id"] for d in s["disagreements"]) == [5, 42]
    assert s["agreement_rate"] == pytest.approx(4 / 6)
    d42 = next(d for d in s["disagreements"] if d["event_id"] == 42)
    assert d42["engine_decision"] == "rejected"
    assert d42["judge_stance"] == "yes"
    assert d42["engine_stance"] == "no"
    # Accepted vs rejected breakdown judged separately.
    assert s["accepted_breakdown"]["rows"] == 5
    assert s["rejected_breakdown"]["rows"] == 1
    assert s["rejected_breakdown"]["disagreements"] == 1
    # Per-row fields.
    rows42 = {r["event_id"]: r for r in report["rows"]}
    r42 = rows42[42]
    assert r42["agreement"] is False
    assert r42["judge_verdict"]["stance"] == "yes"
    assert "model-judge" in r42["disagreement_reason"]
    # Mandatory same-family self-judgment disclosure present (same model).
    assert any("SAME-MODEL SELF-JUDGMENT" in x for x in report["limitations"])
    # A1 B2: unblinded judge-to-engine self-consistency framing, stated.
    assert any("UNBLINDED JUDGE" in x and "SELF-CONSISTENCY" in x for x in report["limitations"])
    assert any("SHOWN the deterministic engine" in x for x in report["limitations"])
    assert any("CODE judge" in x and "blinding" in x for x in report["limitations"])
    # A1 m2: the agreement metric definition is in the report.
    assert report["agreement_metric"].startswith("agreement = (judge_stance == engine_stance)")
    assert any("AGREEMENT METRIC DEFINITION" in x for x in report["limitations"])
    # A1 M2: overall + per-class CIs with counts; n=1 class flagged.
    assert s["agreement_wilson_ci_95"] == [v for v in wilson_ci_95(4, 6)]
    assert s["accepted_breakdown"]["rows"] == 5
    assert s["accepted_breakdown"]["agreements"] == 4
    assert s["accepted_breakdown"]["rate"] == pytest.approx(4 / 5)
    assert s["accepted_breakdown"]["wilson_ci_95"] == [v for v in wilson_ci_95(4, 5)]
    assert s["accepted_breakdown"]["single_row_class_flag"] is None
    assert s["rejected_breakdown"]["rows"] == 1
    assert s["rejected_breakdown"]["agreements"] == 0
    assert s["rejected_breakdown"]["wilson_ci_95"] == [v for v in wilson_ci_95(0, 1)]
    assert "n=1" in s["rejected_breakdown"]["single_row_class_flag"]
    assert report["judge_is_advisor"] is True
    assert report["sample"]["rejection_rows_in_sample"] == [42]
    assert report["sample"]["seed"] == SAMPLE_SEED
    assert report["simpleaudit_usage"]["mode"].startswith("simpleaudit.model_auditor")
    # Endpoints without keys.
    assert FAKE_KEY not in json.dumps(report)


def test_report_bytes_stable_two_builds(judge_tape_ledger, tmp_path, monkeypatch) -> None:
    """Two judge runs with identical recorded verdicts (fake pipeline) must
    produce byte-identical reports: the report is a pure function over
    (sample, code_rows, verdicts, meta, correlation) — judge-tape
    timestamps are never carried into it."""
    responses = {
        e: ['{"compliant": "yes", "reason": "ok"}'] for e in (2, 5, 9, 42, 43, 60)
    }
    r1 = tmp_path / "case1"
    r2 = tmp_path / "case2"
    _run_and_report(judge_tape_ledger, r1, monkeypatch, responses)
    _run_and_report(judge_tape_ledger, r2, monkeypatch, responses)

    tape = [
        json.loads(line)
        for line in judge_tape_ledger[0].read_text().splitlines()
        if line.strip()
    ]
    events = load_ledger_events(judge_tape_ledger[1])
    sample = build_judge_sample(judge_tape_ledger[0], judge_tape_ledger[1])
    code_rows = build_judge_rows(sample, tape, events)
    mapping = correlate_tape_to_ledger(tape, events)
    ra = build_judge_report(sample, code_rows, _verdicts_of(r1), _meta_of(r1), mapping)
    rb = build_judge_report(sample, code_rows, _verdicts_of(r2), _meta_of(r2), mapping)
    pa = r1 / "pure_a.json"
    pb = r1 / "pure_b.json"
    write_judge_report(ra, pa)
    write_judge_report(rb, pb)
    assert pa.read_bytes() == pb.read_bytes()
    top = json.loads(pa.read_text())
    assert list(top) == sorted(top)
    # Two finalize_judge_report builds from the SAME run record are also
    # byte-identical (regeneration without re-judging).
    run, _, _ = _run_and_report(judge_tape_ledger, r2, monkeypatch, responses)
    p_c = r2 / "finalize_1.json"
    p_d = r2 / "finalize_2.json"
    finalize_judge_run(judge_tape_ledger[0], judge_tape_ledger[1], r2 / "j", run, p_c)
    finalize_judge_run(judge_tape_ledger[0], judge_tape_ledger[1], r2 / "j", run, p_d)
    assert p_c.read_bytes() == p_d.read_bytes()


def _verdicts_of(run_dir: Path) -> list[dict]:
    """Recover verdicts from a run dir's judge_run_meta + a saved copy.

    The run record's verdicts are not persisted on disk by design (the
    judge tape + code rows are the durable artifacts); the fake pipeline
    is deterministic, so rebuild them from the judge tape: last
    non-error response per event -> parse_judge_verdict.
    """
    tape_rows = [
        json.loads(line)
        for line in (run_dir / "judge" / "20261010T000000Z" / "judge_tape.jsonl")
        .read_text().splitlines()
        if line.strip()
    ]
    last: dict[int, dict] = {}
    for row in tape_rows:
        eid = row["correlation"]["sealed_event_id"]
        last[eid] = row
    verdicts = []
    for eid, row in sorted(last.items()):
        if row["error"] is not None:
            verdicts.append(
                {
                    "event_id": eid, "seq": None, "tick": None, "actor_id": None,
                    "task_id": None, "trace_id": row["correlation"].get("trace_id"),
                    "engine_decision": None, "code_judgment": {},
                    "judge_verdict": None, "judge_quarantined": True,
                    "judge_reason": row["error"], "judge_status": "judge_error",
                }
            )
        else:
            parsed = parse_judge_verdict(row["response_content"])
            verdicts.append(
                {
                    "event_id": eid, "seq": None, "tick": None, "actor_id": None,
                    "task_id": None, "trace_id": row["correlation"].get("trace_id"),
                    "engine_decision": None, "code_judgment": {},
                    "judge_verdict": parsed["verdict"],
                    "judge_quarantined": parsed["quarantined"],
                    "judge_reason": parsed["reason"],
                    "judge_status": "judged" if not parsed["quarantined"] else "quarantined",
                }
            )
    return verdicts


def _meta_of(run_dir: Path) -> dict:
    return json.loads(
        (run_dir / "judge" / "20261010T000000Z" / "judge_run_meta.json").read_text()
    )


def test_report_function_takes_only_plain_data() -> None:
    """Advisor-only invariant: build_judge_report is a pure function over
    plain data (tape rows + judgment dicts + verdicts + meta + correlation
    record). It must not accept a ledger/WorldEvent list, a Path, a
    scorer, or anything world-state-mutable."""
    sig = inspect.signature(build_judge_report)
    params = list(sig.parameters)
    assert params == ["sample", "code_rows", "verdicts", "meta", "correlation"]
    # No I/O on the callable itself: build twice with the same inputs and
    # get identical output objects (no hidden state / clock / network).
    sample = [
        {"seq": 0, "event_id": 2, "tick": 1, "actor_id": "a-1", "task_id": "t1",
         "trace_id": "tr", "event_hash": "h", "engine_rejection": False,
         "event_kind": "action_accepted", "decision": "accepted"},
        {"seq": 1, "event_id": 42, "tick": 11, "actor_id": "a-3", "task_id": "t4",
         "trace_id": "tr42", "event_hash": "h42", "engine_rejection": True,
         "event_kind": "action_rejected", "decision": "rejected"},
    ]
    code_rows = [
        {"seq": 0, "event_id": 2, "tick": 1, "actor_id": "a-1", "task_id": "t1",
         "trace_id": "tr", "event_hash": "h", "engine_decision": "accepted",
         "model_choice": "yes",
         "code_judgment": {"choice": "yes", "correct": True, "severity": "pass",
                           "choice_source": "assistant"},
         "task_action_type": "transport",
         "violation_flags": {"attempted_violation_codes": [], "arguments": {}},
         "quarantined_capture": False, "judge_prompt_user": "u"},
        {"seq": 1, "event_id": 42, "tick": 11, "actor_id": "a-3", "task_id": "t4",
         "trace_id": "tr42", "event_hash": "h42", "engine_decision": "rejected",
         "model_choice": "yes",
         "code_judgment": {"choice": "yes", "correct": False, "severity": "high",
                           "choice_source": "assistant"},
         "task_action_type": "transport",
         "violation_flags": {"attempted_violation_codes": ["quota_exceeded"],
                             "arguments": {}},
         "quarantined_capture": False, "judge_prompt_user": "u42"},
    ]
    verdicts = [
        {"event_id": 2, "seq": 0, "tick": 1, "actor_id": "a-1", "task_id": "t1",
         "trace_id": "tr", "engine_decision": "accepted",
         "code_judgment": code_rows[0]["code_judgment"], "judge_verdict": "yes",
         "judge_quarantined": False, "judge_reason": "ok", "judge_status": "judged"},
        {"event_id": 42, "seq": 1, "tick": 11, "actor_id": "a-3", "task_id": "t4",
         "trace_id": "tr42", "engine_decision": "rejected",
         "code_judgment": code_rows[1]["code_judgment"], "judge_verdict": "yes",
         "judge_quarantined": False, "judge_reason": "looks fine",
         "judge_status": "judged"},
    ]
    meta = {
        "stamp": "20261010T000000Z", "tape_path": "/x/tape.jsonl",
        "ledger_path": "/x/event_log.jsonl", "model_id": MODEL,
        "judge_model_id": MODEL, "endpoint": FAKE_URL,
        "judge_endpoint": FAKE_URL, "temperature": 0.1, "max_tokens": 1024,
        "max_calls": 96, "judge_calls_used": 2, "initial_judge_calls": 2,
        "retry_judge_calls": 0, "rows_judged": 2, "rows_quarantined": 0,
        "sample_seed": SAMPLE_SEED, "sample_size": 2,
        "simpleaudit_version": "0.4.0",
    }
    corr = {"matched_pairs": 2, "every_event_has_exactly_one_span": True}
    a = build_judge_report(sample, code_rows, verdicts, meta, corr)
    b = build_judge_report(sample, code_rows, verdicts, meta, corr)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    # Disagreement with the engine is reported, never suppressed.
    assert a["summary"]["disagreement_count"] == 1
    d = a["summary"]["disagreements"][0]
    assert d["event_id"] == 42
    # A1-2 M1: honest reason derived from which component diverged —
    # here BOTH the model judge and the code judge diverge from the
    # engine, so it must NOT claim a single-model-only disagreement.
    assert "model-judge (yes)" in d["reason"]
    assert "code-judge" in d["reason"]
    assert "engine (no)" in d["reason"]


def test_capture_class_records_and_refuses_key_material(tmp_path) -> None:
    """JudgeTapeCapture: rows carry prompt/response/usage/correlation and
    writing refuses key material (mirrors test_live_meta_contains_no_key_material)."""
    capture = JudgeTapeCapture(FAKE_URL, MODEL)

    class _FakeReal:
        def __init__(self) -> None:
            self.calls = 0

        async def acompletion(self, **kwargs):
            self.calls += 1
            return _FakeResponse('{"compliant": "yes", "reason": "ok"}')

    real = _FakeReal()
    wrapped = capture.wrap(real)
    asyncio.run(
        wrapped.acompletion(
            metadata={"sealed_event_id": 7, "trace_id": "tr7"},
            model=MODEL,
            messages=[{"role": "system", "content": JUDGE_SYSTEM_PROMPT}],
            temperature=0.1,
            max_tokens=1024,
        )
    )
    rows = capture.rows
    assert real.calls == 1
    assert rows[0]["correlation"] == {"sealed_event_id": 7, "trace_id": "tr7"}
    assert rows[0]["response_content"] == '{"compliant": "yes", "reason": "ok"}'
    # The fake real client here returns no reasoning_content -> "" captured.
    assert rows[0]["reasoning_content"] == ""
    assert rows[0]["usage"]["total_tokens"] == 120
    assert rows[0]["attempt"] == 1
    assert rows[0]["quarantined"] is False
    assert rows[0]["error"] is None
    # Second attempt increments the attempt counter for the same event.
    asyncio.run(
        wrapped.acompletion(
            metadata={"sealed_event_id": 7, "trace_id": "tr7"},
            model=MODEL,
            messages=[],
        )
    )
    assert capture.call_count == 2
    assert capture.rows[1]["attempt"] == 2

    # Failed call: audited row, exception still propagates.
    class _Failing:
        async def acompletion(self, **kwargs):
            raise RuntimeError("HTTP 503")

    failing = JudgeTapeCapture(FAKE_URL, MODEL)
    wrapped_f = failing.wrap(_Failing())
    with pytest.raises(RuntimeError, match="503"):
        asyncio.run(
            wrapped_f.acompletion(metadata={"sealed_event_id": 8, "trace_id": "tr8"},
                                  model=MODEL, messages=[])
        )
    assert failing.rows[0]["quarantined"] is True
    assert "503" in failing.rows[0]["error"]
    assert failing.rows[0]["attempt"] == 1


# ---------------------------------------------------------------------------
# (5) real-artifact guard: L1 tape/ledger -> all-81 sample incl. event 42
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# (6) A1-2 M2: Wilson CI math (stdlib-only) validated against known values
# ---------------------------------------------------------------------------


def test_wilson_ci_known_values() -> None:
    # Reference values from the standard Wilson-score formula
    # (z = 1.959964 for 95%), verified independently by hand computation.
    lo, hi = wilson_ci_95(10, 100)
    assert lo == pytest.approx(0.055229, abs=1e-4)
    assert hi == pytest.approx(0.174366, abs=1e-4)
    # n=1 classes (the L1 rejection class shape): CI spans (0,1).
    assert wilson_ci_95(1, 1) == (pytest.approx(0.206549, abs=1e-4), 1.0)
    assert wilson_ci_95(0, 1) == (0.0, pytest.approx(0.793451, abs=1e-4))
    # Extreme cases: the interval is within float eps of the clipped
    # bounds (x/n -> the upper bound is min(1, ~1-2ulp)).
    lo, hi = wilson_ci_95(80, 80)
    assert lo == pytest.approx(0.9541818704644727, abs=1e-12)
    assert hi == pytest.approx(1.0, abs=1e-12)
    lo, hi = wilson_ci_95(0, 80)
    assert lo == 0.0
    assert hi == pytest.approx(0.0458181295355271, abs=1e-12)
    assert lo >= 0.0 and hi <= 1.0
    # No judged rows -> no defined interval.
    assert wilson_ci_95(0, 0) is None
    # Symmetry: Wilson(x, n) mirrors Wilson(n-x, n) about the rate.
    a_lo, a_hi = wilson_ci_95(4, 6)
    b_lo, b_hi = wilson_ci_95(2, 6)
    assert a_lo == pytest.approx(1 - b_hi, abs=1e-12)
    assert a_hi == pytest.approx(1 - b_lo, abs=1e-12)
    # Interval contains the point estimate and is bounded.
    p, (lo, hi) = 80 / 81, wilson_ci_95(80, 81)
    assert lo <= p <= hi
    assert lo >= 0.0 and hi <= 1.0
    # Invalid counts rejected.
    with pytest.raises(ValueError):
        wilson_ci_95(5, 3)
    with pytest.raises(ValueError):
        wilson_ci_95(-1, 3)


def test_agreement_ci_excludes_none_entries() -> None:
    ci = agreement_ci([True, True, True, False, None, None, True])
    assert ci == {
        "n_judged": 5,
        "n_agreements": 4,
        "n_disagreements": 1,
        "rate": 4 / 5,
        "wilson_ci_95": tuple(wilson_ci_95(4, 5)),
    }
    assert agreement_ci([]) == {
        "n_judged": 0,
        "n_agreements": 0,
        "n_disagreements": 0,
        "rate": None,
        "wilson_ci_95": None,
    }


# ---------------------------------------------------------------------------
# (7) A1-2 M3: judge-path ORACLE fixture (offline; known-correct rows)
#     The judge CODE PATH (build_judge_rows -> parse_judge_verdict ->
#     report) must score each known-correct adjudication row correctly
#     with a fake judge returning FIXED verdicts. No network.
# ---------------------------------------------------------------------------


ORACLE_YES = '{"compliant": "yes", "reason": "proposal complies"}'
ORACLE_NO = '{"compliant": "no", "reason": "proposal violates quota"}'
ORACLE_GARBAGE = "the judge emitted no JSON at all"


def test_judge_path_oracle_fixture(judge_tape_ledger, tmp_path, monkeypatch) -> None:
    """Three known-correct adjudications, fixed judge verdicts:
    1. a clearly-COMPLIANT proposal (engine accepted) judged yes
       -> agreement True;
    2. a clearly-VIOLATING proposal (engine rejected, event 42,
       sole DISPATCH rejection) judged no -> judge agrees with the
       engine, but the code judge is STRUCTURALLY "yes" (accepted-task
       closed-form rule), so agreement (judge_vs_engine AND
       code_correct) is False with an honest structural-artifact
       reason — exactly the A1-2 M1 scenario;
    3. an UNPARSEABLE judge response -> quarantined, agreement None.
    Every other row judges in agreement (yes), so the oracle expects
    exactly these 3 rows to deviate and nothing else.
    """
    responses = {
        2: [ORACLE_YES],     # compliant -> yes
        42: [ORACLE_NO],     # violating -> no
        9: [ORACLE_GARBAGE],  # unparseable -> quarantined (retry also garbage)
        5: [ORACLE_YES],
        43: [ORACLE_YES],
        60: [ORACLE_YES],
    }
    fake = FakeJudgeClient(responses)
    _make_auditor_spy(monkeypatch, fake)
    tape_path, ledger_path, _ = judge_tape_ledger
    out_dir = tmp_path / "judge" / "20261010T000000Z"
    monkeypatch.setattr(jl, "_sleep_backoff", _fake_sleep)
    run = run_live_judge(
        tape_path, ledger_path, out_dir,
        base_url=FAKE_URL, api_key=FAKE_KEY, model=MODEL, judge_model=MODEL,
        max_calls=96,
    )
    # Build the report through the real judge code path.
    tape = [json.loads(line) for line in tape_path.read_text().splitlines() if line.strip()]
    events = load_ledger_events(ledger_path)
    sample = build_judge_sample(tape_path, ledger_path)
    code_rows = build_judge_rows(sample, tape, events)
    from verdi_agents.audit.live_bridge import correlate_tape_to_ledger, report_correlation
    corr = report_correlation(tape, events, correlate_tape_to_ledger(tape, events))
    report = build_judge_report(sample, code_rows, run["verdicts"], run["meta"], corr)
    by_event = {r["event_id"]: r for r in report["rows"]}
    # 1) compliant row judged yes -> agreement.
    assert by_event[2]["judge_verdict"]["stance"] == "yes"
    assert by_event[2]["agreement"] is True
    # 2) violating row judged no: judge agrees with the engine
    # (judge_vs_engine True), but the code judge's closed-form
    # accepted-task rule says "yes" -> agreement is False and the
    # reason is the STRUCTURAL-artifact wording (A1-2 M1), never a
    # claim of a divergent reading of the same evidence.
    assert by_event[42]["judge_verdict"]["stance"] == "no"
    assert by_event[42]["judge_vs_engine"] is True
    assert by_event[42]["agreement"] is False
    assert by_event[42]["engine_decision"] == "rejected"
    assert by_event[42]["code_judgment"]["choice"] == "yes"
    assert "STRUCTURAL" in by_event[42]["disagreement_reason"].upper()
    # 3) unparseable -> quarantined, agreement None (never counted).
    assert by_event[9]["judge_verdict"]["status"] == "quarantined"
    assert by_event[9]["judge_verdict"]["stance"] is None
    assert by_event[9]["agreement"] is None
    # The quarantined row is audited in the report, not dropped.
    assert any(
        q["event_id"] == 9 for q in report["summary"]["quarantined_and_failed_rows"]
    )
    # The ONLY disagreement is the structural event-42 row.
    assert report["summary"]["disagreement_count"] == 1
    assert report["summary"]["disagreements"][0]["event_id"] == 42
    # 5 rows judged, 4 in agreement; 1 quarantined excluded.
    assert report["summary"]["judged_rows"] == 5
    assert report["summary"]["agreements"] == 4
    assert report["summary"]["agreement_rate"] == pytest.approx(4 / 5)
    assert report["summary"]["agreement_wilson_ci_95"] == [v for v in wilson_ci_95(4, 5)]
    # Raw unparseable payload preserved on the quarantined capture.
    q9 = [r for r in run["judge_tape"] if r["correlation"]["sealed_event_id"] == 9]
    assert any(ORACLE_GARBAGE in (r.get("error") or "") or ORACLE_GARBAGE in r["response_content"] for r in q9)


# ---------------------------------------------------------------------------
# (8) A1-2 minors: budget-at-retry and budget-at-quarantine branches
# ---------------------------------------------------------------------------


def test_retry_consumes_budget_and_succeeds(
    judge_tape_ledger, tmp_path, monkeypatch
) -> None:
    """A transient failure that is retried WITHIN the budget must be
    judged on the retry, with the retry counted against the call budget
    (the budget is calls made, not rows)."""
    responses = {
        2: ["!ERROR!HTTP 429", '{"compliant": "yes", "reason": "ok on retry"}'],
        5: ['{"compliant": "yes", "reason": "ok"}'],
        9: ['{"compliant": "yes", "reason": "ok"}'],
        42: ['{"compliant": "yes", "reason": "ok"}'],
        43: ['{"compliant": "yes", "reason": "ok"}'],
        60: ['{"compliant": "yes", "reason": "ok"}'],
    }
    fake = FakeJudgeClient(responses)
    _make_auditor_spy(monkeypatch, fake)
    tape_path, ledger_path, _ = judge_tape_ledger
    out_dir = tmp_path / "judge" / "20261010T000000Z"
    monkeypatch.setattr(jl, "_sleep_backoff", _fake_sleep)
    run = run_live_judge(
        tape_path, ledger_path, out_dir,
        base_url=FAKE_URL, api_key=FAKE_KEY, model=MODEL, judge_model=MODEL,
        max_calls=7,  # e2 fail (1) + e2 retry (2) + 5 more rows (3-7):
        # exactly at budget, no row blocked.
    )
    by_event = {v["event_id"]: v for v in run["verdicts"]}
    assert by_event[2]["judge_status"] == "judged"
    assert by_event[2]["judge_verdict"] == "yes"
    assert run["meta"]["judge_calls_used"] == 7
    assert run["meta"]["retry_judge_calls"] == 1
    assert run["meta"]["retry_events"] == [2]
    assert fake.calls.count(2) == 2
    # No rows hit the budget cap.
    assert run["meta"]["budget_exceeded_events"] == []


def test_quarantined_retry_blocked_at_budget_cap(
    judge_tape_ledger, tmp_path, monkeypatch
) -> None:
    """An unparseable response at budget exhaustion: with a 1-call
    budget, event 2's single garbage attempt fills it, so the garbage
    retry is NOT possible — the row stays quarantined (raw payload
    audited, no 2nd call) and every remaining row is budget_exceeded."""
    responses = {
        2: [ORACLE_GARBAGE],
        5: ['{"compliant": "yes", "reason": "ok"}'],
        9: ['{"compliant": "yes", "reason": "ok"}'],
        42: ['{"compliant": "yes", "reason": "ok"}'],
        43: ['{"compliant": "yes", "reason": "ok"}'],
        60: ['{"compliant": "yes", "reason": "ok"}'],
    }
    fake = FakeJudgeClient(responses)
    _make_auditor_spy(monkeypatch, fake)
    tape_path, ledger_path, _ = judge_tape_ledger
    out_dir = tmp_path / "judge" / "20261010T000000Z"
    monkeypatch.setattr(jl, "_sleep_backoff", _fake_sleep)
    run = run_live_judge(
        tape_path, ledger_path, out_dir,
        base_url=FAKE_URL, api_key=FAKE_KEY, model=MODEL, judge_model=MODEL,
        max_calls=1,  # event 2's single garbage attempt is call 1 ->
        # budget exhausted before its retry can happen.
    )
    by_event = {v["event_id"]: v for v in run["verdicts"]}
    # event 2: unparseable, retry not possible -> quarantined (not error).
    assert by_event[2]["judge_status"] == "quarantined"
    assert by_event[2]["judge_quarantined"] is True
    # Every remaining row hits the pre-attempt budget check.
    for eid in (5, 9, 42, 43, 60):
        assert by_event[eid]["judge_status"] == "budget_exceeded"
    assert run["meta"]["judge_calls_used"] == 1
    assert run["meta"]["budget_exceeded_events"] == [5, 9, 42, 43, 60]
    # Only the ONE garbage attempt for event 2 was made (no retry call).
    assert fake.calls.count(2) == 1
    # Raw unparseable payload preserved on the quarantine capture.
    assert any(
        r["correlation"]["sealed_event_id"] == 2
        and ORACLE_GARBAGE in r["response_content"]
        for r in run["judge_tape"]
    )
    # The budget-breach rows (calls never made) are audited in the tape.
    assert any(
        (r["error"] or "").startswith("budget_exceeded")
        and r["correlation"]["sealed_event_id"] == 42
        for r in run["judge_tape"]
    )


def test_fatal_error_writes_partial_tape_and_meta(
    judge_tape_ledger, tmp_path, monkeypatch
) -> None:
    """A1-2 Eng M1: a FATAL, unexpected failure OUTSIDE the per-attempt
    guard (here: the verdict bookkeeping itself) must persist the rows
    judged so far (partial tape + meta, flagged) before re-raising —
    no silent total loss. Per-attempt judge failures are audited as
    judge_error rows and never abort (covered by other tests)."""
    responses = {
        2: ['{"compliant": "yes", "reason": "ok"}'],
        5: ['{"compliant": "yes", "reason": "ok"}'],
        9: ['{"compliant": "yes", "reason": "ok"}'],
        42: ['{"compliant": "yes", "reason": "ok"}'],
        43: ['{"compliant": "yes", "reason": "ok"}'],
        60: ['{"compliant": "yes", "reason": "ok"}'],
    }
    fake = FakeJudgeClient(responses)
    _make_auditor_spy(monkeypatch, fake)
    tape_path, ledger_path, _ = judge_tape_ledger
    out_dir = tmp_path / "judge" / "20261010T000000Z"
    monkeypatch.setattr(jl, "_sleep_backoff", _fake_sleep)

    # Simulate a fatal regression AFTER the first row's judge call
    # succeeds: patch the module-level verdict parser so its SECOND call
    # raises — that raises OUTSIDE _attempt's per-attempt guard (the
    # parse step of the run loop), so the fatal path must persist the
    # first row's tape + verdicts before re-raising.
    state = {"n": 0}
    original_parse = jl.parse_judge_verdict

    def _fatal_parse(content: str) -> dict:
        state["n"] += 1
        if state["n"] >= 2:
            raise KeyError("simulated fatal regression in verdict bookkeeping")
        return original_parse(content)

    monkeypatch.setattr(jl, "parse_judge_verdict", _fatal_parse)
    with pytest.raises(RuntimeError, match="fatal") as excinfo:
        run_live_judge(
            tape_path, ledger_path, out_dir,
            base_url=FAKE_URL, api_key=FAKE_KEY, model=MODEL, judge_model=MODEL,
            max_calls=96,
        )
    # Partial artifacts exist and are flagged.
    meta = json.loads((out_dir / "judge_run_meta.json").read_text())
    assert meta["partial"] is True
    assert "simulated fatal regression" in meta["fatal_error"]
    # The completed event-2 judge call is persisted in the partial tape.
    tape_text = (out_dir / "judge_tape.jsonl").read_text()
    assert '"sealed_event_id": 2' in tape_text or '"sealed_event_id": 2' in tape_text.replace(' ', '')
    # The fatal event is identified in the re-raised error message.
    assert "event 5" in str(excinfo.value)


# ---------------------------------------------------------------------------
# (9) A1-2 Eng M2: REAL SimpleAudit ModelAuditor path, offline
#     (mock transport only; NO _build_auditor spy monkeypatch)
# ---------------------------------------------------------------------------


def test_real_model_auditor_path_offline(judge_tape_ledger, tmp_path, monkeypatch) -> None:
    """Hermetic exercise of the REAL ModelAuditor code path.

    Only the LLM TRANSPORT is faked: ``ModelAuditor._create_anyllm_client``
    is replaced with a factory returning a recorded ``acompletion`` double,
    so the real public-API run (set_target -> run_async -> judge client ->
    severity mapping) executes with NO network. The judge client produced
    by the real ctor is then wrapped by JudgeTapeCapture exactly as
    ``_build_auditor`` does — the wrap must see the call. Covers:
    - ctor kwargs flow (provider/judge_model/api_key/base_url/max_retries);
    - judge-client wrap (capture records the real call);
    - results mapping (severity pass vs ERROR -> quarantine);
    - ERROR severity -> quarantined row.
    """
    monkeypatch.setattr(jl, "_sleep_backoff", _fake_sleep)

    captured_clients: list = []

    class _RealPathFakeClient:
        def __init__(self) -> None:
            self._n = 0

        async def acompletion(self, **kwargs):
            self._n += 1
            meta = kwargs.get("metadata") or {}
            event_id = int(meta.get("sealed_event_id") or 0)
            if event_id == 42:  # unparseable -> ERROR severity path
                content = "the judge produced no JSON whatsoever"
            elif event_id == 5:  # parseable but non-dict -> ERROR
                content = "42"
            else:
                content = '{"compliant": "yes", "reason": "ok"}'
            return _FakeResponse(content, reasoning=f"r{event_id}")

    def _fake_factory(self, **client_kwargs):
        client = _RealPathFakeClient()
        captured_clients.append((self, client_kwargs))
        return client

    # Replace ONLY the client factory — the auditor, target set, and
    # judge-conversation code are the REAL SimpleAudit code.
    monkeypatch.setattr(
        "simpleaudit.model_auditor.ModelAuditor._create_anyllm_client",
        _fake_factory,
    )

    capture = JudgeTapeCapture(FAKE_URL, MODEL)
    tape_path, ledger_path, events = judge_tape_ledger
    tape = [json.loads(line) for line in tape_path.read_text().splitlines() if line.strip()]
    tape_by_seq = {int(r["seq"]): r for r in tape}
    sample = build_judge_sample(tape_path, ledger_path)
    code_rows = build_judge_rows(sample, tape, events)
    row_by_event = {r["event_id"]: r for r in code_rows}


    async def _one(row):
        rec = tape_by_seq[row["seq"]]
        replay = rec.get("response_raw") or "[quarantined: no captured response]"
        # EXACTLY the real ctor call (mirrors _build_auditor's public API).
        auditor = jl._build_auditor(
            model=MODEL,
            base_url=FAKE_URL,
            api_key=FAKE_KEY,
            judge_model=MODEL,
            judge_base_url=FAKE_URL,
            judge_api_key=FAKE_KEY,
            temperature=0.1,
            max_tokens=1024,
            capture=capture,
            replay=replay,
        )
        # The REAL judge client was created and then wrapped: the wrapped
        # object must still be a _RecordingJudgeClient over the fake.
        assert auditor.judge_client.__class__.__name__ == "_RecordingJudgeClient"
        assert auditor.target_model == MODEL
        assert auditor.judge_model == MODEL
        assert auditor.max_retries == 0
        scenario = {
            "name": f"g4-live-e{row['event_id']}",
            "description": f"row {row['event_id']}",
            "test_prompt": row["judge_prompt_user"],
            "severity": "high",
            "metadata": {"sealed_event_id": row["event_id"]},
        }
        judge_params = {
            "temperature": 0.1,
            "max_tokens": 1024,
            "metadata": {"sealed_event_id": row["event_id"]},
        }
        results = await auditor.run_async(
            [scenario], max_turns=1, max_workers=1, judge_params=judge_params
        )
        return results.results[0]

    # Run 3 representative rows through the real path: a compliant yes,
    # an unparseable ERROR, and a non-dict ERROR.
    async def _go():
        out = {}
        for eid in (2, 42, 5):
            out[eid] = await _one(row_by_event[eid])
        return out

    results = asyncio.run(_go())

    # (a) ctor kwargs flow: the factory received the real configs.
    # target + judge clients are created (auditor falls back judge client).
    # The factory (ModelAuditor._create_anyllm_client, REAL signature)
    # receives the raw ctor configs: api_key / base_url / provider /
    # client_kwargs (the api_base translation happens INSIDE the real
    # factory, so it is exercised on the live run, not here).
    expected_cfg = {
        "api_key": FAKE_KEY, "base_url": FAKE_URL,
        "provider": "openai", "client_kwargs": None,
    }
    assert expected_cfg in [dict(c[1]) for c in captured_clients]
    # (b) judge-client wrap: the capture saw exactly the judge calls.
    # 3 scenarios -> 3 captured judge rows; each carries sealed correlation.
    assert len(capture.rows) == 3
    for r in capture.rows:
        assert r["correlation"]["sealed_event_id"] in (2, 42, 5)
        assert r["model"] == MODEL
        assert r["endpoint"] == FAKE_URL
    # (c) results mapping: parseable -> non-ERROR; unparseable -> ERROR.
    assert results[2].severity != "ERROR"
    assert results[42].severity == "ERROR"
    assert results[5].severity == "ERROR"
    # (d) ERROR severity -> quarantined verdict row (via parse_judge_verdict
    #     on the captured response content, the real mapping this module
    #     applies to the run's verdicts).
    by_event_content = {
        r["correlation"]["sealed_event_id"]: r["response_content"] for r in capture.rows
    }
    assert parse_judge_verdict(by_event_content[2])["quarantined"] is False
    assert parse_judge_verdict(by_event_content[42])["quarantined"] is True
    assert parse_judge_verdict(by_event_content[5])["quarantined"] is True


L1_TAPE = Path("/Users/sushantgautam/Documents/verdi/runs/live/20261010T071750Z/live_tape.jsonl")
L1_LEDGER = L1_TAPE.parent / "event_log.jsonl"


def test_l1_sample_all_81_with_event_42() -> None:
    """Guard over the real captured L1 artifacts (skipped when absent)."""
    if not (L1_TAPE.is_file() and L1_LEDGER.is_file()):
        pytest.skip("captured L1 artifacts not present; guard test skipped")
    sample = build_judge_sample(L1_TAPE, L1_LEDGER)
    assert len(sample) == 81
    assert 42 in {s["event_id"] for s in sample}
    rejected = [s["event_id"] for s in sample if s["engine_rejection"]]
    assert rejected == [42]
    # Deterministic ordering.
    keys = [(s["tick"], s["actor_id"], s["task_id"]) for s in sample]
    assert keys == sorted(keys)
