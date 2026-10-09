"""G0/G1 tests for deterministic option-based scoring (P1-D)."""
from __future__ import annotations

import pytest

from verdi.measurement import JudgeProtocol, RubricScorer, score_probe
from verdi.measurement.probes import ProbeBank
from verdi.schemas import ProbeItem


def _item() -> ProbeItem:
    return ProbeBank("bank-1", "probe-v1", pool_size=2).draw("a1", 0, "private", rng_seed=0)


def test_scoring_is_deterministic() -> None:
    item = _item()
    axis = item.value_axes[0]
    scorer = RubricScorer("rubric-1", {axis: [1.0, 0.1]})
    first = scorer.score(item, "yes")
    second = scorer.score(item, "yes")
    assert first == second
    assert first == scorer.score(item, "Yes")  # case-insensitive option match


def test_scoring_is_option_based_no_rng() -> None:
    item = _item()
    axis = item.value_axes[0]
    scorer = RubricScorer("rubric-1", {axis: [1.0, 0.1], "cooperation": [0.8, 0.2]})
    yes = scorer.score(item, "yes")
    no = scorer.score(item, "no")
    # Score averages only the probe's own value_axes.
    assert yes == 1.0
    assert no == pytest.approx(0.1)
    # Index addressing gives the same result.
    assert scorer.score(item, "0") == yes
    assert scorer.score(item, "1") == no


def test_value_vector_maps_options_to_axes() -> None:
    item = _item()
    axis = item.value_axes[0]
    scorer = RubricScorer("rubric-1", {axis: [1.0, 0.1], "cooperation": [0.8, 0.2]})
    vector = scorer.value_vector(item, "no")
    assert vector == {axis: 0.1, "cooperation": 0.2}
    assert all(0.0 <= v <= 1.0 for v in vector.values())


def test_invalid_response_rejected() -> None:
    item = _item()
    axis = item.value_axes[0]
    scorer = RubricScorer("rubric-1", {axis: [1.0, 0.1]})
    with pytest.raises(ValueError):
        scorer.score(item, "maybe")
    with pytest.raises(ValueError):
        scorer.score(item, "2")  # out of range index


def test_scorer_identity_recorded() -> None:
    item = _item()
    scorer = RubricScorer("rubric-1", {axis: [1.0, 0.1] for axis in item.value_axes}, version="1.0")
    assert scorer.judge_version == "rubric-1/v1.0"


def test_score_probe_default_judge_version() -> None:
    item = _item()
    score, vector, judge_version = score_probe(item, "yes")
    assert 0.0 <= score <= 1.0
    assert vector is not None
    assert judge_version == "rubric-1/v1.0"


class FixedJudge:
    """Duck-typed judge (stands in for a future LLM judge)."""

    judge_version = "fake-llm-judge/v0.1"

    def score(self, probe_item, response: str) -> float:
        return 0.5 if response.strip().lower() == "yes" else 0.0


def test_judge_protocol_pluggable_and_not_used_by_default() -> None:
    item = _item()
    judge = FixedJudge()
    assert isinstance(judge, JudgeProtocol)
    score, vector, version = score_probe(item, "yes", judge=judge)
    assert score == 0.5
    assert vector is None  # vector only from the deterministic rubric
    assert version == "fake-llm-judge/v0.1"

    # Without a judge, the deterministic scorer is used.
    _, _, default_version = score_probe(item, "yes")
    assert default_version != judge.judge_version


def test_judge_must_implement_protocol() -> None:
    with pytest.raises(TypeError):
        score_probe(_item(), "yes", judge=object())  # type: ignore[arg-type]
