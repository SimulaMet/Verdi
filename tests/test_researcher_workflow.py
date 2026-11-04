"""Offline end-to-end usability test for the researcher workflow.

Follows exactly what docs/RESEARCHER_GUIDE.md tells a nontechnical researcher
to do, WITHOUT any network or model endpoint:

1. Load examples/paired_institutional_recovery.yaml through the paired
   runner's own validation path (load_paired_config).
2. Assert the base scenario loads and both arms' resolved scenarios differ
   ONLY in institutional_condition.
3. Lint-style doc checks: RESEARCHER_GUIDE.md exists, mentions the exact CLI
   command string, and links to the example config — so the guide cannot rot
   silently if the command or path changes.
"""
from __future__ import annotations

import copy
from pathlib import Path

from verdi_agents.analysis.scenario import load_scenario
from verdi_agents.experiments.paired_run import build_arm_scenarios, load_paired_config

REPO_ROOT = Path(__file__).resolve().parent.parent
PAIRED_CONFIG = REPO_ROOT / "examples" / "paired_institutional_recovery.yaml"
GUIDE = REPO_ROOT / "docs" / "RESEARCHER_GUIDE.md"

#: The exact one-line command the guide must teach researchers.
CLI_COMMAND = (
    "verdi-agents pair examples/paired_institutional_recovery.yaml"
)


def test_example_paired_config_validates_offline():
    """The shipped example is the first thing a researcher runs; it must
    validate cleanly with no endpoint configured."""
    assert PAIRED_CONFIG.is_file(), "examples/paired_institutional_recovery.yaml missing"
    cfg = load_paired_config(PAIRED_CONFIG)  # raises SystemExit on any user error
    assert cfg["experiment_id"] == "institutional-recovery"
    assert cfg["arms"] == ["fixed", "revisable"]
    assert cfg["seeds"] == [42]
    assert cfg["output_root"] == "runs/paired-institutional-recovery"


def test_arms_differ_only_in_institutional_condition():
    """The scientific core of the study: identical exogenous circumstances,
    only the condition differs."""
    cfg = load_paired_config(PAIRED_CONFIG)
    base_path = REPO_ROOT / cfg["base_scenario"]
    assert base_path.is_file(), f"base scenario missing: {cfg['base_scenario']}"
    base = load_scenario(base_path)

    built = build_arm_scenarios(base, cfg["arms"], cfg["total_ticks_override"])
    fixed, revisable = built["fixed"], built["revisable"]
    assert fixed["institutional_condition"] == "fixed"
    assert revisable["institutional_condition"] == "revisable"

    minus = lambda sc: {k: v for k, v in sc.items() if k != "institutional_condition"}  # noqa: E731
    diff_keys = sorted(
        {k for k in set(minus(fixed)) | set(minus(revisable))
         if minus(fixed).get(k) != minus(revisable).get(k)}
    )
    assert diff_keys == [], f"arms diverge beyond condition: {diff_keys}"

    # Deep-equality, not just key-level: nested blocks must be untouched.
    assert copy.deepcopy(minus(fixed)) == minus(revisable)


def test_researcher_guide_exists_and_is_honest():
    """Docs lint: the guide must exist, teach the exact command, and link to
    the example config it documents."""
    assert GUIDE.is_file(), "docs/RESEARCHER_GUIDE.md missing"
    text = GUIDE.read_text(encoding="utf-8")
    assert CLI_COMMAND in text, (
        "guide must contain the exact command:\n  " + CLI_COMMAND
    )
    assert "examples/paired_institutional_recovery.yaml" in text, (
        "guide must link to the example paired config"
    )
    # The glossary caveat about seeds is a scientific honesty requirement.
    lowered = text.lower()
    assert "deterministic" in lowered and "seed" in lowered


def test_guide_links_to_related_docs():
    """The guide should point at the gallery and the limitations doc so a
    researcher can find honest caveats without hunting."""
    text = GUIDE.read_text(encoding="utf-8")
    assert "INSTITUTIONAL_RECOVERY.md" in text, "guide must link the limitations doc"
    assert "scenario gallery" in text.lower(), "guide must point at the scenario gallery"
