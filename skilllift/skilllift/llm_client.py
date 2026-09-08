from __future__ import annotations

import json
import http.client
import codecs
import hashlib
import os
import re
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import json_repair
import certifi
from json_repair import repair_json

from .errors import LLMOutputError
from .model_config import REPO_ENV_PATH, load_model_config

PRE_CALL_SLEEP_SECONDS = 1.0
PRE_CALL_SLEEP_ROLES = {"skill_generator", "rubricator", "verifier"}


@dataclass(frozen=True)
class ResolvedLLMConfig:
    base_url: str
    api_key: str
    api_key_source: str
    model: str
    display_name: str
    provider: str
    timeout: int
    max_retries: int
    requested_model_matched: bool
    use_stream: bool


class LLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: int = 180,
        display_name: str | None = None,
        max_retries: int = 4,
        provider: str = "default",
        role: str = "llm",
        use_stream: bool = False,
        usage_callback: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.display_name = display_name or model
        self.timeout = timeout
        self.max_retries = max_retries
        self.provider = provider
        self.role = role
        self.use_stream = use_stream
        self.usage_callback = usage_callback
        self.ssl_context = _ssl_context()
        self.request_count = 0
        self.total_tokens = 0

    def call_text(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        *,
        response_format: dict[str, Any] | None = None,
        max_completion_tokens: int | None = None,
    ) -> str:
        response = self._call_api(
            system_prompt,
            user_prompt,
            temperature,
            response_format=response_format,
            max_completion_tokens=max_completion_tokens,
        )
        return _extract_message_text(response)

    def call_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        *,
        repair: bool = True,
    ) -> dict[str, Any]:
        return _parse_json_response(
            self.call_text(system_prompt, user_prompt, temperature), repair=repair
        )

    def _call_api(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        *,
        response_format: dict[str, Any] | None = None,
        max_completion_tokens: int | None = None,
    ) -> dict[str, Any]:
        self.request_count += 1
        request_id = f"{self.role}-{self.request_count:04d}"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        }
        if self.use_stream:
            payload["stream"] = True
            # Ask OpenAI-compatible endpoints to report token usage in the
            # final stream chunk; without this the usage block is omitted
            # and token accounting silently undercounts every streamed call.
            payload["stream_options"] = {"include_usage": True}
        if response_format is not None:
            payload["response_format"] = response_format
        if max_completion_tokens is not None:
            payload["max_completion_tokens"] = max_completion_tokens
        body = json.dumps(payload).encode("utf-8")
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        _log_llm(
            "request start "
            f"id={request_id} role={self.role} provider={self.provider} model={self.model} "
            f"display_model={self.display_name} endpoint={url} timeout={self.timeout}s "
            f"max_retries={self.max_retries} temperature={temperature} stream={self.use_stream} "
            f"system_chars={len(system_prompt)} user_chars={len(user_prompt)} body_bytes={len(body)}"
        )
        try:
            data = self._call_with_retries(url, body, headers, request_id)
        except LLMOutputError as exc:
            if not (self.use_stream and _caused_by_http_400(exc)):
                raise
            _log_llm(
                f"retrying without stream_options id={request_id} "
                "(endpoint rejected the parameter)"
            )
            payload.pop("stream_options", None)
            body = json.dumps(payload).encode("utf-8")
            data = self._call_with_retries(url, body, headers, request_id)
        usage = data.get("usage", {})
        if self.use_stream and not usage:
            _log_llm(
                f"warning: stream finished without usage data id={request_id} "
                "(token totals undercounted for this call)"
            )
        if isinstance(usage, dict):
            self.total_tokens += int(usage.get("total_tokens", 0) or 0)
            _log_llm(
                "request usage "
                f"id={request_id} total_tokens={usage.get('total_tokens', 0) or 0} "
                f"prompt_tokens={usage.get('prompt_tokens', 0) or 0} "
                f"completion_tokens={usage.get('completion_tokens', 0) or 0}"
            )
        if self.usage_callback is not None:
            self.usage_callback(
                {
                    "request_id": request_id,
                    "role": self.role,
                    "model": self.model,
                    "usage": usage if isinstance(usage, dict) else {},
                }
            )
        return data

    def _call_with_retries(
        self,
        url: str,
        body: bytes,
        headers: dict[str, str],
        request_id: str,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(
                url, data=body, headers=headers, method="POST"
            )
            attempt_no = attempt + 1
            started = time.monotonic()
            _log_llm(
                f"attempt start id={request_id} attempt={attempt_no}/{self.max_retries + 1}"
            )
            _sleep_before_framework_llm_call(self.role, request_id)
            try:
                with urllib.request.urlopen(
                    request,
                    timeout=self.timeout,
                    context=self.ssl_context,
                ) as response:
                    status = getattr(
                        response, "status", getattr(response, "code", "unknown")
                    )

                    # Choose processing method based on stream mode
                    try:
                        if self.use_stream:
                            data = self._process_stream(response, request_id)
                        else:
                            data = self._process_non_stream(response, request_id)
                        elapsed = time.monotonic() - started
                        _log_llm(
                            "attempt success "
                            f"id={request_id} attempt={attempt_no}/{self.max_retries + 1} "
                            f"elapsed={elapsed:.2f}s status={status} stream={self.use_stream}"
                        )
                        return data
                    except (json.JSONDecodeError, LLMOutputError) as exc:
                        last_error = exc
                        elapsed = time.monotonic() - started
                        error_kind = (
                            "llm_output"
                            if isinstance(exc, LLMOutputError)
                            else "json_decode"
                        )
                        _log_llm(
                            "attempt failed "
                            f"id={request_id} attempt={attempt_no}/{self.max_retries + 1} "
                            f"kind={error_kind} elapsed={elapsed:.2f}s status={status} "
                            f"error={exc.__class__.__name__}: {_one_line(str(exc))}"
                        )
                        if attempt >= self.max_retries:
                            break
                        _sleep_before_retry(attempt, exc, request_id)
                        continue
            except urllib.error.HTTPError as exc:
                elapsed = time.monotonic() - started
                last_error = exc
                response_text = _read_http_error_body(exc)
                retryable = _is_retryable_http_error(exc)
                _log_llm(
                    "attempt failed "
                    f"id={request_id} attempt={attempt_no}/{self.max_retries + 1} "
                    f"kind=http_error elapsed={elapsed:.2f}s status={exc.code} retryable={retryable} "
                    f"response_preview={_preview(response_text)}"
                )
                if attempt >= self.max_retries or not _is_retryable_http_error(exc):
                    break
                _sleep_before_retry(attempt, exc, request_id)
            except (
                urllib.error.URLError,
                http.client.HTTPException,
                ConnectionError,
                json.JSONDecodeError,
                TimeoutError,
            ) as exc:
                elapsed = time.monotonic() - started
                last_error = exc
                _log_llm(
                    "attempt failed "
                    f"id={request_id} attempt={attempt_no}/{self.max_retries + 1} "
                    f"kind={_network_error_kind(exc)} elapsed={elapsed:.2f}s timeout={self.timeout}s "
                    f"error={exc.__class__.__name__}: {_one_line(str(exc))}"
                )
                if attempt >= self.max_retries:
                    break
                _sleep_before_retry(attempt, exc, request_id)
        _log_llm(
            "request failed "
            f"id={request_id} role={self.role} model={self.model} provider={self.provider} "
            f"final_error={last_error.__class__.__name__ if last_error else 'unknown'}: "
            f"{_one_line(str(last_error)) if last_error else 'unknown'}"
        )
        raise LLMOutputError(
            f"LLM call failed for model={self.model}: {last_error}"
        ) from last_error

    def _process_non_stream(self, response: Any, request_id: str) -> dict[str, Any]:
        """Process non-streaming HTTP response."""
        raw = response.read()
        text = raw.decode("utf-8")
        data = json.loads(text)
        _log_llm(f"non-stream complete id={request_id} response_bytes={len(raw)}")
        return data

    def _process_stream(self, response: Any, request_id: str) -> dict[str, Any]:
        """Process SSE streaming response and reconstruct complete message."""
        content_parts = []
        usage_data = {}
        chunks_received = 0
        buffer = ""
        decoder = codecs.getincrementaldecoder("utf-8")()
        stream_done = False

        while not stream_done:
            chunk = response.read(1024)
            reached_eof = not chunk
            buffer += decoder.decode(chunk, final=reached_eof)
            if not buffer:
                if reached_eof:
                    break
                continue
            if reached_eof and "\n" not in buffer:
                buffer += "\n"

            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line_text = line.strip()

                if not line_text or line_text.startswith(":"):
                    continue

                if line_text.startswith("data:"):
                    data_content = line_text[5:].lstrip()

                    if data_content == "[DONE]":
                        _log_llm(f"stream end id={request_id} chunks={chunks_received}")
                        stream_done = True
                        break

                    try:
                        chunk_data = json.loads(data_content)
                        chunks_received += 1

                        if "choices" in chunk_data and len(chunk_data["choices"]) > 0:
                            delta = chunk_data["choices"][0].get("delta", {})
                            if "content" in delta:
                                content_parts.append(delta["content"])

                        if "usage" in chunk_data:
                            usage_data = chunk_data["usage"]

                        if chunks_received % 10 == 0:
                            _log_llm(f"stream progress id={request_id} chunks={chunks_received}")

                    except json.JSONDecodeError:
                        continue
            if reached_eof:
                break

        # Reconstruct the complete response in OpenAI format
        complete_content = "".join(content_parts)

        if not complete_content:
            raise LLMOutputError("Stream completed but no content received")

        _log_llm(
            f"stream complete id={request_id} chunks={chunks_received} "
            f"content_length={len(complete_content)}"
        )

        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": complete_content
                    },
                    "finish_reason": "stop",
                    "index": 0
                }
            ],
            "usage": usage_data
        }


def _ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return context


def _extract_message_text(response: dict[str, Any]) -> str:
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMOutputError("response missing choices[0].message.content") from exc
    if not isinstance(content, str) or not content.strip():
        raise LLMOutputError("response message content is empty")
    return content.strip()


def _parse_json_response(text: str, *, repair: bool = True) -> dict[str, Any]:
    candidate = _strip_json_fence(text)
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as strict_exc:
        if not repair:
            raise LLMOutputError(f"LLM response is not valid JSON: {strict_exc}") from strict_exc
        extracted = _extract_first_json_object(candidate)
        object_start = candidate.find("{")
        repair_candidate = (
            extracted
            if extracted is not None
            else candidate[object_start:] if object_start >= 0 else candidate
        )
        try:
            if "{" not in repair_candidate and repair_candidate.rstrip().endswith("}"):
                repair_candidate = "{" + repair_candidate
            payload = json_repair.loads(repair_candidate)
        except (ValueError, TypeError, json.JSONDecodeError) as repair_exc:
            try:
                repaired_text = repair_json(repair_candidate, ensure_ascii=False)
                payload = json.loads(repaired_text)
            except (json.JSONDecodeError, ValueError, TypeError) as repaired_exc:
                raise LLMOutputError(
                    f"LLM response is not valid JSON: {strict_exc}; repair failed: {repair_exc}; fallback failed: {repaired_exc}"
                ) from repaired_exc
    if not isinstance(payload, dict):
        raise LLMOutputError("LLM JSON response must be an object")
    return payload


def create_llm_client(
    models_config: Path | str | dict[str, Any],
    requested_model: str | None = None,
    role: str = "llm",
) -> LLMClient:
    resolved = resolve_llm_config(models_config, requested_model)
    _log_llm(
        "resolved config "
        f"role={role} source={_config_source_label(models_config)} requested_model={requested_model or '<default>'} "
        f"provider={resolved.provider} model={resolved.model} display_model={resolved.display_name} "
        f"requested_model_matched={resolved.requested_model_matched} "
        f"base_url={resolved.base_url} endpoint={resolved.base_url}/chat/completions "
        f"timeout={resolved.timeout}s max_retries={resolved.max_retries} stream={resolved.use_stream} "
        f"api_key={_api_key_summary(resolved.api_key)} api_key_source={resolved.api_key_source}"
    )
    return LLMClient(
        resolved.base_url,
        resolved.api_key,
        resolved.model,
        resolved.timeout,
        display_name=resolved.display_name,
        max_retries=resolved.max_retries,
        provider=resolved.provider,
        role=role,
        use_stream=resolved.use_stream,
    )


def resolve_llm_config(
    models_config: Path | str | dict[str, Any], requested_model: str | None = None
) -> ResolvedLLMConfig:
    model_config = load_model_config(models_config)
    config = model_config.raw
    provider_name, provider, model_entry = _select_provider(config, requested_model)
    base_url = _expand_env(provider.get("baseUrl") or provider.get("base_url") or "")
    api_key, api_key_source = _expand_api_key(
        provider.get("apiKey") or provider.get("api_key") or ""
    )
    api_key_source = _source_from_raw_api_key(models_config, provider_name, api_key, api_key_source)
    model = _expand_env(_select_model(provider, model_entry))
    display_name = _display_model_name(provider_name, model_entry, model)
    requested_model_matched = _requested_model_matched(
        provider_name, provider, model_entry, requested_model
    )
    timeout = int(
        provider.get(
            "timeout",
            config.get("timeout", model_config.runtime_int("llm_timeout_seconds", 180)),
        )
    )
    max_retries = int(
        provider.get(
            "maxRetries",
            provider.get(
                "max_retries",
                config.get(
                    "max_retries", model_config.runtime_int("llm_max_retries", 4)
                ),
            ),
        )
    )
    use_stream = bool(
        provider.get(
            "stream",
            config.get("stream", False)
        )
    )
    if not base_url or not api_key or not model:
        missing = [
            name
            for name, value in [
                ("baseUrl", base_url),
                ("apiKey", api_key),
                ("model", model),
            ]
            if not value
        ]
        _log_llm(
            "invalid config "
            f"source={_config_source_label(models_config)} requested_model={requested_model or '<default>'} "
            f"provider={provider_name} missing={','.join(missing)} "
            f"base_url={base_url or '<missing>'} model={model or '<missing>'} api_key_source={api_key_source}"
        )
        raise LLMOutputError(
            f"models config must provide baseUrl, apiKey, and model; missing={','.join(missing)}"
        )
    return ResolvedLLMConfig(
        str(base_url),
        str(api_key),
        api_key_source,
        str(model),
        display_name,
        provider_name,
        timeout,
        max_retries,
        requested_model_matched,
        use_stream,
    )


def _load_config(value: Path | str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    path = Path(value)
    return json.loads(path.read_text(encoding="utf-8"))


def _source_from_raw_api_key(
    models_config: Path | str | dict[str, Any],
    provider_name: str,
    expanded_api_key: str,
    current_source: str,
) -> str:
    raw_value = _raw_provider_api_key(models_config, provider_name)
    if raw_value is None:
        return current_source
    raw_api_key, raw_source = _expand_api_key(str(raw_value))
    if raw_api_key == expanded_api_key and raw_source not in {"literal", "empty"}:
        return raw_source
    return current_source


def _raw_provider_api_key(
    models_config: Path | str | dict[str, Any], provider_name: str
) -> Any | None:
    try:
        config = _load_config(models_config)
    except Exception:
        return None
    if "providers" not in config and isinstance(config.get("models"), dict):
        config = config["models"]
    providers = config.get("providers")
    if isinstance(providers, dict):
        provider = providers.get(provider_name)
        if not isinstance(provider, dict):
            return None
        options = provider.get("options")
        if not isinstance(options, dict):
            options = {}
        return (
            provider.get("apiKey")
            or provider.get("api_key")
            or options.get("apiKey")
            or options.get("api_key")
        )
    if provider_name == "default":
        return config.get("apiKey") or config.get("api_key")
    return None


def _is_retryable_http_error(exc: urllib.error.HTTPError) -> bool:
    return exc.code == 429 or 500 <= exc.code <= 599


def _caused_by_http_400(exc: LLMOutputError) -> bool:
    cause = exc.__cause__
    return isinstance(cause, urllib.error.HTTPError) and cause.code == 400


def _retry_sleep(attempt: int, exc: Exception) -> float:
    if isinstance(exc, urllib.error.HTTPError):
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        if retry_after:
            try:
                return min(float(retry_after), 60.0)
            except ValueError:
                pass
    return min(2**attempt, 30)


def _select_provider(
    config: dict[str, Any], requested_model: str | None = None
) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    if "providers" in config and isinstance(config["providers"], dict):
        providers = config["providers"]
        match = _find_requested_model(providers, requested_model)
        if match is not None:
            return match
        for provider_name, provider in providers.items():
            if isinstance(provider, dict):
                return str(provider_name), provider, _first_model_entry(provider)
    return "default", config, _first_model_entry(config)


def _find_requested_model(
    providers: dict[str, Any],
    requested_model: str | None,
) -> tuple[str, dict[str, Any], dict[str, Any] | None] | None:
    if not requested_model:
        return None
    for provider_name, provider in providers.items():
        if not isinstance(provider, dict):
            continue
        for entry in _model_entries(provider):
            aliases = _model_aliases(str(provider_name), entry)
            if requested_model in aliases:
                return str(provider_name), provider, entry
    return None


def _select_model(
    provider: dict[str, Any], model_entry: dict[str, Any] | None = None
) -> str:
    if provider.get("model"):
        return str(provider["model"])
    if model_entry is not None:
        return str(model_entry.get("id") or model_entry.get("name") or "")
    return ""


def _first_model_entry(provider: dict[str, Any]) -> dict[str, Any] | None:
    entries = _model_entries(provider)
    return entries[0] if entries else None


def _model_entries(provider: dict[str, Any]) -> list[dict[str, Any]]:
    models = provider.get("models")
    if not isinstance(models, list):
        return []
    return [entry for entry in models if isinstance(entry, dict)]


def _model_aliases(provider_name: str, model_entry: dict[str, Any]) -> set[str]:
    model_id = str(model_entry.get("id") or "")
    model_name = str(model_entry.get("name") or "")
    aliases = {item for item in [model_id, model_name] if item}
    if model_id:
        aliases.add(f"{provider_name}/{model_id}")
    return aliases


def _display_model_name(
    provider_name: str, model_entry: dict[str, Any] | None, model: str
) -> str:
    if model_entry and model_entry.get("name"):
        return str(model_entry["name"])
    return f"{provider_name}/{model}" if provider_name != "default" else model


def _requested_model_matched(
    provider_name: str,
    provider: dict[str, Any],
    model_entry: dict[str, Any] | None,
    requested_model: str | None,
) -> bool:
    if not requested_model:
        return True
    selected_model = _select_model(provider, model_entry)
    aliases = {
        selected_model,
        _display_model_name(provider_name, model_entry, selected_model),
    }
    if model_entry is not None:
        aliases.update(_model_aliases(provider_name, model_entry))
    return requested_model in {alias for alias in aliases if alias}


def _expand_api_key(value: str) -> tuple[str, str]:
    match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", str(value).strip())
    if match:
        env_name = match.group(1)
        env_value = os.environ.get(env_name)
        if env_value:
            return env_value, f"env:{env_name}"
        dotenv_value, dotenv_path = _dotenv_lookup(env_name)
        if dotenv_value:
            return dotenv_value, f"dotenv:{dotenv_path}"
        return "", f"missing_env:{env_name}"
    if value:
        return str(value), "literal"
    return "", "empty"


def _expand_env(value: str) -> str:
    return _expand_api_key(value)[0]


def _dotenv_value(env_name: str) -> str:
    value, _ = _dotenv_lookup(env_name)
    return value


def _dotenv_lookup(env_name: str) -> tuple[str, str]:
    if not REPO_ENV_PATH.exists():
        return "", ""
    for line in REPO_ENV_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw_value = stripped.split("=", 1)
        if key.strip() == env_name:
            return raw_value.strip().strip("\"'"), str(REPO_ENV_PATH)
    return "", ""


def _sleep_before_retry(attempt: int, exc: Exception, request_id: str) -> None:
    delay = _retry_sleep(attempt, exc)
    _log_llm(f"retry sleep id={request_id} seconds={delay:.2f}")
    time.sleep(delay)


def _sleep_before_framework_llm_call(role: str, request_id: str) -> None:
    if role not in PRE_CALL_SLEEP_ROLES:
        return
    _log_llm(f"pre-call sleep id={request_id} seconds={PRE_CALL_SLEEP_SECONDS:.2f}")
    time.sleep(PRE_CALL_SLEEP_SECONDS)


def _network_error_kind(exc: Exception) -> str:
    text = str(exc).lower()
    reason = getattr(exc, "reason", None)
    reason_text = str(reason).lower() if reason is not None else ""
    if (
        isinstance(exc, TimeoutError)
        or "timed out" in text
        or "timed out" in reason_text
    ):
        if (
            "read operation timed out" in text
            or "read operation timed out" in reason_text
        ):
            return "read_timeout"
        return "timeout"
    if isinstance(exc, urllib.error.URLError):
        return "url_error"
    if isinstance(exc, http.client.HTTPException):
        return "http_protocol_error"
    return "transport_error"


def _read_http_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def _api_key_summary(api_key: str) -> str:
    if not api_key:
        return "missing"
    digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:8]
    return f"set(len={len(api_key)},sha256={digest})"


def _config_source_label(value: Path | str | dict[str, Any]) -> str:
    if isinstance(value, dict):
        return "<dict>"
    return str(Path(value))


def _preview(text: str, limit: int = 400) -> str:
    one_line = _one_line(text)
    if len(one_line) <= limit:
        return one_line or "<empty>"
    return f"{one_line[:limit]}..."


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def _log_llm(message: str) -> None:
    print(f"[skilllift][llm] {message}", flush=True)


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```\s*", stripped, re.S | re.I)
    if match:
        return match.group(1).strip()
    if re.match(r"^```(?:json)?\s*\n?", stripped, re.I):
        stripped = re.sub(
            r"^```(?:json)?\s*\n?", "", stripped, count=1, flags=re.I
        ).strip()
        if stripped.endswith("```"):
            stripped = stripped[:-3].strip()
    return stripped


def _extract_first_json_object(text: str) -> str | None:
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
            continue
        if char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None
