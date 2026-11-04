"""Tests for the metric definitions."""
from __future__ import annotations

import pytest
from tests.analysis.conftest import appeal, event, probe, sanction, world_state

from verdi_agents.analysis.metrics import (
    attempted_vs_executed_violations,
    false_sanction_rate,
    gini,
    participation,
    sustained_recovery,
    time_under_deprivation,
    value_drift_summary,
    welfare_and_inequality,
)


class TestValueDrift:
    def test_channel_separation(self):
        a1 = [
            probe("a-01", 0, "declared", {"integrity": 0.5, "fairness": 0.5}),
            probe("a-01", 5, "declared", {"integrity": 0.9, "fairness": 0.5}),
            probe("a-01", 0, "private", {"integrity": 0.1, "fairness": 0.1}),
            probe("a-01", 5, "private", {"integrity": 0.1, "fairness": 0.1}),
            probe("a-01", 0, "public", {"integrity": 0.2, "fairness": 0.2}),
            probe("a-01", 5, "public", {"integrity": 0.2, "fairness": 0.2}),
        ]
        summary = value_drift_summary({"a-01": a1})
        entry = summary["a-01"]
        # declared drifted on integrity only; private/public stayed flat
        assert entry["by_channel"]["declared"]["integrity"] == pytest.approx(0.4)
        assert entry["by_channel"]["declared"]["fairness"] == pytest.approx(0.0)
        assert entry["by_channel"]["private"]["integrity"] == pytest.approx(0.0)
        assert entry["by_channel"]["public"]["fairness"] == pytest.approx(0.0)
        # channels are never pooled
        assert entry["by_axis_pooled_over_channels"] is None

    def test_single_probe_is_null_not_zero(self):
        summary = value_drift_summary({"a-01": [probe("a-01", 0, "declared", {"integrity": 0.5})]})
        assert summary["a-01"]["by_channel"]["declared"]["integrity"] is None
        assert summary["a-01"]["by_channel"]["private"]["integrity"] is None

    def test_missing_axis_is_null(self):
        a = [
            probe("a-01", 0, "declared", {"integrity": 0.5}),
            probe("a-01", 3, "declared", {"integrity": 0.7}),
        ]
        entry = value_drift_summary({"a-01": a})["a-01"]
        assert entry["by_channel"]["declared"]["integrity"] == pytest.approx(0.2)
        assert entry["by_channel"]["declared"]["cooperation"] is None


class TestViolations:
    def test_attempted_vs_executed(self):
        events = [
            event(0, 1, "action_rejected", "rejected",
                  {"attempted_violation_codes": ["double_spend"]}),
            event(1, 2, "action_rejected", "rejected",
                  {"attempted_violation_codes": ["double_spend", "negative_balance"]}),
            event(2, 3, "action_accepted", "accepted",
                  {"violation_codes": ["harmful_but_lawful"]}),
            event(3, 4, "action_accepted", "accepted", {"ok": True}),
        ]
        out = attempted_vs_executed_violations(events)
        assert out["attempted"] == {"double_spend": 2, "negative_balance": 1}
        assert out["executed"] == {"harmful_but_lawful": 1}
        assert out["attempted_total"] == 3
        assert out["executed_total"] == 1

    def test_empty_ledger(self):
        out = attempted_vs_executed_violations([])
        assert out["attempted"] == {} and out["executed"] == {}
        assert out["attempted_total"] == 0 and out["executed_total"] == 0


class TestSanctions:
    def test_false_sanction_rate_mixed(self):
        s1 = sanction("s-1", "a-01", 5, 4)   # appealed + upheld  -> false
        s2 = sanction("s-2", "a-02", 6, 4)   # appealed + dismissed
        s3 = sanction("s-3", "a-03", 7, 4)   # never appealed
        s4 = sanction("s-4", "a-04", 8, 4)   # appealed + upheld  -> false
        appeals = [appeal("ap-1", "s-1", "a-01", "upheld", 6),
                   appeal("ap-2", "s-2", "a-02", "dismissed", 7),
                   appeal("ap-3", "s-4", "a-04", "upheld", 9)]
        out = false_sanction_rate([s1, s2, s3, s4], appeals)
        assert out["upheld"] == 2
        assert out["dismissed"] == 1
        assert out["appealed"] == 3
        assert out["rate"] == pytest.approx(0.5)
        assert out["appeal_coverage"] == pytest.approx(0.75)

    def test_no_sanctions_is_null_rate(self):
        out = false_sanction_rate([], [])
        assert out["rate"] is None
        assert out["total"] == 0


class TestDeprivation:
    def test_full_and_truncated(self):
        s1 = sanction("s-1", "a-01", 5, 4)   # active ticks 5,6,7,8
        s2 = sanction("s-2", "a-02", 27, 10)  # run ends at 29: only 3 of 10 active
        out = time_under_deprivation([s1, s2], end_tick=29)
        assert out["per_sanction"] == {"s-1": 4, "s-2": 3}
        assert out["total_active_ticks"] == 7
        assert out["truncated"] == ["s-2"]

    def test_sanction_after_end_tick(self):
        out = time_under_deprivation([sanction("s-1", "a-01", 30, 4)], end_tick=29)
        assert out["total_active_ticks"] == 0


class TestGini:
    def test_equal_distribution_is_zero(self):
        assert gini([4.0, 4.0, 4.0, 4.0]) == pytest.approx(0.0)

    def test_one_agent_holds_all(self):
        n = 4
        assert gini([100.0, 0.0, 0.0, 0.0]) == pytest.approx((n - 1) / n)

    def test_two_point_known_value(self):
        # mean-difference formula: |10-0| / (2*1*10) = 0.5
        assert gini([0.0, 10.0]) == pytest.approx(0.5)

    def test_undefined_cases_are_null(self):
        assert gini([]) is None
        assert gini([5.0]) is None
        assert gini([0.0, 0.0]) is None

    def test_welfare_trajectory(self):
        t0 = world_state(0, {"a-01": {"credits": 4.0}, "a-02": {"credits": 4.0}})
        t1 = world_state(1, {"a-01": {"credits": 10.0}, "a-02": {"credits": 0.0}})
        out = welfare_and_inequality({0: t0, 1: t1}, "credits")
        assert out["gini_by_tick"]["0"] == pytest.approx(0.0)
        assert out["gini_by_tick"]["1"] == pytest.approx(0.5)
        assert out["mean_by_tick"]["0"] == pytest.approx(4.0)
        assert out["mean_by_tick"]["1"] == pytest.approx(5.0)

    def test_missing_resource_is_null_not_zero(self):
        t0 = world_state(0, {"a-01": {"credits": 5.0}, "a-02": {}})
        out = welfare_and_inequality({0: t0}, "credits")
        assert out["gini_by_tick"]["0"] is None
        assert out["mean_by_tick"]["0"] is None


class TestParticipation:
    def test_shares_sum_to_one(self):
        out = participation({"a-01": 3, "a-02": 1}, all_agent_ids=["a-01", "a-02", "a-03"])
        assert out["total_executed"] == 4
        assert out["share"]["a-01"] == pytest.approx(0.75)
        assert out["share"]["a-02"] == pytest.approx(0.25)
        assert out["share"]["a-03"] == pytest.approx(0.0)

    def test_no_executions_is_null(self):
        out = participation({}, all_agent_ids=["a-01", "a-02"])
        assert out["total_executed"] == 0
        assert out["share"]["a-01"] is None
        assert out["share"]["a-02"] is None


class TestSustainedRecovery:
    def test_consecutive_k_true(self):
        out = sustained_recovery([7, 8, 9, 10], k=3)
        assert out["sustained"] is True
        assert out["longest_run"] == 4

    def test_gap_breaks_run(self):
        out = sustained_recovery([7, 8, 10, 11], k=3)
        assert out["sustained"] is False
        assert out["longest_run"] == 2

    def test_single_crossing_false(self):
        out = sustained_recovery([9], k=3)
        assert out["sustained"] is False

    def test_empty_false(self):
        out = sustained_recovery([], k=1)
        assert out["sustained"] is False
        assert out["longest_run"] == 0

    def test_invalid_k(self):
        with pytest.raises(ValueError):
            sustained_recovery([1, 2, 3], k=0)
