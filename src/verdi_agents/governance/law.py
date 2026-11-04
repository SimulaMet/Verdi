"""Immutable universal law. Deterministic invariant enforcement only."""
from __future__ import annotations

from typing import Any

from verdi_agents.schemas import LawVersion


class UniversalLaw:
    """Holds the immutable law and flags amendments that override it.

    Violation detection is deterministic string matching: a proposed
    amendment conflicts with an invariant when it declares (sets, deletes,
    or redefines) a rule key equal to an invariant name.
    """

    def __init__(self, law: LawVersion) -> None:
        self._law = law
        self._invariants = frozenset(law.invariants)

    @property
    def law(self) -> LawVersion:
        return self._law

    @property
    def invariants(self) -> frozenset[str]:
        return self._invariants

    def invariant_violation_codes(self, amendment_payload: dict[str, Any]) -> list[str]:
        """Deterministic violation codes for each invariant the payload touches.

        Returns ``law:<law_id>:<invariant>`` for every invariant whose rule
        key appears as a declared key in the amendment payload. Sorted for
        determinism.
        """
        if not isinstance(amendment_payload, dict):
            return []
        violations = {
            f"law:{invariant}"
            for invariant in self._invariants
            if invariant in amendment_payload
        }
        return sorted(violations)

    def law_overrides(self, amendment_payload: dict[str, Any]) -> list[str]:
        """Violation codes when an immutable-law invariant is overridden.

        Immutable law outranks the constitution: any non-empty result means
        the amendment must be flagged invalid, never silently enacted.
        """
        return self.invariant_violation_codes(amendment_payload)
        """Violation codes when an immutable-law invariant is overridden.

        Immutable law outranks the constitution: any non-empty result means
        the amendment must be flagged invalid, never silently enacted.
        """
        return self.invariant_violation_codes(amendment_payload)
