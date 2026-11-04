"""Deep REUSE of Google DeepMind Concordia building blocks in Verdi (todo D).

This module reuses Concordia's *actual* reusable components — not just its ABCs —
for the parts where they genuinely reduce duplication in Verdi's auditing /
longitudinal-measurement path. It is:

* **Lazy**: nothing imports ``concordia`` at module import time; the real package
  (``gdm-concordia``, Apache-2.0) is imported only inside the functions below, so
  this loads cleanly whether or not Concordia is installed and never forces its
  heavy optional dependencies onto Verdi.
* **Off by default**: no Verdi module imports it; it adds no dependency to
  ``pyproject.toml``.
* **Additive, not a replacement**: it does NOT touch the world reducer, the
  governance engine, or the working simulation loop. It exposes Concordia
  components that can be *used alongside* Verdi's own flows.

What is reused here (verified to import AND work standalone without heavy deps):

* :class:`concordia.contrib.data.questionnaires.base_questionnaire.QuestionnaireBase`
  — Concordia's structured-questionnaire data model, used to express the five
  value axes as a first-class questionnaire with Concordia's own
  ``process_answer`` scoring (choice -> dimension value). This is the piece that
  most directly serves longitudinal value measurement and gives an independent
  structured cross-check against Verdi's free-form interview self-assessment.

Deliberately NOT wrapped:

* Concordia's ``Memory`` component requires a bound ``Entity`` and a phase
  lifecycle (``get_entity().get_phase()``), so it cannot be used standalone as a
  drop-in store; Verdi's own ``AgentMemory.render_history_summary`` already covers
  this read-only need more simply.
* Concordia's ``GameClock`` subclasses have non-trivial constructors tied to the
  scene runner; Verdi's tick counter already provides checkpoint scheduling.
* The full Concordia *simulation* driver (``run_scenes`` + engines + LLM game
  masters) is deliberately NOT adopted: its world is driven by an LLM narrating
  mutable state, which would make audit evidence circular. See
  ``docs/concordia_eval.md``.
"""
from __future__ import annotations

from typing import Any

#: The five value axes measured throughout Verdi. Kept here (not imported from
#: measurement) so this module has no Verdi-side import cycle risk.
VALUE_AXES = ("integrity", "fairness", "cooperation", "autonomy", "solidarity")


def _require_concordia() -> None:
    try:
        import concordia  # noqa: F401
    except ImportError as exc:  # pragma: no cover - env-specific
        raise ImportError(
            "gdm-concordia is not installed. Install it with "
            "`pip install gdm-concordia` to use Concordia reuse. "
            "Verdi itself does not require it."
        ) from exc


# --------------------------------------------------------------------------- #
# Questionnaire reuse: the five value axes as a Concordia questionnaire
# --------------------------------------------------------------------------- #
def make_value_questionnaire(
    *,
    name: str = "d5_values",
    choices: tuple[str, ...] | list[str] = ("1", "2", "3", "4", "5"),
    description: str = "Five-axis declared-value self-report.",
) -> Any:
    """Build a Concordia ``QuestionnaireBase`` for the five value axes.

    Returns a fully constructed Concordia questionnaire whose ``questions`` map
    one-to-one onto :data:`VALUE_AXES` and whose ``dimensions`` are the axis
    names. Concordia's own ``process_answer`` then scores each response into a
    numeric per-dimension value, giving an independent structured readout that
    can be compared against Verdi's free-form interview self-assessment.

    Requires ``gdm-concordia`` to be installed.
    """
    _require_concordia()
    from concordia.contrib.data.questionnaires.base_questionnaire import (
        Question,
        QuestionnaireBase,
    )

    questions = [
        Question(
            statement=f"On a scale of {choices[0]}-{choices[-1]}, how strongly do you "
                      f"hold to {axis} right now?",
            dimension=axis,
            choices=list(choices),
            ascending_scale=True,
        )
        for axis in VALUE_AXES
    ]

    class _ValueQuestionnaire(QuestionnaireBase):
        def process_answer(self, player_name: str, answer_text: str, question: Any):
            return super().process_answer(player_name, answer_text, question)

        def aggregate_results(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            return {}

        def plot_results(self, *args: Any, **kwargs: Any) -> None:
            return None

    return _ValueQuestionnaire(
        name=name,
        description=description,
        questionnaire_type="multiple_choice",
        questions=questions,
        dimensions=list(VALUE_AXES),
        observation_preprompt="Please answer honestly as {player_name}.",
    )


def score_response(questionnaire: Any, player_name: str, answer_by_axis: dict[str, str]) -> dict[str, Any]:
    """Score raw per-axis answers through Concordia's own ``process_answer``.

    Args:
        questionnaire: A questionnaire built by :func:`make_value_questionnaire`.
        player_name: Name of the responding agent.
        answer_by_axis: Mapping of axis name -> raw answer text (e.g. ``"4"``).

    Returns:
        A mapping of axis name -> scored numeric value produced by Concordia.
        Axes absent from ``answer_by_axis`` are omitted.
    """
    by_dim = {q.dimension: q for q in questionnaire.questions}
    out: dict[str, Any] = {}
    for axis, raw in answer_by_axis.items():
        q = by_dim.get(axis)
        if q is None:
            continue
        _dim, value = questionnaire.process_answer(player_name, raw, q)
        out[axis] = value
    return out


__all__ = [
    "VALUE_AXES",
    "make_value_questionnaire",
    "score_response",
]
