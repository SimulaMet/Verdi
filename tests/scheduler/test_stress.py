"""Scheduler stress fixture (WS-7): SeededScheduler stability under scale.

Pin, with deliberately adversarial (colliding-key) loads:
1. byte-identical determinism across scheduler instances and run orderings;
2. stable total ordering when every sort-key component but task_id collides;
3. scale soundness (runtime bound + exact permutation of the input);
4. scoped world-state sensitivity (a single agent's state moves only its
   own tasks, and the unperturbed world always yields the unperturbed order).

Test-only workstream: source bugs are findings, not fixes.
"""
from __future__ import annotations

import time

from verdi.scheduler import SeededScheduler

from .conftest import make_agent, make_task
from .fixtures_stress import (
    adversarial_load,
    adversarial_world,
    full_signature,
    rebuild_world,
    with_agent,
)


# ---------------------------------------------------------------------------
# 1. Determinism at scale
# ---------------------------------------------------------------------------
def test_determinism_at_scale_same_seed_identical_across_instances(world):
    """500 adversarial tasks: 3 fresh scheduler instances, same run_seed,
    including a reversed input list -> byte-identical full Schedule."""
    tasks = adversarial_load(500)
    w = adversarial_world(world)  # 10-agent clumped profile, two sanctioned

    sigs = [full_signature(SeededScheduler(run_seed=99).schedule(list(tasks), w)) for _ in range(3)]
    sigs.append(full_signature(SeededScheduler(run_seed=99).schedule(list(reversed(tasks)), w)))
    assert len(set(sigs)) == 1, "same run_seed must give a byte-identical Schedule"

    # order field is a contiguous 0..n-1 range in the canonical signature
    (order, _task, _agent), *_ = sigs[0][0]
    assert order == 0
    assert [a[0] for a in sigs[0][0]] == list(range(len(sigs[0][0])))


def test_determinism_at_scale_different_seeds_reorder_ties(world):
    """With clumped idle_ticks the digest tie-break is active at scale:
    two different run_seeds must produce the same PERMUTATION but a
    DIFFERENT order (documented in seeded.py: 'different seeds can reorder
    equal-idle ties' — here verified for a 500-task adversarial load)."""
    w = adversarial_world(world)
    tasks = adversarial_load(500)
    s1 = SeededScheduler(run_seed=1).schedule(tasks, w)
    s2 = SeededScheduler(run_seed=2).schedule(tasks, w)
    assert set(s1.order_sequence) == set(s2.order_sequence)
    assert s1.order_sequence != s2.order_sequence, (
        "seed-derived digest tie-break must be able to reorder equal-idle ties"
    )


# ---------------------------------------------------------------------------
# 2. Stable tie-breaks under full key collision
# ---------------------------------------------------------------------------
def _fully_colliding_tasks(n: int = 200) -> list:
    """EVERY sort-key component but task_id collides: one agent, one
    idle_ticks value, no distinguishing fields other than the id."""
    return [make_task(f"c{i:04d}", "solo") for i in range(n)]


def test_full_collision_total_order_is_deterministic(world):
    agents = {"solo": make_agent("solo", idle_ticks=4)}
    w = rebuild_world(adversarial_world(world), agents)
    tasks = _fully_colliding_tasks(200)

    s1 = SeededScheduler(run_seed=7).schedule(list(tasks), w).order_sequence
    s2 = SeededScheduler(run_seed=7).schedule(list(reversed(tasks)), w).order_sequence
    assert s1 == s2
    assert len(set(s1)) == 200


def test_full_collision_sort_key_is_total_world(world):
    """The last-resort key must make the full _sort_key unique per task:
    no two tasks may compare equal on the full key (else the order is only
    an artifact of list position, not a total order over the set)."""
    agents = {"solo": make_agent("solo", idle_ticks=4)}
    w = rebuild_world(adversarial_world(world), agents)
    tasks = _fully_colliding_tasks(200)
    sched = SeededScheduler(run_seed=7)

    keys = [sched._sort_key(t, w) for t in tasks]
    assert len(set(keys)) == len(keys), "full sort key must be unique per task"
    # and the sorted key order matches the produced assignment order
    assert [k[2] for k in sorted(keys)] == list(sched.schedule(tasks, w).order_sequence)


# ---------------------------------------------------------------------------
# 3. Scale soundness
# ---------------------------------------------------------------------------
def test_scale_soundness_1000_tasks(world):
    w = adversarial_world(world)
    tasks = adversarial_load(1000)
    start = time.perf_counter()
    schedule = SeededScheduler(run_seed=5).schedule(tasks, w)
    elapsed = time.perf_counter() - start

    # Loose sanity bound; if this ever trips, report the observed time as a
    # finding before touching the bound.
    assert elapsed < 2.0, f"scheduling 1000 tasks took {elapsed:.3f}s (bound 2s)"

    # Exact permutation: every task exactly once across assigned + skipped,
    # nothing lost or duplicated.
    all_ids = list(schedule.order_sequence) + [s.task_id for s in schedule.skipped]
    assert sorted(all_ids) == sorted(t.task_id for t in tasks)
    assert len(set(all_ids)) == 1000
    # order column is contiguous over assignments
    assert [a.order for a in schedule.assignments] == list(range(len(schedule.assignments)))


# ---------------------------------------------------------------------------
# 4. World perturbation sensitivity
# ---------------------------------------------------------------------------
def test_unperturbed_world_gives_unperturbed_order(world):
    """No accidental cross-dependency: the scheduler output is a pure
    function of (run_seed, tasks, world) — identical inputs, identical output."""
    w = adversarial_world(world)
    tasks = adversarial_load(300)
    base = full_signature(SeededScheduler(run_seed=3).schedule(tasks, w))
    again = full_signature(SeededScheduler(run_seed=3).schedule(list(tasks), w))
    assert base == again


def test_perturbed_agent_moves_only_its_own_tasks(world):
    """_sort_key reads only the acting agent's idle_ticks: bumping one
    agent's idle_ticks must move ONLY that agent's tasks (all the way to
    the front) and leave the relative order of every other task's block,
    and of each other agent's own block, untouched."""
    agents = {f"a{i}": make_agent(f"a{i}", idle_ticks=0) for i in range(4)}  # full collision
    w = rebuild_world(adversarial_world(world), agents)
    tasks = [make_task(f"p{i:03d}", f"a{i % 4}") for i in range(40)]
    base = SeededScheduler(run_seed=11).schedule(tasks, w)

    perturbed = with_agent(w, "a2", idle_ticks=10**6)
    result = SeededScheduler(run_seed=11).schedule(tasks, perturbed)

    # still a valid full permutation (nothing lost/duplicated, no skips)
    assert result.skipped == ()
    assert sorted(result.order_sequence) == sorted(t.task_id for t in tasks)

    # only a2's tasks changed side: they form the exact prefix
    a2_ids = {t.task_id for t in tasks if t.actor_id == "a2"}
    prefix = tuple(result.order_sequence[: len(a2_ids)])
    assert set(prefix) == a2_ids

    # relative order of each non-a2 block is invariant (a2 simply removed)
    actor_of = {t.task_id: t.actor_id for t in tasks}
    for a in ("a0", "a1", "a3"):
        base_rest = [x for x in base.order_sequence if actor_of[x] == a]
        new_rest = [x for x in result.order_sequence if actor_of[x] == a]
        assert new_rest == base_rest, f"perturbing a2 must not reorder {a}'s tasks"
    # a2's own block keeps its base relative order (pure promotion to front)
    base_a2 = [x for x in base.order_sequence if actor_of[x] == "a2"]
    assert list(prefix) == base_a2
