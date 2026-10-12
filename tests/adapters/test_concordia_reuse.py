"""Tests for the Concordia deep-reuse adapter (todo D).

These are skip-guarded: they run only when ``gdm-concordia`` is installed. The
module under test is lazy and dependency-free at import, so importing it always
works; the tests below exercise the actual reuse path against real Concordia
components.
"""
from __future__ import annotations

import pytest

pytest.importorskip("concordia")

from verdi.adapters import concordia_reuse as cr  # noqa: E402


def test_value_axes_constant():
    assert cr.VALUE_AXES == ("integrity", "fairness", "cooperation", "autonomy", "solidarity")


def test_make_value_questionnaire_has_all_axes():
    qn = cr.make_value_questionnaire()
    dims = [q.dimension for q in qn.questions]
    assert dims == list(cr.VALUE_AXES)
    assert qn.questionnaire_type == "multiple_choice"


def test_score_response_uses_concordia_scoring():
    qn = cr.make_value_questionnaire()
    scores = cr.score_response(qn, "alice", {"integrity": "5", "fairness": "1"})
    assert set(scores) == {"integrity", "fairness"}
    # Concordia's process_answer maps a choice to a numeric value (not None).
    assert all(v is not None for v in scores.values())
    # Higher self-report should score >= lower one on the same ascending axis.
    hi = cr.score_response(qn, "a", {"integrity": "5"})["integrity"]
    lo = cr.score_response(qn, "a", {"integrity": "1"})["integrity"]
    assert hi > lo


def test_concordia_cross_check_maps_zero_one_to_scores():
    now = {
        "integrity": 0.9,
        "fairness": 0.2,
        "cooperation": 0.6,
        "autonomy": 0.5,
        "solidarity": 0.8,
    }
    cc = cr.concordia_cross_check(now, player_name="alice")
    assert set(cc) == set(now)
    assert all(isinstance(v, (int, float)) and v is not None for v in cc.values())
    # Monotonic: higher declared value -> equal-or-higher Concordia score.
    assert cc["integrity"] >= cc["fairness"]


def test_concordia_cross_check_ignores_unknown_axes():
    cc = cr.concordia_cross_check({"integrity": 0.5, "bogus": 0.9}, player_name="x")
    assert set(cc) == {"integrity"}


def test_concordia_cross_check_empty_when_no_numeric_values():
    assert cr.concordia_cross_check({}, player_name="x") == {}
    assert cr.concordia_cross_check({"integrity": "high"}, player_name="x") == {}
