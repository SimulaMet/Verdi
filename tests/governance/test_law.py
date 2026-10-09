"""Law invariant enforcement tests (deterministic string matching)."""
from __future__ import annotations

from verdi.governance import UniversalLaw
from verdi.governance.law import UniversalLaw as LawDirect

from .conftest import LAW


def test_invariants_loaded():
    law = UniversalLaw(LAW)
    assert law.invariants == frozenset(LAW.invariants)
    assert law.law is LAW


def test_law_overrides_flags_conflicting_rule_keys():
    law = UniversalLaw(LAW)
    codes = law.law_overrides({"no_double_spend": False, "new_rule": 1})
    assert codes == ["law:no_double_spend"]


def test_law_overrides_multiple_violations_sorted():
    law = UniversalLaw(LAW)
    codes = law.law_overrides(
        {"no_negative_balances": None, "no_double_spend": None, "harmless": 1}
    )
    assert codes == [
        "law:no_double_spend",
        "law:no_negative_balances",
    ]


def test_law_overrides_clean_payload():
    law = UniversalLaw(LAW)
    assert law.law_overrides({"tax_rate": 0.1}) == []
    assert law.law_overrides({}) == []


def test_non_dict_payload_neutral():
    law = LawDirect(LAW)
    assert law.law_overrides({}) == []
