"""Environment-driven configuration. No secrets are ever read from files."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelEndpointConfig:
    """Qwen on an OpenAI-compatible vLLM endpoint, configured via environment."""

    base_url: str
    model: str
    api_key: str
    max_concurrency: int
    timeout_seconds: float
    max_retries: int


def model_endpoint_from_env() -> ModelEndpointConfig | None:
    """Build config from VERDI_* env vars; returns None when endpoint is unconfigured."""
    base_url = os.environ.get("VERDI_VLLM_BASE_URL")
    model = os.environ.get("VERDI_VLLM_MODEL")
    if not base_url or not model:
        return None
    return ModelEndpointConfig(
        base_url=base_url,
        model=model,
        api_key=os.environ.get("VERDI_VLLM_API_KEY", "EMPTY"),
        max_concurrency=int(os.environ.get("VERDI_MAX_CONCURRENCY", "8")),
        timeout_seconds=float(os.environ.get("VERDI_REQUEST_TIMEOUT", "120")),
        max_retries=int(os.environ.get("VERDI_MAX_RETRIES", "5")),
    )
