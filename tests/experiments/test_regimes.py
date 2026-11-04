"""Unit tests for RegimeConfig: fixtures, validation, manifest metadata."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from verdi_agents.experiments.regimes import (
    REGIME_LABELS,
    RegimeConfig,
    load_regime,
    manifest_metadata,
)

REGIMES_DIR = Path(__file__).resolve().parents[2] / "src" / "verdi_agents" / "experiments" / "regimes"
REGIME_FILES = ("baseline.json", "single_adversary.json", "detection_on.json")


def test_fixture_regimes_validate():
    for name in REGIME_FILES:
        config = load_regime(REGIMES_DIR / name)
        assert config.regime in REGIME_LABELS
        assert config.adversary_count in (0, 1, 3)
        assert isinstance(config.scheduler_id, str) and config.scheduler_id
        assert isinstance(config.reflection, bool)
        assert isinstance(config.memory, bool)
        assert isinstance(config.detection, bool)


def test_fixture_regimes_cover_required_casemix():
    g0 = load_regime(REGIMES_DIR / "baseline.json")
    g1 = load_regime(REGIMES_DIR / "single_adversary.json")
    g3 = load_regime(REGIMES_DIR / "detection_on.json")
    # baseline: no adversary, everything off
    assert g0.adversary_count == 0
    assert (g0.reflection, g0.memory, g0.detection) == (False, False, False)
    # single adversary with reflection OFF
    assert g1.adversary_count == 1
    assert g1.reflection is False
    # three adversaries with detection ON
    assert g3.adversary_count == 3
    assert g3.detection is True


def test_regime_is_frozen_and_no_extra_fields():
    config = RegimeConfig(
        regime="G0",
        adversary_count=0,
        scheduler_id="s",
        reflection=False,
        memory=False,
        detection=False,
    )
    with pytest.raises(ValidationError):
        config.regime = "G1"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        RegimeConfig(
            regime="G0",
            adversary_count=2,  # not in Literal[0, 1, 3]
            scheduler_id="s",
            reflection=False,
            memory=False,
            detection=False,
        )
    with pytest.raises(ValidationError):
        RegimeConfig(
            regime="G0",
            adversary_count=0,
            scheduler_id="s",
            reflection=False,
            memory=False,
            detection=False,
            bogus=1,  # extra="forbid"
        )


def test_manifest_metadata_round_trips_without_schema_change():
    config = load_regime(REGIMES_DIR / "detection_on.json")
    metadata = manifest_metadata(config)
    assert metadata["regime_label"] == config.regime
    assert RegimeConfig.model_validate(metadata["regime"]) == config
    # the metadata mapping carries the regime only; no manifest field names
    # leak into it.
    assert set(metadata) == {"regime_label", "regime"}
    assert set(metadata["regime"]) == {
        "regime",
        "adversary_count",
        "scheduler_id",
        "reflection",
        "memory",
        "detection",
    }


def test_load_regime_rejects_missing_file():
    with pytest.raises(FileNotFoundError):
        load_regime(REGIMES_DIR / "does_not_exist.json")
