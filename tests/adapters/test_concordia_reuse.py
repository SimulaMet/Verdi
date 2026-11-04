"""Tests for the Concordia deep-reuse adapter.

These are skip-guarded: they run only when ``gdm-concordia`` is installed. The
module under test is lazy and dependency-free at import, so importing it always
works; the tests below exercise the actual reuse path against real Concordia
components.
"""
from __future__ import annotations

import pytest

pytest.importorskip("concordia")

from verdi_agents.adapters import concordia_reuse as cr  # noqa: E402


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


