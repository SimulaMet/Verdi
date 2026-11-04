"""G0/G1 tests for the hidden probe bank (P1-D, G3 isolation defense)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from verdi_agents.measurement import ProbeBank
from verdi_agents.schemas import ProbeItem


def _bank() -> ProbeBank:
    return ProbeBank("bank-1", "probe-v1", rubric_id="rubric-1", pool_size=4)


def test_channels_are_distinct_pools() -> None:
    bank = _bank()
    assert bank.channels_distinct()
    declared = set(bank.all_dilemma_texts("declared"))
    private = set(bank.all_dilemma_texts("private"))
    public = set(bank.all_dilemma_texts("public"))
    assert declared.isdisjoint(private)
    assert declared.isdisjoint(public)
    assert private.isdisjoint(public)


def test_draw_returns_valid_probe_items() -> None:
    bank = _bank()
    for channel in ("declared", "private", "public"):
        item = bank.draw("a1", tick=0, channel=channel, rng_seed=42)
        assert isinstance(item, ProbeItem)
        assert item.channel == channel
        assert item.bank_version == "bank-1"
        assert item.instrument_version == "probe-v1"
        assert item.scoring_rubric_id == "rubric-1"
        assert item.options and item.dilemma_text
    with pytest.raises(ValueError):
        bank.draw("a1", 0, "leaked", rng_seed=42)


def test_rotation_same_seed_replays_identically() -> None:
    bank = _bank()
    schedule_a = [bank.draw("a1", t, "private", rng_seed=7).probe_id for t in range(8)]
    schedule_b = [bank.draw("a1", t, "private", rng_seed=7).probe_id for t in range(8)]
    assert schedule_a == schedule_b


def test_rotation_different_seed_differs() -> None:
    bank = _bank()
    s0 = [bank.draw("a1", t, "private", rng_seed=1).probe_id for t in range(8)]
    s1 = [bank.draw("a1", t, "private", rng_seed=2).probe_id for t in range(8)]
    assert s0 != s1


def test_no_repeat_within_epoch_per_agent() -> None:
    bank = _bank()
    epoch = bank.pool_size
    for agent in ("a1", "a2"):
        for channel in ("declared", "private", "public"):
            for seed in (3, 99):
                ids = [
                    bank.draw(agent, t, channel, rng_seed=seed).probe_id
                    for t in range(epoch)
                ]
                assert len(set(ids)) == epoch, (agent, channel, seed)
    # Epoch restart uses a different permutation (schedule keeps rotating).
    t0 = bank.draw("a1", 0, "private", rng_seed=5).probe_id
    t1 = bank.draw("a1", 4, "private", rng_seed=5).probe_id
    assert t0 != t1


def test_per_agent_schedules_differ_across_agents() -> None:
    bank = _bank()
    a1 = [bank.draw("a1", t, "public", rng_seed=11).probe_id for t in range(4)]
    a2 = [bank.draw("a2", t, "public", rng_seed=11).probe_id for t in range(4)]
    assert a1 != a2


def test_leak_check_flags_embedded_dilemma() -> None:
    bank = _bank()
    secret = bank.draw("a1", 0, "private", rng_seed=42).dilemma_text
    # Agent memory accidentally contains the hidden dilemma verbatim.
    leaked_memory = f"Recalling last tick: {secret} My answer: yes"
    hits = bank.leak_check(leaked_memory)
    assert secret in hits
    assert len(hits) >= 1


def test_leak_check_passes_clean_text() -> None:
    bank = _bank()
    clean = "I value fairness and I will trade openly with teammates."
    assert bank.leak_check(clean) == []


def test_bank_is_frozen_schema_compatible() -> None:
    # ProbeItem is a frozen model: mutation attempts must fail.
    item = _bank().draw("a1", 0, "declared", rng_seed=1)
    with pytest.raises(ValidationError):
        item.dilemma_text = "mutated"
