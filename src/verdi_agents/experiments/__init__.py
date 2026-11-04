"""P2 controlled recovery experiments (WS-2 public surface).

Re-exports the public names other workstreams may import: regime
configuration, record-only deterministic treatments and matched pairs.
"""
from verdi_agents.experiments.pairs import MatchedPair, make_matched_pair, tape_hash
from verdi_agents.experiments.regimes import (
    REGIME_LABELS,
    RegimeConfig,
    load_regime,
    manifest_metadata,
)
from verdi_agents.experiments.treatments import (
    TreatmentOutcome,
    placebo_treatment,
    rights_restoration_treatment,
)

__all__ = [
    "REGIME_LABELS",
    "MatchedPair",
    "RegimeConfig",
    "TreatmentOutcome",
    "load_regime",
    "make_matched_pair",
    "manifest_metadata",
    "placebo_treatment",
    "rights_restoration_treatment",
    "tape_hash",
]
