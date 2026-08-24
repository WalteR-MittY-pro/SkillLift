from __future__ import annotations

import pytest

from skilllift_eval.model_endpoints import ModelEndpointProfile, endpoint_config_hash, resolve_endpoints


def test_endpoint_redacted_view_omits_literal_secret_key() -> None:
    endpoint = ModelEndpointProfile.from_mapping(
        "gpt-5.4",
        {
            "provider": "openai-completions",
            "provider_model_id": "gpt-5.4",
            "base_url": "https://example.test/v1",
            "api_key": "literal-secret",
        },
    )

    redacted = endpoint.redacted()
    assert "literal-secret" not in str(redacted)
    assert "api_key" not in redacted
    assert endpoint_config_hash(endpoint).startswith("sha256:")


def test_endpoint_requires_access_reference_and_resolves_known_models() -> None:
    config = {
        "model_endpoints": {
            "gpt-5.4": {
                "provider": "openai-completions",
                "provider_model_id": "gpt-5.4",
                "base_url": "https://gpt.test/v1",
                "api_key_env": "GPT_API_KEY",
            },
            "opus-4.7": {
                "provider": "openai-completions",
                "provider_model_id": "claude-opus-4-7",
                "base_url": "https://claude.test/v1",
                "api_key_env": "CLAUDE_API_KEY",
            },
        }
    }
    endpoints = resolve_endpoints(config, ["gpt-5.4", "opus-4.7"])
    assert set(endpoints) == {"gpt-5.4", "opus-4.7"}
    assert endpoints["opus-4.7"].provider_model_id == "claude-opus-4-7"

    with pytest.raises(ValueError, match="api_key_env or api_key"):
        ModelEndpointProfile.from_mapping(
            "bad",
            {"provider": "openai-completions", "provider_model_id": "x", "base_url": "u"},
        )


def test_endpoint_resolves_model_and_base_url_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_A_MODEL", "provider-model-a")
    monkeypatch.setenv("MODEL_A_BASE_URL", "https://model-a.example/v1")

    endpoint = ModelEndpointProfile.from_mapping(
        "model-a",
        {
            "provider": "openai-completions",
            "provider_model_id_env": "MODEL_A_MODEL",
            "base_url_env": "MODEL_A_BASE_URL",
            "api_key_env": "MODEL_A_API_KEY",
        },
    )

    assert endpoint.provider_model_id == "provider-model-a"
    assert endpoint.base_url == "https://model-a.example/v1"
