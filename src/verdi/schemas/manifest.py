"""Experiment manifest (frozen: schema version 1)."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ExperimentManifest(FrozenModel):
    """Everything needed to reproduce a run, without any secret.

    ``model_endpoint_alias`` is a non-secret name; base URL/model come from
    environment at run time.
    """

    schema_version: int = 1
    experiment_id: str
    world_id: str
    run_seed: int
    branch_origin: str | None = None
    model_endpoint_alias: str
    model_fingerprint: str
    sampling: dict[str, float] = Field(default_factory=dict)
    framework_versions: dict[str, str] = Field(default_factory=dict)
    treatment_seed: int = 0
    task_tape_hash: str | None = None
    probe_bank_version: str | None = None
    git_sha: str | None = None
    simpleaudit_version: str | None = None
