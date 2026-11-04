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
from dataclasses import dataclass, field
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
    # repr=False: a printed config or a traceback must never show the key.
    api_key: str = field(repr=False)
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


# Every key value resolved in this process (including ones read through a
# scenario's ``model.api_key_env``), so leak guards check the real secrets and
# not only the default variable names.
_RESOLVED_KEYS: set[str] = set()


def _resolve_api_key(api_key_env: str | None) -> str:
    if api_key_env:
        key = os.environ.get(api_key_env) or "EMPTY"
    else:
        key = (
            os.environ.get(API_KEY_ENV)
            or os.environ.get(_LEGACY_API_KEY_ENV)
            or "EMPTY"
        )
    if key != "EMPTY":
        _RESOLVED_KEYS.add(key)
    return key


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
    timeout = float(
        block["timeout_seconds"] if block.get("timeout_seconds") is not None
        else os.environ.get("VERDI_REQUEST_TIMEOUT", "120")
    )
    retries = int(
        block["max_retries"] if block.get("max_retries") is not None
        else os.environ.get("VERDI_MAX_RETRIES", "5")
    )
    concurrency = int(os.environ.get("VERDI_MAX_CONCURRENCY", "8"))
    if timeout <= 0:
        raise ValueError(f"model timeout_seconds must be > 0, got {timeout}")
    if retries < 0:
        raise ValueError(f"model max_retries must be >= 0, got {retries}")
    if concurrency < 1:
        raise ValueError(f"VERDI_MAX_CONCURRENCY must be >= 1, got {concurrency}")
    return ModelEndpointConfig(
        base_url=str(base_url),
        model=str(name),
        api_key=_resolve_api_key(block.get("api_key_env")),
        max_concurrency=concurrency,
        timeout_seconds=timeout,
        max_retries=retries,
    )


def api_key_values() -> list[str]:
    """Every configured endpoint key value, for leak guards.

    Default-variable keys come first (stable order), followed by any key that
    was resolved via ``model.api_key_env`` in this process.
    """
    values = [
        v for v in (os.environ.get(API_KEY_ENV), os.environ.get(_LEGACY_API_KEY_ENV))
        if v
    ]
    values.extend(sorted(k for k in _RESOLVED_KEYS if k not in values))
    return values
