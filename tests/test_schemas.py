"""Contract tests: fixtures serialize/deserialize and enforce invariants (G0/G1)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from verdi import schemas
from verdi.schemas import (
    Appeal,
    ConstitutionVersion,
    ExperimentManifest,
    LawVersion,
    ProbeItem,
    ProbeResult,
    ProposedAction,
    Sanction,
    Task,
    ValidationResult,
    WorldEvent,
    WorldState,
)

FIXTURE = json.loads((Path(__file__).resolve().parent / "fixtures" / "contract_fixture.json").read_text())


def _roundtrip(model_cls, name: str):
    obj = model_cls.model_validate(FIXTURE[name])
    dumped = obj.model_dump()
    again = model_cls.model_validate(dumped)
    assert again == obj
    return obj


@pytest.mark.parametrize("cls,name", [
    (WorldState, "world_state"),
    (Task, "task"),
    (ProposedAction, "proposed_action"),
    (ValidationResult, "validation_rejected"),
    (WorldEvent, "world_event_rejected"),
    (LawVersion, "law"),
    (ConstitutionVersion, "constitution"),
    (Sanction, "sanction"),
    (Appeal, "appeal"),
    (ProbeItem, "probe_item"),
    (ProbeResult, "probe_result"),
    (ExperimentManifest, "manifest"),
])
def test_fixture_roundtrip(cls, name):
    _roundtrip(cls, name)


def test_contracts_are_frozen():
    ws = _roundtrip(WorldState, "world_state")
    with pytest.raises(ValidationError):
        ws.tick = 1  # type: ignore[misc]


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        Task.model_validate({**FIXTURE["task"], "unexpected": True})


def test_event_decision_pattern():
    with pytest.raises(ValidationError):
        WorldEvent.model_validate({**FIXTURE["world_event_rejected"], "decision": "maybe"})


def test_rejected_event_must_carry_rejection():
    v = _roundtrip(ValidationResult, "validation_rejected")
    assert v.accepted is False
    assert v.attempted_violation_codes  # a rejection must record *why*


def test_sanction_requires_evidence():
    payload = dict(FIXTURE["sanction"])
    payload["evidence"] = []
    with pytest.raises(ValidationError):
        Sanction.model_validate(payload)


def test_value_profile_bounds():
    ws = _roundtrip(WorldState, "world_state")
    agent = ws.agents["a1"]
    for value in agent.declared_values.model_dump().values():
        assert 0.0 <= value <= 1.0
    bad_agent = dict(FIXTURE["world_state"]["agents"]["a1"])
    bad_agent["agent_id"] = "a_bad"
    bad_agent["declared_values"] = {
        **FIXTURE["world_state"]["agents"]["a1"]["declared_values"],
        "integrity": 1.5,
    }
    bad_world = dict(FIXTURE["world_state"])
    bad_world["agents"] = {**FIXTURE["world_state"]["agents"], "a_bad": bad_agent}
    with pytest.raises(ValidationError):
        WorldState.model_validate(bad_world)


def test_probe_channels_are_distinct():
    for channel in ("declared", "private", "public"):
        item = ProbeItem.model_validate({**FIXTURE["probe_item"], "channel": channel})
        assert item.channel == channel
    with pytest.raises(ValidationError):
        ProbeItem.model_validate({**FIXTURE["probe_item"], "channel": "leaked_into_memory"})


def test_manifest_has_no_secret_fields():
    manifest = _roundtrip(ExperimentManifest, "manifest")
    dumped = manifest.model_dump()
    assert not any("secret" in k.lower() or "password" in k.lower() for k in dumped)
    assert manifest.model_endpoint_alias  # alias, not a URL with credentials


def test_contracts_versioned():
    assert isinstance(schemas.CONTRACT_VERSION, str)
    assert schemas.CONTRACT_VERSION
    # All versioned fixtures carry schema_version
    for name in ("world_state", "task", "proposed_action", "validation_rejected",
                 "world_event_rejected", "manifest"):
        assert FIXTURE[name]["schema_version"] == 1
