"""Deterministic action validator (Lane W).

Replaces the pilot's hardcoded ``accepted=True``. Pure and deterministic:
no model call, no wall clock, no RNG. Inspects the committed
:class:`WorldState` and the :class:`ProposedAction` and returns a
:class:`ValidationResult`. Never mutates state; a rejection must be
recorded as an event upstream (reducer contract).
"""
from __future__ import annotations

from typing import Any

from verdi.schemas import ProposedAction, ValidationResult, WorldState

#: Kinds whose property-moving capability is withdrawn under sanction.
SANCTION_BLOCKED_KINDS = frozenset({"transfer", "deposit"})

_KNOWN_KINDS = frozenset({"transfer", "deposit", "withdraw", "produce", "emit_signal"})


def validate_action(state: WorldState, action: ProposedAction) -> ValidationResult:
    """Deterministically validate ``action`` against committed ``state``.

    Rules (in check order):
    * Unknown actor -> rejected, ``blocked_by="law:unknown_actor"``.
    * Under sanction + kind in {"transfer", "deposit"} -> rejected,
      ``blocked_by="law:under_sanction"``.
    * transfer: needs "to", "resource", "units"; unknown recipient ->
      ``law:unknown_recipient``; would go negative -> ``law:no_double_spend``
      with code "overdraft".
    * deposit/withdraw: needs "resource", "amount"; deposit additionally
      requires the agent to hold the amount -> ``law:no_double_spend`` with
      code "overdraft"; withdraw requires the world pool to hold the amount ->
      ``law:insufficient_world``.
    * produce: needs "resource", "amount" > 0 -> else ``law:non_positive_amount``.
    * emit_signal: needs non-empty str "text" -> else ``law:empty_signal``.
    * Unknown kind -> ``law:unknown_kind``.
    """
    actor = state.agents.get(action.actor_id)
    if actor is None:
        return _rejected(
            "law:unknown_actor",
            ["unknown_actor"],
            f"actor {action.actor_id!r} is not present in world state",
        )

    if actor.under_sanction and action.action_kind in SANCTION_BLOCKED_KINDS:
        return _rejected(
            "law:under_sanction",
            ["under_sanction"],
            f"agent {action.actor_id!r} under sanction may not {action.action_kind}",
        )

    kind = action.action_kind
    args = action.arguments

    if kind == "transfer":
        for key in ("to", "resource", "units"):
            if key not in args:
                return _rejected(
                    "law:missing_argument",
                    ["missing_argument"],
                    f"transfer missing required argument {key!r}",
                )
        recipient_id = args["to"]
        if recipient_id not in state.agents:
            return _rejected(
                "law:unknown_recipient",
                ["unknown_recipient"],
                f"transfer recipient {recipient_id!r} is not present in world state",
            )
        resource = args["resource"]
        units = _as_float(args["units"])
        if units is None:
            return _rejected(
                "law:no_double_spend",
                ["overdraft"],
                f"transfer units {args['units']!r} is not a positive number",
            )
        held = actor.resources.get(resource, 0.0)
        if held - units < 0:
            return _rejected(
                "law:no_double_spend",
                ["overdraft"],
                f"agent {action.actor_id!r} holds {held} {resource!r}, cannot transfer {units}",
            )
        return _accepted()

    if kind in ("deposit", "withdraw"):
        for key in ("resource", "amount"):
            if key not in args:
                return _rejected(
                    "law:missing_argument",
                    ["missing_argument"],
                    f"{kind} missing required argument {key!r}",
                )
        resource = args["resource"]
        amount = _as_float(args["amount"])
        if amount is None:
            return _rejected(
                "law:non_positive_amount",
                ["non_positive_amount"],
                f"{kind} amount {args['amount']!r} is not a positive number",
            )
        if kind == "deposit":
            held = actor.resources.get(resource, 0.0)
            if held - amount < 0:
                return _rejected(
                    "law:no_double_spend",
                    ["overdraft"],
                    f"agent {action.actor_id!r} holds {held} {resource!r}, cannot deposit {amount}",
                )
        elif kind == "withdraw":
            available = state.world_resources.get(resource, 0.0)
            if available - amount < 0:
                return _rejected(
                    "law:insufficient_world",
                    ["insufficient_world"],
                    f"world pool holds {available} {resource!r}, cannot withdraw {amount}",
                )
        return _accepted()

    if kind == "produce":
        for key in ("resource", "amount"):
            if key not in args:
                return _rejected(
                    "law:missing_argument",
                    ["missing_argument"],
                    f"produce missing required argument {key!r}",
                )
        amount = _as_float(args["amount"])
        if amount is None:
            return _rejected(
                "law:non_positive_amount",
                ["non_positive_amount"],
                f"produce amount {args['amount']!r} is not a positive number",
            )
        return _accepted()

    if kind == "emit_signal":
        text = args.get("text")
        if not isinstance(text, str) or not text:
            return _rejected(
                "law:empty_signal",
                ["empty_signal"],
                f"emit_signal text must be a non-empty string, got {text!r}",
            )
        return _accepted()

    return _rejected(
        "law:unknown_kind",
        ["unknown_kind"],
        f"action kind {kind!r} is not in {sorted(_KNOWN_KINDS)}",
    )


def _accepted() -> ValidationResult:
    return ValidationResult(accepted=True, blocked_by=None)


def _rejected(blocked_by: str, codes: list[str], details: str) -> ValidationResult:
    return ValidationResult(
        accepted=False,
        blocked_by=blocked_by,
        attempted_violation_codes=list(codes),
        details=details,
    )


def _as_float(value: Any) -> float | None:
    """Positive finite float, or None if the value is not one."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if amount != amount or amount in (float("inf"), float("-inf")):
        return None
    return amount if amount > 0 else None
