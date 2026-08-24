from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any

from .schemas import stable_hash


@dataclass(frozen=True)
class ModelEndpointProfile:
    model_endpoint_id: str
    model_label: str
    provider: str
    provider_model_id: str
    base_url: str
    api_key_env: str | None = None
    api_key: str | None = None
    timeout_seconds: int = 300
    max_retries: int = 2
    stream: bool = False
    selection_note: str | None = None

    @classmethod
    def from_mapping(cls, model_label: str, data: dict[str, Any]) -> "ModelEndpointProfile":
        provider = str(data.get("provider") or "")
        provider_model_id = str(
            data.get("provider_model_id")
            or os.environ.get(str(data.get("provider_model_id_env") or ""), "")
        )
        base_url = str(data.get("base_url") or os.environ.get(str(data.get("base_url_env") or ""), ""))
        for field, value in (("provider", provider), ("provider_model_id", provider_model_id), ("base_url", base_url)):
            if not value:
                raise ValueError(f"missing required field {field} for model endpoint {model_label}")
        if not data.get("api_key_env") and not data.get("api_key"):
            raise ValueError(f"model endpoint {model_label} requires api_key_env or api_key")
        return cls(
            model_endpoint_id=data.get("model_endpoint_id", model_label),
            model_label=data.get("model_label", model_label),
            provider=provider,
            provider_model_id=provider_model_id,
            base_url=base_url,
            api_key_env=data.get("api_key_env"),
            api_key=data.get("api_key"),
            timeout_seconds=int(data.get("timeout_seconds", 300)),
            max_retries=int(data.get("max_retries", 2)),
            stream=bool(data.get("stream", False)),
            selection_note=data.get("selection_note"),
        )

    def redacted(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("api_key", None)
        return {k: v for k, v in data.items() if v is not None}

    def to_private_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


def endpoint_config_hash(endpoint: ModelEndpointProfile) -> str:
    return stable_hash(endpoint.redacted())


def resolve_endpoints(config: dict[str, Any], required_models: list[str]) -> dict[str, ModelEndpointProfile]:
    raw = config.get("model_endpoints") or {}
    endpoints: dict[str, ModelEndpointProfile] = {}
    for model in required_models:
        if model not in raw:
            raise ValueError(f"missing model endpoint {model}")
        endpoints[model] = ModelEndpointProfile.from_mapping(model, raw[model])
    return endpoints
