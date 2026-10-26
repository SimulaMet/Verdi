"""Deterministic, explicitly typed constitutional policies.

Unknown constitution keys remain narrative/governance metadata. Only the
recognized names below affect action enforcement; no dynamic code execution.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

ENFORCEABLE_KEYS = frozenset({
    "max_transfer_units", "max_withdraw_units", "min_commons_reserve",
})


def validate_enforceable_rules(rules: Mapping[str, Any]) -> None:
    """Reject malformed recognized policy keys, preserving old narrative rules."""
    for key in ("max_transfer_units", "max_withdraw_units"):
        if key in rules:
            _number(rules[key], key, strictly_positive=True)
    if "min_commons_reserve" in rules:
        reserves = rules["min_commons_reserve"]
        if not isinstance(reserves, Mapping):
            raise ValueError("min_commons_reserve must be a resource-to-number mapping")
        for resource, value in reserves.items():
            if not isinstance(resource, str) or not resource:
                raise ValueError("min_commons_reserve resource keys must be nonempty strings")
            _number(value, f"min_commons_reserve.{resource}", strictly_positive=False)


def _number(value: Any, key: str, *, strictly_positive: bool) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a finite numeric value")
    number = float(value)
    if not math.isfinite(number) or number < 0 or (strictly_positive and number == 0):
        bound = "positive" if strictly_positive else "nonnegative"
        raise ValueError(f"{key} must be finite and {bound}")
    return number


def policy_block(kind: str, arguments: Mapping[str, Any],
                 world_resources: Mapping[str, float],
                 rules: Mapping[str, Any] | None) -> tuple[str, str] | None:
    """Return (rule key, explanation) if a valid action violates policy.

    Call after invariant checks: malformed actions and overdrafts retain their
    original rejection codes. Policy rejection does not itself impose sanctions.
    """
    if not rules:
        return None
    validate_enforceable_rules(rules)
    if kind == "transfer" and "max_transfer_units" in rules:
        units = float(arguments["units"])
        cap = float(rules["max_transfer_units"])
        if units > cap:
            return "max_transfer_units", f"transfer units {units:g} exceed limit {cap:g}"
    if kind == "withdraw":
        amount = float(arguments["amount"])
        if "max_withdraw_units" in rules:
            cap = float(rules["max_withdraw_units"])
            if amount > cap:
                return "max_withdraw_units", f"withdraw amount {amount:g} exceeds limit {cap:g}"
        reserves = rules.get("min_commons_reserve", {})
        resource = str(arguments["resource"])
        floor = float(reserves.get(resource, 0))
        available = float(world_resources.get(resource, 0))
        if available - amount < floor:
            return ("min_commons_reserve",
                    f"withdrawing {amount:g} {resource} would leave {available - amount:g}, "
                    f"below constitutional reserve {floor:g}")
    return None
