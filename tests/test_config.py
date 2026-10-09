import os
from types import SimpleNamespace

from verdi.config import model_endpoint_from_env


def test_config_from_env(monkeypatch) -> None:
    monkeypatch.setenv("VERDI_VLLM_BASE_URL", "http://localhost:8000/v1")
    monkeypatch.setenv("VERDI_VLLM_MODEL", "qwen2.5-7b")
    cfg = model_endpoint_from_env()
    assert cfg is not None
    assert cfg.base_url == "http://localhost:8000/v1"
    assert cfg.model == "qwen2.5-7b"
    assert cfg.api_key == "EMPTY"


def test_config_unset(monkeypatch) -> None:
    monkeypatch.delenv("VERDI_VLLM_BASE_URL", raising=False)
    monkeypatch.delenv("VERDI_VLLM_MODEL", raising=False)
    assert model_endpoint_from_env() is None
