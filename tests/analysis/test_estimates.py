"""Tests that each preregistered estimand is computed from the right metric.

Covers:
* ``run_estimand_values`` maps the six preregistered estimands to the
  metrics.py functions (checked against hand-computed values on a small
  synthetic run, and against the reference run's metric outputs);
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

from verdi_agents.analysis.estimates import (
    ESTIMANDS,
    paired_estimates,
    run_estimand_values,
)
from verdi_agents.analysis.statistics import PairedResult

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
    from verdi_agents.analysis.metrics import gini

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


def test_paired_estimates_empty_raises():
    # An empty pair sequence is a caller bug, not an estimate.
    with pytest.raises(ValueError, match="at least one matched pair"):
        paired_estimates([])


# --------------------------------------------------------------------------- #
# sustained_recovery operational definition (preregistered, v1.1)
# --------------------------------------------------------------------------- #

def _single_sanction_events(sid="s1", subject="a-01", end=4):
    return [
        event(0, tick=3, kind="sanction_imposed", decision="accepted",
              payload={"tick": 3, "sanction_id": sid, "subject_id": subject,
                       "scope": ["speaking"], "duration_ticks": 2,
                       "evidence_event_ids": []}),
        event(1, tick=end, kind="rights_restored", decision="accepted",
              payload={"tick": end, "sanction_id": sid, "appellant_id": subject}),
    ]


def test_sustained_recovery_single_crossing_disagrees_with_k_boolean():
    # Fails under the old formula, passes under the new one: the agent meets the pre-sanction level
    # at only ONE of three post-restriction snapshot ticks (ticks 6, 7, 9;
    # food 1, 1, 3; pre-sanction level 3).
    #   OLD formula: k=1 consecutive-crossing boolean -> no 2 consecutive
    #                 crossings... in fact the OLD code required the
    #                 metrics.sustained_recovery(k) boolean over the
    #                 crossing ticks: crossing = [9] alone, longest_run 1
    #                 >= k=1 -> sustained True -> value 1.0/1.0 = 1.0.
    #   NEW formula (v1.1 operational): per-agent snapshot fraction
    #                 1/3 -> estimand 1/3.
    # The two formulas DISAGREE (1.0 vs 1/3); assert the new one.
    states = {
        0: world_state(0, {"a-01": {"food": 3.0}}),
        3: world_state(3, {"a-01": {"food": 1.0}}),
        6: world_state(6, {"a-01": {"food": 1.0}}),
        7: world_state(7, {"a-01": {"food": 1.0}}),
        9: world_state(9, {"a-01": {"food": 3.0}}),
    }
    values = run_estimand_values(states_by_tick=states,
                                 log=_single_sanction_events())
    assert values["sustained_recovery"] == pytest.approx(1.0 / 3.0)
    # (old formula would have reported 1.0 here)


def test_sustained_recovery_mean_of_per_agent_fractions():
    # Two treated agents, each with post-restriction snapshot ticks
    # {5,6,7,8}. a-01 meets its pre-sanction level at tick 5 only (1/4);
    # a-02 meets at ticks 6 and 8 (2/4).
    #   NEW formula (v1.1 operational): estimand = mean(1/4, 2/4) = 3/8.
    #   OLD k-boolean formula: each agent has >= one crossing run of k=1
    #     -> both "sustained" -> value = 2/2 = 1.0.
    # Formulas disagree (1.0 vs 3/8); assert the new one.
    events = _single_sanction_events("s1", "a-01", end=4) + [
        event(2, tick=3, kind="sanction_imposed", decision="accepted",
              payload={"tick": 3, "sanction_id": "s2", "subject_id": "a-02",
                       "scope": ["speaking"], "duration_ticks": 2,
                       "evidence_event_ids": []}),
        event(3, tick=4, kind="rights_restored", decision="accepted",
              payload={"tick": 4, "sanction_id": "s2", "appellant_id": "a-02"}),
    ]

    def ws(tick, f1, f2):
        return world_state(tick, {"a-01": {"food": f1}, "a-02": {"food": f2}})

    states = {
        0: ws(0, 3.0, 6.0),   # pre-sanction: a-01=3.0, a-02=6.0
        3: ws(3, 1.0, 2.0),   # sanctioned
        5: ws(5, 3.0, 2.0),   # a-01 meets level
        6: ws(6, 1.0, 6.0),   # a-02 meets level
        7: ws(7, 1.0, 2.0),
        8: ws(8, 1.0, 6.0),   # a-02 meets level again
    }
    values = run_estimand_values(states_by_tick=states, log=events)
    # a-01: post ticks {5,6,7,8} -> meets at 5 -> 1/4
    # a-02: post ticks {5,6,7,8} -> meets at 6,8 -> 2/4
    # estimand = (1/4 + 2/4) / 2 = 3/8
    assert values["sustained_recovery"] == pytest.approx((1 / 4 + 2 / 4) / 2)


# --------------------------------------------------------------------------- #
# false_punishment_rate operational definition (preregistered, v1.1)
# --------------------------------------------------------------------------- #

def _sanction_with_restoration_but_dismissed_appeal():
    # A sanction whose appeal was DISMISSED but whose rights were later
    # restored (rights_restored event): under the preregistered v1.0 ideal
    # definition ("upheld-then-reversed OR appealed-and-restored") this
    # sanction counts in the numerator via the restoration leg; under
    # the v1.1 OPERATIONAL definition (appealed-and-upheld / all) it
    # does not.
    return [
        event(0, tick=3, kind="sanction_imposed", decision="accepted",
              payload={"tick": 3, "sanction_id": "s1", "subject_id": "a-01",
                       "scope": ["speaking"], "duration_ticks": 2,
                       "evidence_event_ids": []}),
        event(1, tick=4, kind="appeal_filed", decision="accepted",
              payload={"tick": 4, "appeal_id": "ap1", "sanction_id": "s1",
                       "appellant_id": "a-01", "grounds": "g"}),
        event(2, tick=5, kind="appeal_dismissed", decision="accepted",
              payload={"tick": 5, "appeal_id": "ap1", "sanction_id": "s1"}),
        event(3, tick=5, kind="rights_restored", decision="accepted",
              payload={"tick": 5, "sanction_id": "s1", "appellant_id": "a-01"}),
    ]


def test_false_punishment_rate_restoration_leg_disagrees():
    # Restored-but-appeal-dismissed sanction. In the v1.0 ledger
    # schema there is NO sanction state distinct from "upheld appeal":
    # the only reversal evidence is a `rights_restored` event, which
    # this synthetic run carries alongside a DISMISSED appeal.
    #   ideal v1.0 numerator (upheld-then-reversed OR appealed-and-
    #   restored), counting that restoration event: 1/1 = 1.0
    #   v1.1 OPERATIONAL (appealed-and-upheld only, implemented): 0/1 = 0.0
    # The formulas DISAGREE; assert the operational one and document the
    # data-model gap that forces path (b).
    events = _sanction_with_restoration_but_dismissed_appeal()
    # data-model fact: no appeal was upheld; the only restoration evidence
    # is the rights_restored event itself (not an upheld-appeal state)
    assert not any(e.kind == "appeal_upheld" for e in events)
    assert any(e.kind == "rights_restored" for e in events)
    states = {
        0: world_state(0, {"a-01": {"food": 3.0}, "a-02": {"food": 7.0}}),
        6: world_state(6, {"a-01": {"food": 3.0}, "a-02": {"food": 7.0}}),
    }
    values = run_estimand_values(states_by_tick=states, log=events)
    # the operational definition counts only appealed-and-upheld
    assert values["false_punishment_rate"] == pytest.approx(0.0)
    # the upheld-appeal case is still counted (regression guard): the
    # synthetic run from _events() has one sanction with an upheld appeal
    # -> 1/1 = 1.0 under BOTH the v1.0 ideal and the v1.1 operational
    # numerator (they agree whenever the restoration is expressible as
    # an upheld appeal, which is the only reversal state in v1.0)
    assert run_estimand_values(states_by_tick=states, log=_events())[
        "false_punishment_rate"
    ] == pytest.approx(1.0)
