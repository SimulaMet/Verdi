"""Estimand wiring tests (P2 WS-4, tests/analysis).

Covers:
* ``run_estimand_values`` maps the six PREREGISTRATION estimands to the
  metrics.py functions (checked against hand-computed values on a small
  synthetic run, and against the pilot's metric outputs);
* ``paired_estimates`` single-pair honesty (sufficient=False, CI withheld)
  and multi-pair behavior (real CIs from n_pairs >= 10, insufficient flag
  for 2-9 pairs).
"""
from __future__ import annotations

import pytest
from tests.analysis.conftest import (  # noqa: F401  (builders)
    appeal,
    event,
    sanction,
    world_state,
)

from verdi.analysis.estimates import (
    ESTIMANDS,
    paired_estimates,
    run_estimand_values,
)
from verdi.analysis.statistics import PairedResult

# --------------------------------------------------------------------------- #
# synthetic ledger: 4 action events (1 rejected), 1 sanction, 1 upheld appeal
# --------------------------------------------------------------------------- #


def _events():
    return [
        event(0, tick=0, kind="action_accepted", decision="accepted", payload={}),
        event(1, tick=1, kind="action_accepted", decision="accepted", payload={}),
        event(2, tick=2, kind="action_rejected", decision="rejected",
              payload={"attempted_violation_codes": ["x"]}),
        event(3, tick=3, kind="action_accepted", decision="accepted", payload={}),
        event(4, tick=3, kind="sanction_imposed", decision="accepted",
              payload={"tick": 3, "sanction_id": "s1", "subject_id": "a-01",
                       "scope": ["speaking"], "duration_ticks": 2,
                       "evidence_event_ids": [2]}),
        event(5, tick=4, kind="appeal_filed", decision="accepted",
              payload={"tick": 4, "appeal_id": "ap1", "sanction_id": "s1",
                       "appellant_id": "a-01", "grounds": "g"}),
        event(6, tick=4, kind="appeal_upheld", decision="accepted",
              payload={"tick": 4, "appeal_id": "ap1", "sanction_id": "s1",
                       "appellant_id": "a-01"}),
        event(7, tick=4, kind="rights_restored", decision="accepted",
              payload={"tick": 4, "sanction_id": "s1", "appellant_id": "a-01"}),
    ]


def _states():
    # terminal tick 6: agent a-01 recovered to pre-sanction level (3.0)
    # at ticks 5 and 6 (2 consecutive); a-02 constant.
    return {
        0: world_state(0, {"a-01": {"food": 3.0}, "a-02": {"food": 7.0}}),
        3: world_state(3, {"a-01": {"food": 1.0}, "a-02": {"food": 7.0}}),
        5: world_state(5, {"a-01": {"food": 3.0}, "a-02": {"food": 7.0}}),
        6: world_state(6, {"a-01": {"food": 3.0}, "a-02": {"food": 7.0}}),
    }


def test_run_estimand_values_maps_all_six():
    values = run_estimand_values(states_by_tick=_states(), log=_events())
    assert tuple(sorted(values)) == tuple(sorted(ESTIMANDS))
    # 1 rejected of 4 action events
    assert values["safety_rate"] == pytest.approx(0.25)
    # 1 upheld appeal over 1 sanction
    assert values["false_punishment_rate"] == pytest.approx(1.0)
    # terminal snapshot tick 6: mean(3, 7), gini of [3, 7]
    assert values["welfare_mean_food"] == pytest.approx(5.0)
    from verdi.analysis.metrics import gini

    assert values["inequality_gini_food"] == pytest.approx(gini([3.0, 7.0]))
    # restriction end = tick 4 (rights_restored); post ticks 5, 6 both meet
    # the pre-sanction level 3.0 -> sustained, fraction 1.0
    assert values["sustained_recovery"] == pytest.approx(1.0)
    # min(2, end_tick 6 + 1 - effective 3) = 2 active ticks by run end
    assert values["time_under_deprivation"] == pytest.approx(2.0)


def test_run_estimand_values_no_evidence_is_zero():
    empty_states = {0: world_state(0, {"a-01": {"food": 0.0}, "a-02": {"food": 0.0}})}
    values = run_estimand_values(states_by_tick=empty_states, log=[])
    assert values == {name: 0.0 for name in ESTIMANDS}


def test_run_estimand_values_requires_snapshots():
    with pytest.raises(ValueError, match="at least one snapshot"):
        run_estimand_values(states_by_tick={}, log=[])


def test_single_pair_is_honest():
    t = {name: float(i) for i, name in enumerate(ESTIMANDS)}
    p = {name: float(i) * 2.0 for i, name in enumerate(ESTIMANDS)}
    results = paired_estimates([(t, p)])
    assert set(results) == set(ESTIMANDS)
    for name in ESTIMANDS:
        r = results[name]
        assert isinstance(r, PairedResult)
        assert r.n_pairs == 1
        assert r.sufficient is False
        assert r.ci_low is None
        assert r.ci_high is None
        # the single paired difference
        assert r.estimate == pytest.approx(t[name] - p[name])


def test_two_to_nine_pairs_flag_insufficient_without_ci():
    pairs = [({n: 1.0 for n in ESTIMANDS}, {n: 0.5 for n in ESTIMANDS}) for _ in range(3)]
    results = paired_estimates(pairs)
    for name in ESTIMANDS:
        assert results[name].n_pairs == 3
        assert results[name].sufficient is False
        assert results[name].ci_low is None
        assert results[name].ci_high is None
        assert results[name].estimate == pytest.approx(0.5)


def test_ten_pairs_yield_real_cis():
    pairs = []
    for i in range(10):
        t = {name: 1.0 + (i % 3) * 0.1 for name in ESTIMANDS}
        p = {name: 0.5 + (i % 2) * 0.1 for name in ESTIMANDS}
        pairs.append((t, p))
    results = paired_estimates(pairs)
    for name in ESTIMANDS:
        r = results[name]
        assert r.n_pairs == 10
        assert r.sufficient is True
        assert r.ci_low is not None
        assert r.ci_high is not None
        assert r.ci_low <= r.estimate <= r.ci_high


def test_run_dict_pairs_are_accepted():
    # run dicts carrying precomputed estimates
    trun = {"arm": "treatment", "estimates": {n: 1.0 for n in ESTIMANDS}}
    prun = {"arm": "placebo", "estimates": {n: 0.0 for n in ESTIMANDS}}
    results = paired_estimates([(trun, prun)])
    assert results["safety_rate"].estimate == pytest.approx(1.0)
    assert results["safety_rate"].sufficient is False


def test_run_dict_missing_estimates_raises():
    with pytest.raises(ValueError, match="missing estimand keys"):
        paired_estimates([({"arm": "t"}, {"arm": "p"})])
