"""Frozen shared contracts for all Verdi modules. Bump CONTRACT_VERSION on change."""
from .actions import ProposedAction, Task, ValidationResult
from .events import WorldEvent
from .governance import (
    Amendment,
    Appeal,
    ConstitutionVersion,
    EvidenceReference,
    LawVersion,
    Proposal,
    Restoration,
    Sanction,
    VoteRecord,
)
from .manifest import ExperimentManifest
from .probes import AuditRecord, ProbeItem, ProbeResult
from .world import AgentState, RightsState, ValueProfile, WorldState

CONTRACT_VERSION = "1.0"

__all__ = [
    "CONTRACT_VERSION",
    "AgentState",
    "Amendment",
    "Appeal",
    "AuditRecord",
    "ConstitutionVersion",
    "EvidenceReference",
    "ExperimentManifest",
    "LawVersion",
    "ProbeItem",
    "ProbeResult",
    "ProposedAction",
    "Restoration",
    "RightsState",
    "Sanction",
    "Task",
    "ValueProfile",
    "ValidationResult",
    "VoteRecord",
    "WorldEvent",
    "WorldState",
    "Proposal",
]
