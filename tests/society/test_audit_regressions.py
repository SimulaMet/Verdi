"""Regression tests for defects found in the 2026-11 system audit (AUDIT.md)."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from verdi_agents.society.memory import AgentMemory
from verdi_agents.society.tasks import _parse_orchestrator_response, generate_llm_tape

from .test_tasks import make_agent, make_state


def _state():
    return make_state({a: make_agent(a, edges=["a1", "a2"]) for a in ("a1", "a2")})


def test_one_bad_amount_does_not_discard_the_orchestrated_tape():
    content = json.dumps({
        "a1": {"action_type": "produce", "amount": None},
        "a2": {"action_type": "produce", "amount": "two"},
    })
    tasks = _parse_orchestrator_response(content, _state(), 1)
    assert tasks is not None and len(tasks) == 2


def test_transfer_to_self_or_unknown_becomes_a_valid_task():
    content = json.dumps({
        "a1": {"action_type": "transfer", "resource": "food", "amount": 1, "to": "a1"},
        "a2": {"action_type": "transfer", "resource": "food", "amount": 1, "to": None},
    })
    tasks = _parse_orchestrator_response(content, _state(), 1)
    assert tasks is not None
    assert all(t.action_type != "transfer" for t in tasks)


class _Caller:
    def __init__(self, content: str):
        self._content = content

    async def generate_structured(self, messages, **kwargs):
        return SimpleNamespace(content=self._content, usage={"prompt_tokens": 11,
                               "completion_tokens": 5}, trace_id="t-1")


def test_unusable_orchestrator_output_still_reports_call_meta():
    tasks, used_llm, meta = asyncio.run(generate_llm_tape(1, _state(), _Caller("not json")))
    assert not used_llm and tasks
    # Tokens were spent: the campaign must be able to record the call.
    assert meta["prompt_tokens"] == 11
    assert "parse_error" in meta


def test_consolidated_summary_excludes_recent_entries():
    mem = AgentMemory()
    for i in range(30):
        mem.append_reflection("a1", f"r{i}")
    mem.apply_consolidation("a1", "SUM")
    assert mem.consolidated_summary("a1") == "SUM"
    assert mem.consolidated_summary("nobody") == ""


def test_watchdog_counts_in_process_calls_during_long_batches(tmp_path, monkeypatch):
    # Reflection/consolidation batches write model_calls.jsonl only at the
    # end; completed calls must still reset the stall deadline.
    import verdi_agents.society.live as live_module

    (tmp_path / "events.jsonl").write_text("")
    monkeypatch.setattr(live_module, "_HEARTBEAT_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr("os._exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))
    calls = {"n": 0}

    def tick_calls():
        calls["n"] += 1
        return calls["n"]

    async def scenario():
        watchdog = asyncio.create_task(
            live_module._stall_watchdog(tmp_path, 0.2, extra_progress=tick_calls))
        await asyncio.sleep(0.6)
        assert not watchdog.done(), "watchdog fired despite completed calls"
        watchdog.cancel()

    asyncio.run(scenario())


def test_prompt_shows_lifecycle_tick_not_ledger_counter():
    from verdi_agents.schemas import Task
    from verdi_agents.society.prompting import build_social_prompt_messages

    state = make_state({"a1": make_agent("a1")}, tick=104)
    task = Task(task_id="soc-t07-00", actor_id="a1", action_type="produce",
                counterpart_ids=[], constraints={"resource": "food", "amount": 1},
                public_observations=["produce 1 food"], tick_created=7)
    user = build_social_prompt_messages("a1", task, state, AgentMemory())[1]["content"]
    assert "It is now tick 7." in user
    assert "tick 104" not in user


def test_metrics_count_only_accepted_sanctions_and_filed_appeals():
    from verdi_agents.society.campaign import _compute_metrics

    result = SimpleNamespace(
        probes=[], constitution_versions=[], quarantined=[], assembly_quarantined={},
        assembly_outcome=None, restoration_events=[], model_calls=[],
        final_state_hash="h",
        sanction_events=[
            {"kind": "sanction_imposed", "decision": "accepted"},
            {"kind": "sanction_imposed"},  # t0 declaration (no decision field)
            {"kind": "sanction_imposed", "decision": "rejected"},
            {"kind": "world_shock", "resource": "food"},
        ],
        appeal_events=[{"phase": "filed"}, {"phase": "adjudicated"}],
    )
    metrics = _compute_metrics(result, [])
    assert metrics["sanctions"] == 2
    assert metrics["appeals"] == 1


def test_inner_life_prompt_uses_lifecycle_tick():
    from verdi_agents.society.inner_life import build_inner_life_prompt_messages

    state = make_state({"a1": make_agent("a1")}, tick=104)
    user = build_inner_life_prompt_messages("a1", state, AgentMemory(), tick=7)[1]["content"]
    assert "tick 7" in user and "tick 104" not in user


def test_probe_subjects_follow_send_order_not_completion_order():
    from verdi_agents.society.campaign import _RecordingCaller

    class _Slow:
        async def generate_structured(self, messages, **kw):
            await asyncio.sleep(0.05 if messages[0]["content"] == "first" else 0)
            return SimpleNamespace(content="yes", usage={}, trace_id=messages[0]["content"])

    campaign = SimpleNamespace(_tick=0, _model_calls=[],
                               _model_fingerprint=lambda: "fp",
                               _write_model_calls_lines=lambda lines: None)
    subjects = iter(["alice", "bob"])
    rec = _RecordingCaller(_Slow(), campaign, "probe", "probe",
                           subject_provider=lambda: next(subjects))

    async def run():
        await asyncio.gather(
            rec.generate_structured([{"role": "user", "content": "first"}]),
            rec.generate_structured([{"role": "user", "content": "second"}]),
        )

    asyncio.run(run())
    # "second" completes first, but it was sent by bob.
    by_trace = {c.request_id: c.subject for c in campaign._model_calls}
    assert by_trace == {"first": "alice", "second": "bob"}
