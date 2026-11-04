
from verdi_agents.config import model_endpoint_from_env


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


def test_scenario_model_block_overrides_env(monkeypatch) -> None:
    monkeypatch.setenv("VERDI_VLLM_BASE_URL", "http://env.invalid/v1")
    monkeypatch.setenv("VERDI_VLLM_MODEL", "env-model")
    monkeypatch.setenv("MODEL_API_KEY", "k-new")
    monkeypatch.setenv("VERDI_VLLM_API_KEY", "k-legacy")
    cfg = model_endpoint_from_env(
        {"base_url": "http://yaml.invalid/v1", "name": "yaml-model", "max_retries": 2}
    )
    assert (cfg.base_url, cfg.model, cfg.max_retries) == (
        "http://yaml.invalid/v1", "yaml-model", 2)
    assert cfg.api_key == "k-new"


def test_model_block_alone_is_enough(monkeypatch) -> None:
    for v in ("VERDI_VLLM_BASE_URL", "VERDI_VLLM_MODEL", "MODEL_API_KEY", "VERDI_VLLM_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    cfg = model_endpoint_from_env({"base_url": "http://x/v1", "name": "m"})
    assert cfg is not None and cfg.api_key == "EMPTY"


def test_api_key_env_and_legacy_fallback(monkeypatch) -> None:
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setenv("VERDI_VLLM_API_KEY", "k-legacy")
    block = {"base_url": "http://x/v1", "name": "m"}
    assert model_endpoint_from_env(block).api_key == "k-legacy"
    monkeypatch.setenv("OTHER_KEY", "k-other")
    assert model_endpoint_from_env({**block, "api_key_env": "OTHER_KEY"}).api_key == "k-other"


def test_literal_api_key_and_unknown_keys_rejected() -> None:
    import pytest
    with pytest.raises(ValueError, match="api_key"):
        model_endpoint_from_env({"base_url": "u", "name": "m", "api_key": "sk-x"})
    with pytest.raises(ValueError, match="unknown"):
        model_endpoint_from_env({"base_url": "u", "name": "m", "temp": 1})
