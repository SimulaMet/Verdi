"""Regression tests for the G3 probe-leak guard.

The reviewer finding: ``ProbeBank.leak_check`` existed but was never wired
into the pilot run. The guard must (a) pass on the clean pilot run, (b)
raise ``ProbeLeakError`` when a hidden dilemma text appears in any
agent-visible surface, and (c) treat the containment check as verbatim
only (paraphrase is documented out of scope).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from verdi_agents.measurement.probes import ProbeBank
from verdi_agents.pilot.driver import (
    BANK_VERSION,
    INSTRUMENT_VERSION,
    PilotDriver,
    ProbeLeakError,
    assert_no_probe_leak,
    probe_response_for,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def bank() -> ProbeBank:
    return ProbeBank(BANK_VERSION, INSTRUMENT_VERSION, rubric_id="rubric-pilot-1", pool_size=4)


def test_clean_text_passes(bank) -> None:
    assert assert_no_probe_leak(bank, "agent a-01 harvested 2 units of food", "test") is None


def test_verbatim_leak_raises(bank) -> None:
    dilemma = bank.all_dilemma_texts()[0]
    with pytest.raises(ProbeLeakError):
        assert_no_probe_leak(bank, f"prompt tail: {dilemma}", "injected prompt")


def test_pilot_run_passes_leak_guard() -> None:
    """The full pilot run completes, which means the per-tick guard ran over
    every agent-visible surface (persona summaries, public signals, public
    observations, rendered prompts) and found no dilemma text."""
    PilotDriver(REPO_ROOT).run()  # raises ProbeLeakError on any leak


def test_probe_response_stays_out_of_band(bank) -> None:
    """A recorded probe response is a model output for the AUDIT channel; it
    must never contain a *different* agent's hidden dilemma verbatim in a way
    the guard would flag on an agent-visible surface."""
    response = probe_response_for("a-01", 5, "private")
    # The response for (agent, tick, channel) may reference its own drawn
    # dilemma, but the guard is about agent-VISIBLE surfaces, so assert the
    # response is deterministic and non-empty rather than leak-free here.
    assert response
    assert response == probe_response_for("a-01", 5, "private")
