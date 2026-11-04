"""Opt-in versioned constrained economy + objective task-success oracle.

This module is the DETERMINISTIC core of the P0-B "meaningful shock and
task environment" work: pure, stateless functions that enforce a finite
economy (per-tick production caps, production input costs, end-of-tick
consumption) and evaluate verifiable delivery objectives from committed
world state alone. Nothing here calls a model, reads the wall clock, or
consults RNG — every outcome is computable from (spec, state).

Semantics (documented choices)
------------------------------
* **Production limit = REJECT (not clamp).** An agent may produce at most
  ``production_limit_per_tick[resource]`` units per resource per tick. A
  ``produce`` action exceeding the cap is rejected by the validator with
  ``blocked_by="law:production_limit"`` so the rejection is visible in the
  event ledger and feeds blocked-action metrics. Clamping would silently
  rewrite the agent's intent; rejection keeps event truth separate from
  world-state truth like every other law violation.
* **Production cost = reject when unaffordable.** Producing N units of a
  resource may require spending ``cost_per_unit * N`` credits from the
  producer (finite input). If the producer holds fewer credits than the
  cost, the action is rejected with
  ``blocked_by="law:insufficient_production_input"`` — no partial
  production, no negative balances. On acceptance the reducer deducts the
  input and adds the output (conservation: inputs deducted, outputs added).
* **Consumption = end-of-tick deterministic deduction.** Each tick, after
  all actions are committed, every agent must pay ``consumption_per_tick``
  (global or per-resource/per-role) out of its OWN holdings. The deduction
  is clamped at zero (never overdrafts); any shortfall puts the agent into
  "deficit" for that tick, tracked in a world-level deficit counter. The
  whole step is recorded as ONE ledger event (kind ``consumption``, actor
  ``world``) whose payload carries the full per-agent breakdown, so replay
  reproduces it from the tape without re-deriving anything.
* **Delivery oracle = state-diff only.** A delivery objective succeeds iff
  at its due tick the designated target (world pool or an agent) holds at
  least ``amount`` MORE of the resource than it did at the objective's
  start tick. Success/failure is computed purely from two committed
  WorldState snapshots — never from agent self-report or LLM judgment —
  and is ledgered as ``task_completed`` / ``task_failed`` at the due tick.

Legacy behavior: when a scenario has no ``economy`` block, none of these
functions are invoked and every existing run keeps today's semantics
exactly (unlimited produce, no demand, no costs).
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from verdi.schemas import WorldState

#: Economy spec versions this engine understands.
ECONOMY_SPEC_VERSIONS = frozenset({1})


@dataclass(frozen=True)
class EconomySpec:
    """Parsed, validated ``economy`` scenario block (versioned).

    All numbers are finite and nonnegative. ``production_limit_per_tick``
    maps resource -> max units producible per agent per tick (a missing
    resource means unlimited for that resource). ``production_cost`` maps
    resource -> credit price per unit produced (missing = free).
    ``consumption_per_tick`` maps resource -> units each agent consumes at
    end of tick (missing = 0); ``role_consumption`` optionally overrides
    per (role, resource).
    """

    version: int
    production_limit_per_tick: dict[str, float]
    production_cost: dict[str, float]
    consumption_per_tick: dict[str, float]
    role_consumption: dict[tuple[str, str], float]

    @property
    def active(self) -> bool:
        return True


def parse_economy_spec(block: Mapping[str, Any] | None) -> EconomySpec | None:
    """Parse and validate the optional scenario ``economy`` block.

    Returns None when the block is absent (legacy semantics). Raises
    ValueError on malformed content so a researcher sees a clear config
    error instead of a silent degenerate run.
    """
    if not block:
        return None
    if not isinstance(block, Mapping):
        raise ValueError("economy block must be a mapping")
    version = block.get("version", 1)
    if version not in ECONOMY_SPEC_VERSIONS:
        raise ValueError(f"unsupported economy spec version {version!r}")

    limits = _nonneg_mapping(block.get("production_limit_per_tick"), "production_limit_per_tick")
    costs = _nonneg_mapping(block.get("production_cost"), "production_cost")
    consumption = _nonneg_mapping(block.get("consumption_per_tick"), "consumption_per_tick")
    role_block = block.get("role_consumption") or {}
    if not isinstance(role_block, Mapping):
        raise ValueError("role_consumption must be a mapping of role -> resource -> number")
    role_consumption: dict[tuple[str, str], float] = {}
    for role, resources in role_block.items():
        if not isinstance(role, str) or not role:
            raise ValueError("role_consumption role keys must be nonempty strings")
        res_map = _nonneg_mapping(resources, f"role_consumption.{role}")
        for resource, value in res_map.items():
            if resource not in consumption:
                # Overrides only apply to resources with a base rate; a
                # role-only entry used to be ignored without any error.
                raise ValueError(
                    f"role_consumption.{role}.{resource} overrides a resource missing "
                    "from consumption_per_tick; add a base rate there (0 is allowed)"
                )
            role_consumption[(role, resource)] = value
    return EconomySpec(
        version=int(version),
        production_limit_per_tick=limits,
        production_cost=costs,
        consumption_per_tick=consumption,
        role_consumption=role_consumption,
    )


def _nonneg_mapping(value: Any, key: str) -> dict[str, float]:
    """Parse a resource->number mapping; all numbers finite and nonnegative."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be a resource-to-number mapping")
    out: dict[str, float] = {}
    for resource, amount in value.items():
        if not isinstance(resource, str) or not resource:
            raise ValueError(f"{key} resource keys must be nonempty strings")
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            raise ValueError(f"{key}.{resource} must be a finite number")
        num = float(amount)
        if not math.isfinite(num) or num < 0:
            raise ValueError(f"{key}.{resource} must be finite and nonnegative")
        out[resource] = num
    return out


# --------------------------------------------------------------------------- #
# production limits + cost
# --------------------------------------------------------------------------- #
def check_production_limits(
    spec: EconomySpec,
    resource: str,
    amount: float,
    already_produced_this_tick: float,
) -> tuple[str, str] | None:
    """Return (rule key, explanation) if producing ``amount`` more of
    ``resource`` this tick exceeds the per-tick cap, else None."""
    cap = spec.production_limit_per_tick.get(resource)
    if cap is None:
        return None
    if amount > 0 and already_produced_this_tick + amount > cap + 1e-9:
        return (
            "max_produce_units_per_tick",
            f"producing {amount:g} {resource} exceeds the per-tick limit of "
            f"{cap:g} ({already_produced_this_tick:g} already produced)",
        )
    return None


def production_input_cost(spec: EconomySpec, resource: str, amount: float) -> float:
    """Credits required to produce ``amount`` of ``resource`` (0 if free)."""
    return spec.production_cost.get(resource, 0.0) * amount


def _affordable_holdings(
    agent_resources: Mapping[str, float], world_resources: Mapping[str, float],
) -> dict[str, float]:
    """The producer's effective credit balance for affordability checks.

    A producer may draw on its own credits AND withdraw from the shared
    commons pool (the same finite-input constraint as any other economy
    participant), so the affordable balance is personal + pool credits.
    """
    return {
        **dict(agent_resources),
        "credits": agent_resources.get("credits", 0.0)
        + world_resources.get("credits", 0.0),
    }


def deduct_production_cost(
    agent_resources: Mapping[str, float],
    world_resources: Mapping[str, float],
    cost: float,
) -> tuple[dict[str, float], dict[str, float]]:
    """Deduct the production input (credits): personal credits FIRST (the
    producer's own finite capital), and only the REMAINDER is drawn from the
    shared commons pool. This keeps the pool as a genuine common reserve
    rather than an infinite money printer. Conservation holds: credits move
    from personal/pool holdings to the produced output. Returns
    (new_agent_resources, new_world_resources). Raises ValueError when the
    combined balance cannot cover the cost.
    """
    if cost <= 0:
        return dict(agent_resources), dict(world_resources)
    personal = agent_resources.get("credits", 0.0)
    pool = world_resources.get("credits", 0.0)
    if personal + pool < cost - 1e-9:
        raise ValueError(
            f"production cost {cost:g} exceeds combined credits "
            f"(personal {personal:g} + pool {pool:g})"
        )
    take_personal = min(personal, cost)
    take_pool = max(0.0, cost - take_personal)
    new_agent = {**agent_resources, "credits": personal - take_personal}
    new_world = {**world_resources, "credits": pool - take_pool}
    return new_agent, new_world


def withdraw_into_holdings(
    agent_resources: Mapping[str, float],
    world_resources: Mapping[str, float],
    resource: str,
    amount: float,
) -> tuple[dict[str, float], dict[str, float]]:
    """Withdraw up to ``amount`` of ``resource`` from the shared pool into the
    agent's holdings (clamped at the pool balance; never negative). A
    deterministic, conservative variant of the withdraw action used by the
    constrained economy so producers can access the credit reserve they need
    for production inputs."""
    if amount <= 0:
        return dict(agent_resources), dict(world_resources)
    available = world_resources.get(resource, 0.0)
    take = min(amount, available)
    if take <= 0:
        return dict(agent_resources), dict(world_resources)
    new_agent = {**agent_resources, resource: agent_resources.get(resource, 0.0) + take}
    new_world = {**world_resources, resource: available - take}
    return new_agent, new_world


def effective_production_cap(
    spec: EconomySpec,
    resource: str,
    policy_rules: Mapping[str, Any] | None = None,
) -> float | None:
    """The enforceable per-tick production cap for ``resource``.

    The CONSTITUTIONAL key ``max_produce_units_per_tick`` (enacted via
    amendment) overrides the scenario ``economy`` block's cap when present —
    this is what makes an enacted amendment change enforcement at its
    effective tick. Returns None when no cap applies.
    """
    const_cap = (policy_rules or {}).get("max_produce_units_per_tick")
    if isinstance(const_cap, (int, float)) and not isinstance(const_cap, bool):
        return float(const_cap)
    return spec.production_limit_per_tick.get(resource)


def validate_production(
    spec: EconomySpec,
    actor_id: str,
    state: WorldState,
    *,
    resource: str,
    amount: float,
    already_produced_this_tick: float,
    policy_rules: Mapping[str, Any] | None = None,
) -> tuple[str, list[str], str] | None:
    """Full deterministic pre-check for a ``produce`` action under an active
    economy. Returns (blocked_by, codes, details) or None when accepted.

    Check order: per-tick cap first (``law:production_limit``; the
    constitutional ``max_produce_units_per_tick`` overrides the scenario
    cap when present), then affordability
    (``law:insufficient_production_input``).
    """
    cap = effective_production_cap(spec, resource, policy_rules)
    if cap is not None and amount > 0 and already_produced_this_tick + amount > cap + 1e-9:
        _key, details = (
            "max_produce_units_per_tick",
            f"producing {amount:g} {resource} exceeds the per-tick limit of "
            f"{cap:g} ({already_produced_this_tick:g} already produced)",
        )
        return "law:production_limit", ["production_limit"], details
    cost = production_input_cost(spec, resource, amount)
    if cost > 0:
        agent = state.agents.get(actor_id)
        personal = agent.resources.get("credits", 0.0) if agent is not None else 0.0
        pool = state.world_resources.get("credits", 0.0)
        if personal + pool < cost - 1e-9:
            return (
                "law:insufficient_production_input",
                ["insufficient_production_input"],
                f"agent {actor_id!r} has {personal:g} personal + {pool:g} pool "
                f"credits but producing {amount:g} {resource} costs {cost:g}",
            )
    return None


def apply_production_deduction(
    state: WorldState,
    actor_id: str,
    *,
    resource: str,
    amount: float,
    spec: EconomySpec,
) -> WorldState:
    """Deduct the credit input cost of an already-committed produce.

    The reducer has ALREADY added the produced units to the producer's
    holdings; this step removes the finite input (personal credits first,
    then the shared commons pool) so conservation holds: inputs deducted,
    outputs added. Pure — returns a new WorldState. Does NOT advance
    tick/event_cursor; the campaign records it as a state-only tape step
    (like rights_change / world_shock), keeping hash chain and replay exact.
    """
    agent = state.agents[actor_id]
    cost = production_input_cost(spec, resource, amount)
    new_agent_res, new_world = deduct_production_cost(
        agent.resources, state.world_resources, cost,
    )
    assert_all_nonnegative(new_agent_res)
    assert_all_nonnegative(new_world)
    new_agents = {
        aid: (a.model_copy(update={"resources": new_agent_res}) if aid == actor_id else a)
        for aid, a in state.agents.items()
    }
    return state.model_copy(update={"agents": new_agents, "world_resources": new_world})


# --------------------------------------------------------------------------- #
# consumption (end of tick)
# --------------------------------------------------------------------------- #
def consumption_need(spec: EconomySpec, role: str, agent_resources: Mapping[str, float]) -> dict[str, float]:
    """The per-agent consumption vector for one tick (role-aware)."""
    needs: dict[str, float] = {}
    for resource, base in spec.consumption_per_tick.items():
        need = spec.role_consumption.get((role, resource), base)
        if need > 0:
            needs[resource] = need
    del agent_resources  # needs are fixed by spec+role, not current holdings
    return needs


def apply_consumption(state: WorldState, spec: EconomySpec) -> tuple[WorldState, dict[str, Any]]:
    """Deterministic end-of-tick consumption over ALL agents.

    For each agent (sorted by id), each needed resource is deducted from
    the agent's own holdings, clamped at zero (no overdraft ever); the
    shortfall counts toward that agent's deficit for this tick. Returns the
    new WorldState plus the ledger payload::

        {"tick_consumed": {...per agent...}, "deficit_agents": [...]}

    The payload is fully determined by (state, spec), so a tape replay that
    applies the same step to the same state reproduces it exactly.
    """
    new_agents: dict[str, Any] = {}
    per_agent: dict[str, dict[str, float]] = {}
    deficit_agents: list[str] = []
    for aid in sorted(state.agents):
        agent = state.agents[aid]
        needs = consumption_need(spec, agent.role, agent.resources)
        if not needs:
            new_agents[aid] = agent
            continue
        resources = dict(agent.resources)
        paid: dict[str, float] = {}
        short: dict[str, float] = {}
        for resource, need in sorted(needs.items()):
            held = resources.get(resource, 0.0)
            take = min(held, need)
            if take > 0:
                resources[resource] = held - take
            paid[resource] = take
            if need - take > 1e-9:
                short[resource] = need - take
        if short:
            deficit_agents.append(aid)
            per_agent[aid] = {"paid": paid, "shortfall": short}
        elif paid:
            per_agent[aid] = {"paid": paid, "shortfall": {}}
        new_agents[aid] = agent.model_copy(update={"resources": resources})
    assert_all_nonnegative_in_state(new_agents, state.world_resources)
    nxt = state.model_copy(update={"agents": new_agents})
    payload = {
        "tick_consumed": per_agent,
        "deficit_agents": deficit_agents,
    }
    return nxt, payload


# --------------------------------------------------------------------------- #
# objective task-success oracle
# --------------------------------------------------------------------------- #
def pool_holding(state: WorldState, target_pool: str, resource: str) -> float:
    """Committed holding of ``resource`` at the designated target."""
    if target_pool == "world":
        return state.world_resources.get(resource, 0.0)
    agent = state.agents.get(target_pool)
    if agent is None:
        raise ValueError(f"delivery target {target_pool!r} is not the world pool or an agent")
    return agent.resources.get(resource, 0.0)


def check_delivery(objective: Mapping[str, Any], before: WorldState, after: WorldState) -> dict[str, Any]:
    """Evaluate one delivery objective from committed state only.

    The objective succeeded iff at ``after`` (the due-tick state) the target
    holds >= ``amount`` MORE of ``resource`` than at ``before`` (the
    objective's start state). Returns a deterministic outcome record::

        {"objective_id", "status": "completed"|"failed", "required",
         "baseline", "actual", "delta"}
    """
    objective_id = str(objective.get("objective_id", ""))
    resource = str(objective["resource"])
    required = float(objective["amount"])
    target = str(objective.get("target_pool", "world"))
    baseline = pool_holding(before, target, resource)
    actual = pool_holding(after, target, resource)
    delta = actual - baseline
    ok = delta >= required - 1e-9
    return {
        "objective_id": objective_id,
        "status": "completed" if ok else "failed",
        "required": required,
        "baseline": baseline,
        "actual": actual,
        "delta": delta,
        "resource": resource,
        "target_pool": target,
    }


# --------------------------------------------------------------------------- #
# tasks block parsing
# --------------------------------------------------------------------------- #
def parse_task_objectives(block: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Parse the optional scenario ``tasks`` block into normalized,
    deterministic delivery objectives (with stable ids + start ticks).

    Each entry needs ``due_tick`` (int >= 0), ``resource`` (nonempty str)
    and ``amount`` (positive finite number); optional ``start_tick``
    (default 0, must be <= due_tick) and ``target_pool`` (default "world").
    Returns [] when the block is absent (legacy: no objectives).
    """
    if not block:
        return []
    deliveries = block.get("deliveries") if isinstance(block, Mapping) else block
    if not isinstance(deliveries, list):
        raise ValueError("tasks block must carry a 'deliveries' list")
    objectives: list[dict[str, Any]] = []
    for i, entry in enumerate(deliveries):
        if not isinstance(entry, Mapping):
            raise ValueError(f"tasks.deliveries[{i}] must be a mapping")
        try:
            due_tick = int(entry["due_tick"])
            start_tick = int(entry.get("start_tick", 0))
            amount = float(entry["amount"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"tasks.deliveries[{i}] is malformed: {exc}") from exc
        resource = entry.get("resource")
        target = str(entry.get("target_pool", "world"))
        if not isinstance(resource, str) or not resource:
            raise ValueError(f"tasks.deliveries[{i}].resource must be a nonempty string")
        if due_tick < 0 or start_tick < 0 or start_tick > due_tick:
            raise ValueError(
                f"tasks.deliveries[{i}]: need 0 <= start_tick <= due_tick, got "
                f"{start_tick}..{due_tick}"
            )
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError(f"tasks.deliveries[{i}].amount must be a positive finite number")
        objective_id = str(entry.get("objective_id") or f"obj-{i:02d}")
        if any(o["objective_id"] == objective_id for o in objectives):
            # Baselines and outcomes are keyed by id: a duplicate (including an
            # auto id "obj-NN" colliding with an explicit one) would overwrite
            # another objective's baseline.
            raise ValueError(f"tasks.deliveries[{i}]: duplicate objective_id {objective_id!r}")
        objectives.append({
            "objective_id": objective_id,
            "resource": resource,
            "amount": amount,
            "target_pool": target,
            "start_tick": start_tick,
            "due_tick": due_tick,
        })
    return objectives


# --------------------------------------------------------------------------- #
# invariants
# --------------------------------------------------------------------------- #
def assert_all_nonnegative(resources: Mapping[str, float]) -> None:
    for res, val in resources.items():
        if val < -1e-9:
            raise AssertionError(f"negative balance: resource={res} value={val}")


def assert_all_nonnegative_in_state(
    agents: Mapping[str, Any], world_resources: Mapping[str, float]
) -> None:
    for aid, agent in agents.items():
        for res, val in agent.resources.items():
            if val < -1e-9:
                raise AssertionError(f"negative balance: agent={aid} resource={res} value={val}")
    for res, val in world_resources.items():
        if val < -1e-9:
            raise AssertionError(f"negative world balance: resource={res} value={val}")


__all__ = [
    "ECONOMY_SPEC_VERSIONS",
    "EconomySpec",
    "apply_consumption",
    "apply_production_deduction",
    "assert_all_nonnegative",
    "check_delivery",
    "check_production_limits",
    "consumption_need",
    "deduct_production_cost",
    "parse_economy_spec",
    "parse_task_objectives",
    "pool_holding",
    "production_input_cost",
    "validate_production",
    "withdraw_into_holdings",
]
