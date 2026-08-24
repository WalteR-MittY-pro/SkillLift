from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MODULE_MODELS_KEYS = ("moduleModels", "module_models", "modules")
RUNTIME_KEYS = ("runtime", "settings")
ENV_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
REPO_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


@dataclass(frozen=True)
class ModelRoleConfig:
    raw: dict[str, Any]
    source: Path | None = None

    def model_for(self, role: str, fallback: str | None = None) -> str:
        model = _model_from_role_map(self.raw, role)
        if model:
            return model
        if fallback:
            return fallback
        return _first_config_model(self.raw)

    def model_alias_for(self, role: str, fallback: str | None = None) -> str:
        model = _model_from_role_map(self.raw, role)
        if model:
            return _canonical_model_alias(self.raw, model) or model
        if fallback:
            return _canonical_model_alias(self.raw, fallback) or fallback
        return _first_config_model_alias(self.raw)

    def runtime_int(self, key: str, fallback: int) -> int:
        value = _runtime_value(self.raw, key)
        if value is None:
            return fallback
        return int(value)

    def runtime_float(self, key: str, fallback: float) -> float:
        value = _runtime_value(self.raw, key)
        if value is None:
            return fallback
        return float(value)

    def runtime_str(self, key: str, fallback: str | None = None) -> str | None:
        value = _runtime_value(self.raw, key)
        if value is None:
            return fallback
        return str(value)


def load_model_config(value: Path | str | dict[str, Any], source: Path | None = None) -> ModelRoleConfig:
    raw = _load_raw_config(value)
    return ModelRoleConfig(_normalize_models_config(raw, source), source)


def _load_raw_config(value: Path | str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, dict):
        return _expand_env_placeholders(value)
    path = Path(value)
    return _expand_env_placeholders(json.loads(path.read_text(encoding="utf-8")))


def _expand_env_placeholders(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _expand_env_placeholders(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_expand_env_placeholders(child) for child in value]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        env_name = match.group(1)
        env_value = os.environ.get(env_name)
        if env_value:
            return env_value
        dotenv_value = _dotenv_value(env_name)
        if dotenv_value:
            return dotenv_value
        raise ValueError(
            f"{env_name} must be set to a non-empty value when models config uses ${{{env_name}}}"
        )

    return ENV_PLACEHOLDER_RE.sub(replace, value)


def _dotenv_value(env_name: str) -> str:
    if not REPO_ENV_PATH.exists():
        return ""
    for line in REPO_ENV_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        if key.strip() == env_name:
            return raw_value.strip().strip("\"'")
    return ""


def _normalize_models_config(models_config: dict[str, Any], source: Path | None) -> dict[str, Any]:
    if not isinstance(models_config, dict):
        raise ValueError(f"Models config must be a JSON object: {source or '<dict>'}")

    if "providers" not in models_config and isinstance(models_config.get("models"), dict):
        models_config = models_config["models"]

    if "providers" not in models_config:
        raise ValueError(
            f"Models config must contain a top-level 'providers' object: {source or '<dict>'}"
        )

    providers = models_config.get("providers")
    if not isinstance(providers, dict):
        raise ValueError(f"Models config 'providers' must be a JSON object: {source or '<dict>'}")

    normalized = dict(models_config)
    normalized.setdefault("mode", "merge")
    normalized["providers"] = {
        str(provider_id): _normalize_provider(provider_id, provider_config, source)
        for provider_id, provider_config in providers.items()
    }
    return normalized


def _normalize_provider(provider_id: str, provider_config: Any, source: Path | None) -> dict[str, Any]:
    if not isinstance(provider_config, dict):
        raise ValueError(f"Provider config for '{provider_id}' must be a JSON object: {source or '<dict>'}")

    provider = dict(provider_config)
    options = provider.pop("options", {})
    npm_package = provider.pop("npm", None)
    if options is not None and not isinstance(options, dict):
        raise ValueError(f"Provider options for '{provider_id}' must be a JSON object: {source or '<dict>'}")

    options = options or {}
    base_url = provider.get("baseUrl") or options.get("baseUrl") or options.get("baseURL")
    api_key = provider.get("apiKey") or options.get("apiKey")
    headers = provider.get("headers") or options.get("headers")

    if base_url is not None:
        provider["baseUrl"] = base_url
    if api_key is not None:
        provider["apiKey"] = api_key
    if headers is not None:
        provider["headers"] = headers
    if "api" not in provider and (npm_package == "@ai-sdk/openai-compatible" or provider.get("baseUrl")):
        provider["api"] = "openai-completions"

    provider["models"] = _normalize_provider_models(provider_id, provider.get("models", []), source)
    return provider


def _normalize_provider_models(provider_id: str, models: Any, source: Path | None) -> list[dict[str, Any]]:
    if isinstance(models, dict):
        normalized = []
        for model_id, model_config in models.items():
            entry = dict(model_config) if isinstance(model_config, dict) else {}
            entry.setdefault("id", model_id)
            entry.setdefault("name", model_id)
            normalized.append(entry)
        return normalized

    if not isinstance(models, list):
        raise ValueError(f"Provider models for '{provider_id}' must be a JSON object or array: {source or '<dict>'}")

    normalized = []
    for model_entry in models:
        if not isinstance(model_entry, dict):
            raise ValueError(f"Provider models for '{provider_id}' must contain JSON objects only: {source or '<dict>'}")
        entry = dict(model_entry)
        model_id = entry.get("id") or entry.get("name")
        if not model_id:
            raise ValueError(f"Each model entry for provider '{provider_id}' must define 'id' or 'name': {source or '<dict>'}")
        entry.setdefault("id", model_id)
        entry.setdefault("name", model_id)
        normalized.append(entry)
    return normalized


def _model_from_role_map(config: dict[str, Any], role: str) -> str:
    for key in MODULE_MODELS_KEYS:
        roles = config.get(key)
        if not isinstance(roles, dict):
            continue
        value = _case_insensitive_get(roles, role)
        model = _model_value(value)
        if model:
            return model
    return ""


def _runtime_value(config: dict[str, Any], key: str) -> Any:
    for runtime_key in RUNTIME_KEYS:
        runtime = config.get(runtime_key)
        if isinstance(runtime, dict):
            value = _case_insensitive_get(runtime, key)
            if value is not None:
                return value
    return None


def _case_insensitive_get(values: dict[str, Any], key: str) -> Any:
    aliases = {key, _snake_to_camel(key), _camel_to_snake(key)}
    for candidate in aliases:
        if candidate in values:
            return values[candidate]
    lowered = {str(item_key).lower(): item_value for item_key, item_value in values.items()}
    for candidate in aliases:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    return None


def _model_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(value.get("model") or value.get("name") or value.get("id") or "").strip()
    return ""


def _first_config_model(config: dict[str, Any]) -> str:
    providers = config.get("providers", {})
    if not isinstance(providers, dict):
        return ""
    for provider_name, provider in providers.items():
        if not isinstance(provider, dict):
            continue
        models = provider.get("models", [])
        if not isinstance(models, list) or not models:
            continue
        entry = models[0]
        if not isinstance(entry, dict):
            continue
        return str(entry.get("name") or f"{provider_name}/{entry.get('id')}" if entry.get("id") else "").strip()
    return ""


def _first_config_model_alias(config: dict[str, Any]) -> str:
    providers = config.get("providers", {})
    if not isinstance(providers, dict):
        return ""
    for provider_name, provider in providers.items():
        if not isinstance(provider, dict):
            continue
        models = provider.get("models", [])
        if not isinstance(models, list) or not models:
            continue
        entry = models[0]
        if isinstance(entry, dict):
            return _provider_model_alias(str(provider_name), entry)
    return ""


def _canonical_model_alias(config: dict[str, Any], requested_model: str) -> str:
    providers = config.get("providers", {})
    if not isinstance(providers, dict):
        return ""
    for provider_name, provider in providers.items():
        if not isinstance(provider, dict):
            continue
        models = provider.get("models", [])
        if not isinstance(models, list):
            continue
        for entry in models:
            if not isinstance(entry, dict):
                continue
            if requested_model in _model_aliases(str(provider_name), entry):
                return _provider_model_alias(str(provider_name), entry)
    return ""


def _model_aliases(provider_name: str, entry: dict[str, Any]) -> set[str]:
    model_id = str(entry.get("id") or "").strip()
    model_name = str(entry.get("name") or "").strip()
    aliases = {value for value in (model_id, model_name) if value}
    if model_id:
        aliases.add(f"{provider_name}/{model_id}")
    return aliases


def _provider_model_alias(provider_name: str, entry: dict[str, Any]) -> str:
    model_id = str(entry.get("id") or "").strip()
    return f"{provider_name}/{model_id}" if model_id else ""


def _snake_to_camel(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _camel_to_snake(value: str) -> str:
    result = []
    for index, char in enumerate(value):
        if char.isupper() and index > 0:
            result.append("_")
        result.append(char.lower())
    return "".join(result)
