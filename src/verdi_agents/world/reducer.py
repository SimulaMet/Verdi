"""Deterministic world reducer.

The reducer is the ONLY component allowed to transition world state
(ARCHITECTURE.md: "Only deterministic reducer mutates world state"). It
takes (WorldState, ProposedAction, ValidationResult) and returns a NEW
frozen WorldState; inputs are never mutated.

Invariant policy
----------------
* Tick/event monotonicity: every processed attempt (accepted or rejected)
  advances ``tick`` and ``event_cursor`` by exactly 1.
* Accepted actions apply the documented mutation for their
  ``action_kind``; rejected actions change NO substantive content
  (agents, resources, laws) -- they only advance bookkeeping counters,
  and the transition still produces a ledger event with
  ``decision='rejected'``.
* No double-spend / no negative balances: an accepted action whose
  application would leave a negative balance, or that names an unknown
  actor, raises :class:`InvariantViolationError` instead of returning a
  broken state.

``canonical_state_hash`` hashes substantive world content only. It
excludes ``state_hash`` (self-reference) plus ``tick`` and
``event_cursor`` (ledger bookkeeping fully determined by the event log),
so a rejected attempt -- which changes nothing but bookkeeping -- leaves
the state hash unchanged.
"""
from __future__ import annotations

from typing import Any

from verdi_agents.schemas import ProposedAction, ValidationResult, WorldEvent, WorldState
from verdi_agents.world.canonical import canonical_sha256

GENESIS_HASH = "genesis"

#: Maximum public signals retained per agent (oldest dropped).
#: Prevents unbounded prompt growth from accumulated signals.
_MAX_PUBLIC_SIGNALS = 20

#: Accepted action kinds that mutate substantive state. Any other accepted
#: kind is record-only (validated, logged, no state mutation).
MUTATING_KINDS = frozenset({"transfer", "deposit", "withdraw", "produce", "emit_signal"})


class InvariantViolationError(RuntimeError):
    """Raised when an accepted action would break a world invariant."""


class WorldReducer:
    """Pure, deterministic transition function for :class:`WorldState`."""

    def apply(
        self,
        state: WorldState,
        action: ProposedAction,
        validation: ValidationResult,
    ) -> WorldState:
        """Return a NEW WorldState after one attempted action.

        Raises :class:`InvariantViolationError` when an accepted action
        would violate no-double-spend / no-negative-balance or names an
        unknown actor.
        """
        tick = state.tick + 1
        event_cursor = state.event_cursor + 1

        if action.action_kind == "governance":
            # Governance transitions carry rule-version bookkeeping only:
            # when an amendment takes effect the payload/arguments hold the
            # enacted constitution version. No agent or resource content
            # changes, so replay from the recorded tape is exact.
            update: dict[str, Any] = {"tick": tick, "event_cursor": event_cursor}
            if validation.accepted:
                new_constitution = action.arguments.get("constitution_version")
                if isinstance(new_constitution, str) and new_constitution:
                    update["constitution_version"] = new_constitution
            return self._with_hash(state.model_copy(update=update))

        if not validation.accepted:
            nxt = state.model_copy(update={"tick": tick, "event_cursor": event_cursor})
            return self._with_hash(nxt)

        if action.action_kind == "task_objective" or action.actor_id == "world":
            # Deterministic record-only world steps (P0-B delivery-objective
            # outcomes): content is unchanged by construction (before ==
            # after); only the ledger bookkeeping advances. The campaign's
            # replay path rebuilds these events from the recorded tape
            # payload, so they never reach _apply_mutation.
            nxt = state.model_copy(update={"tick": tick, "event_cursor": event_cursor})
            return self._with_hash(nxt)

        agents, world_resources = self._apply_mutation(state, action)
        nxt = state.model_copy(
            update={
                "tick": tick,
                "event_cursor": event_cursor,
                "agents": agents,
                "world_resources": world_resources,
            }
        )
        return self._with_hash(nxt)

    # ------------------------------------------------------------------ #
    # canonical hashing
    # ------------------------------------------------------------------ #
    @staticmethod
    def canonical_state_hash(state: WorldState) -> str:
        """Hex sha256 of canonical JSON (sorted keys, ensure_ascii=False).

        Hashes substantive content only; ``state_hash``, ``tick`` and
        ``event_cursor`` are excluded (see module docstring).
        """
        data = state.model_dump(mode="json")
        for excluded in ("state_hash", "tick", "event_cursor"):
            data.pop(excluded, None)
        return canonical_sha256(data)

    # ------------------------------------------------------------------ #
    # event construction
    # ------------------------------------------------------------------ #
    def build_event(
        self,
        before: WorldState,
        after: WorldState,
        action: ProposedAction,
        validation: ValidationResult,
        prev_hash: str,
    ) -> WorldEvent:
        """Build the ledger event for one transition (accepted or rejected).

        ``event_hash`` is left empty: :class:`~verdi_agents.world.log.EventLog`
        is the single writer that fills it on append so the chain is
        computed in exactly one place.
        """
        accepted = validation.accepted
        payload: dict[str, Any] = {
            "task_id": action.task_id,
            "action_kind": action.action_kind,
            "arguments": action.arguments,
        }
        if not accepted:
            payload.update(
                {
                    "blocked_by": validation.blocked_by,
                    "attempted_violation_codes": validation.attempted_violation_codes,
                    "details": validation.details,
                }
            )
        event_id = before.event_cursor  # monotonic, 0-based
        return WorldEvent(
            event_id=event_id,
            tick=after.tick,
            cause_event_id=event_id - 1 if event_id > 0 else None,
            actor_id=action.actor_id,
            kind="action_accepted" if accepted else "action_rejected",
            payload=payload,
            before_hash=self.canonical_state_hash(before),
            after_hash=self.canonical_state_hash(after) if accepted else None,
            decision="accepted" if accepted else "rejected",
            prev_hash=prev_hash,
            event_hash="",
        )

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #
    @staticmethod
    def _with_hash(state: WorldState) -> WorldState:
        return state.model_copy(update={"state_hash": WorldReducer.canonical_state_hash(state)})

    def _apply_mutation(
        self, state: WorldState, action: ProposedAction
    ) -> tuple[dict[str, Any], dict[str, float]]:
        """Return new (agents, world_resources) content for an accepted action."""
        if action.actor_id not in state.agents:
            raise InvariantViolationError(f"accepted action by unknown actor: {action.actor_id!r}")
        agents: dict[str, Any] = {aid: a.model_copy(deep=True) for aid, a in state.agents.items()}
        world_resources = dict(state.world_resources)

        kind = action.action_kind
        agent = agents[action.actor_id]
        if kind == "transfer":
            self._transfer(agent, action.actor_id, agents, action.arguments)
        elif kind == "deposit":
            self._agent_to_world(agent, action.actor_id, agents, world_resources, action.arguments)
        elif kind == "withdraw":
            self._world_to_agent(agent, action.actor_id, agents, world_resources, action.arguments)
        elif kind == "produce":
            resource = str(_arg(action.arguments, "resource"))
            amount = _positive_amount(action.arguments)
            resources = {**agent.resources, resource: agent.resources.get(resource, 0.0) + amount}
            agents[action.actor_id] = agent.model_copy(update={"resources": resources})
        elif kind == "emit_signal":
            text = str(_arg(action.arguments, "text")).strip()
            # Drop a verbatim repeat of a signal the agent already sent: with
            # temperature-0 sampling an unchanged situation regenerates identical
            # text, which just clutters neighbors' context. A near-new signal is
            # still appended (only exact duplicates are suppressed), so genuine
            # re-emphasis with different wording is preserved.
            existing = [s for s in agent.public_signals if s.strip()]
            if text and text not in existing:
                signals = [*(existing + [text])][-_MAX_PUBLIC_SIGNALS:]
                agents[action.actor_id] = agent.model_copy(update={"public_signals": signals})
        # Any other accepted kind is record-only: no substantive mutation.

        _assert_nonnegative(agents, world_resources)
        return agents, world_resources

    @staticmethod
    def _transfer(sender, sender_id: str, agents: dict[str, Any], arguments: dict[str, Any]) -> None:
        recipient_id = str(_arg(arguments, "to"))
        if recipient_id == sender_id:
            # Reading the recipient before writing the sender back would turn
            # a self-transfer into money creation; the validator rejects it,
            # so reaching here is an invariant breach.
            raise InvariantViolationError(f"self-transfer by agent {sender_id!r}")
        recipient = agents.get(recipient_id)
        if recipient is None:
            raise InvariantViolationError(f"transfer to unknown agent: {recipient_id!r}")
        resource = str(_arg(arguments, "resource"))
        amount = _positive_amount(arguments, key="units")
        sender_res = {**sender.resources, resource: sender.resources.get(resource, 0.0) - amount}
        agents[sender_id] = sender.model_copy(update={"resources": sender_res})
        rec_res = {**recipient.resources, resource: recipient.resources.get(resource, 0.0) + amount}
        agents[recipient_id] = recipient.model_copy(update={"resources": rec_res})

    @staticmethod
    def _agent_to_world(agent, agent_id: str, agents: dict[str, Any], world_resources: dict[str, float], arguments: dict[str, Any]) -> None:
        resource = str(_arg(arguments, "resource"))
        amount = _positive_amount(arguments)
        res = {**agent.resources, resource: agent.resources.get(resource, 0.0) - amount}
        agents[agent_id] = agent.model_copy(update={"resources": res})
        world_resources[resource] = world_resources.get(resource, 0.0) + amount

    @staticmethod
    def _world_to_agent(agent, agent_id: str, agents: dict[str, Any], world_resources: dict[str, float], arguments: dict[str, Any]) -> None:
        resource = str(_arg(arguments, "resource"))
        amount = _positive_amount(arguments)
        world_resources[resource] = world_resources.get(resource, 0.0) - amount
        res = {**agent.resources, resource: agent.resources.get(resource, 0.0) + amount}
        agents[agent_id] = agent.model_copy(update={"resources": res})


def canonical_state_hash(state: WorldState) -> str:
    """Module-level alias of :meth:`WorldReducer.canonical_state_hash`."""
    return WorldReducer.canonical_state_hash(state)


# ---------------------------------------------------------------------- #
# helpers
# ---------------------------------------------------------------------- #
def _arg(arguments: dict[str, Any], key: str) -> Any:
    if key not in arguments:
        raise InvariantViolationError(f"accepted action missing required argument {key!r}")
    return arguments[key]


def _positive_amount(arguments: dict[str, Any], *, key: str = "amount") -> float:
    value = _arg(arguments, key)
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise InvariantViolationError("non-numeric amount in accepted action") from exc
    if amount <= 0:
        raise InvariantViolationError(
            f"non-positive amount {amount!r}; reducer only applies transfers of positive value"
        )
    return amount


def _assert_nonnegative(agents: dict[str, Any], world_resources: dict[str, float]) -> None:
    for aid, agent in agents.items():
        for res, val in agent.resources.items():
            if val < 0:
                raise InvariantViolationError(
                    f"negative balance: agent={aid} resource={res} value={val}"
                )
    for res, val in world_resources.items():
        if val < 0:
            raise InvariantViolationError(f"negative world balance: resource={res} value={val}")
