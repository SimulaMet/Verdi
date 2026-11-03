"""Model endpoint configuration.

Experiment-level settings (``base_url``, ``name``, optional ``timeout_seconds`` /
``max_retries``) live in the scenario's ``model:`` block so they are versioned
with the experiment. Secrets and machine tuning stay in the environment:

* ``MODEL_API_KEY`` (or the variable named by ``model.api_key_env``) — the key.
* ``VERDI_MAX_CONCURRENCY`` — hardware throughput knob, not part of the experiment.

``VERDI_VLLM_BASE_URL`` / ``VERDI_VLLM_MODEL`` / ``VERDI_VLLM_API_KEY`` remain as
legacy fallbacks so existing ``.env`` files keep working. No secrets are ever
read from files other than ``.env``.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

API_KEY_ENV = "MODEL_API_KEY"
_LEGACY_API_KEY_ENV = "VERDI_VLLM_API_KEY"

_MODEL_BLOCK_KEYS = frozenset(
    {"base_url", "name", "api_key_env", "timeout_seconds", "max_retries"}
)


@dataclass(frozen=True)
class ModelEndpointConfig:
    """An OpenAI-compatible chat endpoint (vLLM, Ollama, hosted, ...)."""

    base_url: str
    model: str
    api_key: str
    max_concurrency: int
    timeout_seconds: float
    max_retries: int


def _validate_model_block(block: Mapping[str, Any]) -> None:
    if "api_key" in block:
        raise ValueError(
            "scenario model.api_key is not allowed (never commit keys); "
            f"set {API_KEY_ENV} in .env, or name another variable via model.api_key_env"
        )
    unknown = set(block) - _MODEL_BLOCK_KEYS
    if unknown:
        raise ValueError(
            f"unknown scenario model keys: {sorted(unknown)}; "
            f"allowed: {sorted(_MODEL_BLOCK_KEYS)}"
        )


def _resolve_api_key(api_key_env: str | None) -> str:
    if api_key_env:
        return os.environ.get(api_key_env) or "EMPTY"
    return (
        os.environ.get(API_KEY_ENV)
        or os.environ.get(_LEGACY_API_KEY_ENV)
        or "EMPTY"
    )


def model_endpoint_from_env(
    model: Mapping[str, Any] | None = None,
) -> ModelEndpointConfig | None:
    """Build endpoint config; returns None when base URL or model name is unset.

    ``model`` is the scenario's optional ``model:`` block. Precedence per field:
    scenario block > legacy ``VERDI_VLLM_*`` env > built-in default.
    """
    block: Mapping[str, Any] = model or {}
    _validate_model_block(block)
    base_url = block.get("base_url") or os.environ.get("VERDI_VLLM_BASE_URL")
    name = block.get("name") or os.environ.get("VERDI_VLLM_MODEL")
    if not base_url or not name:
        return None
    timeout = block.get("timeout_seconds")
    retries = block.get("max_retries")
    return ModelEndpointConfig(
        base_url=str(base_url),
        model=str(name),
        api_key=_resolve_api_key(block.get("api_key_env")),
        max_concurrency=int(os.environ.get("VERDI_MAX_CONCURRENCY", "8")),
        timeout_seconds=float(
            timeout if timeout is not None
            else os.environ.get("VERDI_REQUEST_TIMEOUT", "120")
        ),
        max_retries=int(
            retries if retries is not None
            else os.environ.get("VERDI_MAX_RETRIES", "5")
        ),
    )


def api_key_values() -> list[str]:
    """Every configured endpoint key value, for leak guards."""
    return [
        v for v in (os.environ.get(API_KEY_ENV), os.environ.get(_LEGACY_API_KEY_ENV))
        if v
    ]
