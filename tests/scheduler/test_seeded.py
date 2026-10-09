from verdi.scheduler import SeededScheduler

from .conftest import make_agent, make_task


def test_same_seed_gives_identical_order(world):
    agents = {a: make_agent(a, idle_ticks=i) for a, i in (("a1", 3), ("a2", 1), ("a3", 5), ("a4", 2))}
    tasks = [
        make_task(f"t{i}", actor_id)
        for i, actor_id in enumerate(("a2", "a4", "a1", "a3", "a2", "a3", "a1", "a4"))
    ]
    s1 = SeededScheduler(run_seed=42).schedule(tasks, world(agents)).order_sequence
    s2 = SeededScheduler(run_seed=42).schedule(list(reversed(tasks)), world(agents)).order_sequence
    assert s1 == s2
    assert len(s1) == 8


def test_different_seeds_can_reorder_ties(world):
    # all idle ticks equal -> only seed breaks ties
    agents = {a: make_agent(a, idle_ticks=0) for a in ("a1", "a2", "a3", "a4")}
    tasks = [make_task(f"t{i}", f"a{i % 4 + 1}") for i in range(8)]
    s1 = SeededScheduler(run_seed=1).schedule(tasks, world(agents)).order_sequence
    s2 = SeededScheduler(run_seed=2).schedule(tasks, world(agents)).order_sequence
    assert sorted(s1) == sorted(s2)  # both cover every task exactly once
    assert s1 != s2  # the seed changes the tie-break order


def test_idle_agents_get_tasks_first(world):
    agents = {
        "busy": make_agent("busy", idle_ticks=0),
        "idle": make_agent("idle", idle_ticks=10),
    }
    tasks = [make_task("t-busy", "busy"), make_task("t-idle", "idle")]
    schedule = SeededScheduler(run_seed=0).schedule(tasks, world(agents))
    assert tuple(schedule.order_sequence) == ("t-idle", "t-busy")


def test_sanctioned_agent_skipped_for_rights_tasks(world):
    agents = {
        "sane": make_agent("sane", idle_ticks=1),
        "sanctioned": make_agent("sanctioned", idle_ticks=9, under_sanction=True),
    }
    tasks = [
        make_task("t-rights", "sanctioned", constraints={"requires_rights": True}),
        make_task("t-free", "sanctioned", constraints={}),
        make_task("t-normal", "sane"),
    ]
    schedule = SeededScheduler(run_seed=3).schedule(tasks, world(agents))
    skipped = {s.task_id: s.reason for s in schedule.skipped}
    assert set(skipped) == {"t-rights"}
    assert "under_sanction" in skipped["t-rights"]
    assert set(schedule.order_sequence) == {"t-free", "t-normal"}


def test_unknown_actor_is_skipped(world):
    agents = {"a1": make_agent("a1")}
    tasks = [make_task("t-ok", "a1"), make_task("t-ghost", "ghost")]
    schedule = SeededScheduler(run_seed=1).schedule(tasks, world(agents))
    assert {s.task_id for s in schedule.skipped} == {"t-ghost"}
    assert tuple(schedule.order_sequence) == ("t-ok",)


def test_fairness_stats_count_per_agent(world):
    agents = {a: make_agent(a, idle_ticks=1) for a in ("a1", "a2", "a3")}
    tasks = [make_task(f"t{i}", f"a{i % 3 + 1}") for i in range(9)]
    schedule = SeededScheduler(run_seed=11).schedule(tasks, world(agents))
    assert schedule.fairness_stats == {"a1": 3, "a2": 3, "a3": 3}
