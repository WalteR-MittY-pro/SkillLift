import json

import skilllift.model_config as model_config
from skilllift.model_config import load_model_config


def test_model_config_resolves_module_roles_and_runtime(tmp_path) -> None:
    path = tmp_path / "models.json"
    path.write_text(
        json.dumps(
            {
                "moduleModels": {
                    "openclaw_agent": "provider/model-a",
                    "skill_generator": {"model": "provider/model-b"},
                },
                "runtime": {
                    "oracle_rate_limit_retries": 5,
                    "oracleRateLimitWaitSeconds": 7.5,
                },
                "providers": {
                    "provider": {
                        "baseUrl": "https://example.test",
                        "apiKey": "token",
                        "models": [{"id": "model-a", "name": "provider/model-a"}],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    config = load_model_config(path)
    assert config.model_for("openclaw_agent") == "provider/model-a"
    assert config.model_alias_for("openclaw_agent") == "provider/model-a"
    assert config.model_for("skill_generator") == "provider/model-b"
    assert config.model_for("rubricator", "provider/model-a") == "provider/model-a"
    assert config.runtime_int("oracle_rate_limit_retries", 2) == 5
    assert config.runtime_float("oracle_rate_limit_wait_seconds", 120.0) == 7.5


def test_model_config_accepts_nested_models_object(tmp_path) -> None:
    path = tmp_path / "models.json"
    path.write_text(
        json.dumps(
            {
                "models": {
                    "moduleModels": {"oracle": "zhipu/glm-5.1"},
                    "providers": {
                        "zhipu": {
                            "options": {"baseUrl": "https://example.test", "apiKey": "token"},
                            "models": {"glm-5.1": {"name": "zhipu/glm-5.1"}},
                        }
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    config = load_model_config(path)
    assert config.model_for("oracle") == "zhipu/glm-5.1"
    assert config.raw["providers"]["zhipu"]["baseUrl"] == "https://example.test"
    assert config.raw["providers"]["zhipu"]["models"][0]["id"] == "glm-5.1"


def test_model_alias_for_resolves_display_name_to_provider_id(tmp_path) -> None:
    path = tmp_path / "models.json"
    path.write_text(
        json.dumps(
            {
                "moduleModels": {"openclaw_agent": "GLM 5.1 Coding Plan"},
                "providers": {
                    "glm-coding-plan": {
                        "baseUrl": "https://example.test",
                        "apiKey": "token",
                        "models": [{"id": "glm-5.1", "name": "GLM 5.1 Coding Plan"}],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    config = load_model_config(path)
    assert config.model_for("openclaw_agent") == "GLM 5.1 Coding Plan"
    assert config.model_alias_for("openclaw_agent") == "glm-coding-plan/glm-5.1"


def test_model_config_expands_env_placeholders(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GPT_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("MY_PROXY_API_KEY", "token")
    monkeypatch.setenv("GPT_MODEL", "gpt-5.4")
    path = tmp_path / "models.json"
    path.write_text(
        json.dumps(
            {
                "moduleModels": {"openclaw_agent": "openai/gpt-5.4"},
                "providers": {
                    "openai": {
                        "baseUrl": "${GPT_BASE_URL}",
                        "apiKey": "${MY_PROXY_API_KEY}",
                        "models": [{"id": "${GPT_MODEL}", "name": "openai/gpt-5.4"}],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_model_config(path)

    assert config.raw["providers"]["openai"]["baseUrl"] == "https://example.test/v1"
    assert config.raw["providers"]["openai"]["apiKey"] == "token"
    assert config.raw["providers"]["openai"]["models"][0]["id"] == "gpt-5.4"
    assert config.model_alias_for("openclaw_agent") == "openai/gpt-5.4"


def test_model_config_expands_placeholders_from_repo_dotenv(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GPT_BASE_URL", raising=False)
    monkeypatch.delenv("MY_PROXY_API_KEY", raising=False)
    monkeypatch.delenv("GPT_MODEL", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "GPT_BASE_URL=https://example.test/v1\n"
        "MY_PROXY_API_KEY=dotenv-token\n"
        "GPT_MODEL=gpt-5.4\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(model_config, "REPO_ENV_PATH", env_path)
    path = tmp_path / "models.json"
    path.write_text(
        json.dumps(
            {
                "moduleModels": {"openclaw_agent": "openai/gpt-5.4"},
                "providers": {
                    "openai": {
                        "baseUrl": "${GPT_BASE_URL}",
                        "apiKey": "${MY_PROXY_API_KEY}",
                        "models": [{"id": "${GPT_MODEL}", "name": "openai/gpt-5.4"}],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_model_config(path)

    assert config.raw["providers"]["openai"]["baseUrl"] == "https://example.test/v1"
    assert config.raw["providers"]["openai"]["apiKey"] == "dotenv-token"
    assert config.raw["providers"]["openai"]["models"][0]["id"] == "gpt-5.4"
    assert config.model_alias_for("openclaw_agent") == "openai/gpt-5.4"
