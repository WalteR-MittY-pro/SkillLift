import json
import http.client
import io
import urllib.error

import pytest

import skilllift.llm_client as llm_client
import skilllift.model_config as model_config
from skilllift.errors import LLMOutputError
from skilllift.llm_client import LLMClient, _dotenv_value, _extract_message_text, _parse_json_response, create_llm_client, resolve_llm_config


def test_extract_message_text() -> None:
    assert _extract_message_text({"choices": [{"message": {"content": " hello "}}]}) == "hello"


def test_parse_json_response_accepts_fenced_object() -> None:
    assert _parse_json_response("```json\n{\"a\": 1}\n```") == {"a": 1}


def test_parse_json_response_accepts_unclosed_fence_with_extra_text() -> None:
    text = "```json\n{\"a\": {\"b\": 2}}\ntrailing explanation"
    assert _parse_json_response(text) == {"a": {"b": 2}}


def test_parse_json_response_repairs_malformed_llm_json() -> None:
    text = "```json\n{'edits': [{'op': 'append', 'content': '检查结果',}],}\n```"
    assert _parse_json_response(text) == {
        "edits": [{"op": "append", "content": "检查结果"}]
    }


def test_process_stream_handles_split_utf8_usage_and_done() -> None:
    payload = (
        'data: {"choices":[{"delta":{"content":"检查"}}]}\n\n'
        'data:{"choices":[{"delta":{"content":"完成"}}],"usage":{"total_tokens":7}}\n'
        'data: [DONE]\n'
    ).encode("utf-8")

    class FakeResponse:
        def __init__(self) -> None:
            self.chunks = [payload[:43], payload[43:51], payload[51:]]

        def read(self, size: int) -> bytes:
            return self.chunks.pop(0) if self.chunks else b""

    client = LLMClient("https://example.test", "token", "model", use_stream=True)
    response = client._process_stream(FakeResponse(), "stream-test")

    assert response["choices"][0]["message"]["content"] == "检查完成"
    assert response["usage"]["total_tokens"] == 7


def test_llm_client_retries_empty_stream(monkeypatch) -> None:
    attempts = 0

    class FakeResponse:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload
            self.read_once = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, size: int) -> bytes:
            if self.read_once:
                return b""
            self.read_once = True
            return self.payload

    def fake_urlopen(request, timeout, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return FakeResponse(b"data: [DONE]\n")
        return FakeResponse(b'data: {"choices":[{"delta":{"content":"ok"}}]}\n')

    monkeypatch.setattr("skilllift.llm_client.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("skilllift.llm_client.time.sleep", lambda _: None)

    client = LLMClient(
        "https://example.test/v4",
        "token",
        "model",
        max_retries=1,
        use_stream=True,
    )

    assert client.call_text("system", "user") == "ok"
    assert attempts == 2


def test_parse_json_response_rejects_non_object() -> None:
    with pytest.raises(LLMOutputError):
        _parse_json_response("[1, 2]")


@pytest.mark.parametrize(
    "text",
    [
        '"a": 1}',
        '{"a": 1',
        '{"a": 1 "b": 2}',
        "{'a': '中文内容'}",
    ],
)
def test_parse_json_response_repairs_common_model_json_errors(text) -> None:
    assert isinstance(_parse_json_response(text), dict)


def test_parse_json_response_preserves_unicode() -> None:
    assert _parse_json_response("{'text': '中文内容'}") == {"text": "中文内容"}


def test_create_llm_client_from_openclaw_models_config(tmp_path) -> None:
    path = tmp_path / "models.json"
    path.write_text(
        json.dumps(
            {
                "providers": {
                    "zhipu": {
                        "baseUrl": "https://example.test/v4",
                        "apiKey": "token",
                        "timeout": 123,
                        "maxRetries": 4,
                        "models": [{"id": "glm-5.1", "name": "zhipu/glm-5.1"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    client = create_llm_client(path, requested_model="zhipu/glm-5.1")
    assert isinstance(client, LLMClient)
    assert client.base_url == "https://example.test/v4"
    assert client.model == "glm-5.1"
    assert client.display_name == "zhipu/glm-5.1"
    assert client.provider == "zhipu"


def test_resolve_llm_config_matches_requested_display_name(tmp_path) -> None:
    path = tmp_path / "models.json"
    path.write_text(
        json.dumps(
            {
                "providers": {
                    "zhipu": {
                        "baseUrl": "https://example.test/v4",
                        "apiKey": "token",
                        "models": [{"id": "glm-5.1", "name": "zhipu/glm-5.1"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    resolved = resolve_llm_config(path, requested_model="zhipu/glm-5.1")
    assert resolved.provider == "zhipu"
    assert resolved.model == "glm-5.1"
    assert resolved.display_name == "zhipu/glm-5.1"
    assert resolved.timeout == 180
    assert resolved.max_retries == 4
    assert resolved.api_key_source == "literal"
    assert resolved.requested_model_matched is True


def test_resolve_llm_config_reads_timeout_and_retries(tmp_path) -> None:
    path = tmp_path / "models.json"
    path.write_text(
        json.dumps(
            {
                "providers": {
                    "zhipu": {
                        "baseUrl": "https://example.test/v4",
                        "apiKey": "token",
                        "timeout": 123,
                        "maxRetries": 4,
                        "models": [{"id": "glm-5.1", "name": "zhipu/glm-5.1"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    resolved = resolve_llm_config(path, requested_model="zhipu/glm-5.1")
    assert resolved.timeout == 123
    assert resolved.max_retries == 4


def test_resolve_llm_config_reads_runtime_timeout_and_retries(tmp_path) -> None:
    path = tmp_path / "models.json"
    path.write_text(
        json.dumps(
            {
                "runtime": {"llm_timeout_seconds": 321, "llm_max_retries": 6},
                "providers": {
                    "zhipu": {
                        "baseUrl": "https://example.test/v4",
                        "apiKey": "token",
                        "models": [{"id": "glm-5.1", "name": "zhipu/glm-5.1"}],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    resolved = resolve_llm_config(path, requested_model="zhipu/glm-5.1")
    assert resolved.timeout == 321
    assert resolved.max_retries == 6


def test_llm_client_retries_remote_disconnect(monkeypatch) -> None:
    attempts = 0

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"ok"}}]}'

    def fake_urlopen(request, timeout, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise http.client.RemoteDisconnected("closed")
        return FakeResponse()

    monkeypatch.setattr("skilllift.llm_client.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("skilllift.llm_client.time.sleep", lambda _: None)

    client = LLMClient("https://example.test/v4", "token", "model", max_retries=1)

    assert client.call_text("system", "user") == "ok"
    assert attempts == 2


def test_llm_client_uses_four_exponential_backoff_retries(monkeypatch) -> None:
    attempts = 0
    sleeps = []

    def fake_urlopen(request, timeout, **kwargs):
        nonlocal attempts
        attempts += 1
        raise TimeoutError("timed out")

    monkeypatch.setattr("skilllift.llm_client.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("skilllift.llm_client.time.sleep", sleeps.append)

    client = LLMClient("https://example.test/v4", "token", "model")

    with pytest.raises(LLMOutputError):
        client.call_text("system", "user")

    assert attempts == 5
    assert sleeps == [1, 2, 4, 8]


@pytest.mark.parametrize("role", ["skill_generator", "rubricator", "verifier"])
def test_llm_client_sleeps_before_framework_model_request(monkeypatch, role) -> None:
    events = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"ok"}}]}'

    def fake_urlopen(request, timeout, **kwargs):
        events.append("urlopen")
        return FakeResponse()

    def fake_sleep(seconds):
        events.append(("sleep", seconds))

    monkeypatch.setattr("skilllift.llm_client.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("skilllift.llm_client.time.sleep", fake_sleep)

    client = LLMClient(
        "https://example.test/v4",
        "token",
        "model",
        max_retries=0,
        role=role,
    )

    assert client.call_text("system", "user") == "ok"
    assert events == [("sleep", 1.0), "urlopen"]


def test_llm_client_does_not_sleep_for_non_framework_role(monkeypatch) -> None:
    events = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"ok"}}]}'

    def fake_urlopen(request, timeout, **kwargs):
        events.append("urlopen")
        return FakeResponse()

    def fake_sleep(seconds):
        events.append(("sleep", seconds))

    monkeypatch.setattr("skilllift.llm_client.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("skilllift.llm_client.time.sleep", fake_sleep)

    client = LLMClient("https://example.test/v4", "token", "model", max_retries=0)

    assert client.call_text("system", "user") == "ok"
    assert events == ["urlopen"]


def test_llm_client_stream_requests_usage_and_survives_rejection(monkeypatch) -> None:
    payloads = []

    class FakeResponse:
        def __init__(self, body: bytes):
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, size=-1):
            return self._body

    def fake_urlopen(request, timeout, **kwargs):
        payloads.append(json.loads(request.data))
        if len(payloads) == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                400,
                "Bad Request",
                {},
                io.BytesIO(b'{"error":{"message":"stream_options is not supported"}}'),
            )
        return FakeResponse(
            b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
            b'data: {"usage":{"total_tokens":7,"prompt_tokens":5,"completion_tokens":2}}\n\n'
            b"data: [DONE]\n\n"
        )

    monkeypatch.setattr("skilllift.llm_client.urllib.request.urlopen", fake_urlopen)
    client = LLMClient("https://example.test/v1", "token", "model", use_stream=True, max_retries=0)

    assert client.call_text("system", "user") == "ok"
    assert payloads[0]["stream_options"] == {"include_usage": True}
    assert "stream_options" not in payloads[1]
    assert payloads[1]["stream"] is True
    assert client.total_tokens == 7


def test_llm_client_transports_optional_structured_output_fields(monkeypatch) -> None:
    payloads = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"ok"}}]}'

    def fake_urlopen(request, timeout, **kwargs):
        payloads.append(json.loads(request.data))
        return FakeResponse()

    monkeypatch.setattr("skilllift.llm_client.urllib.request.urlopen", fake_urlopen)
    client = LLMClient("https://example.test/v1", "token", "model", max_retries=0)
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": "edit", "strict": True, "schema": {"type": "object"}},
    }

    assert client.call_text(
        "system",
        "user",
        response_format=response_format,
        max_completion_tokens=321,
    ) == "ok"
    assert client.call_text("system", "user") == "ok"

    assert payloads[0]["response_format"] == response_format
    assert payloads[0]["max_completion_tokens"] == 321
    assert "response_format" not in payloads[1]
    assert "max_completion_tokens" not in payloads[1]


def test_llm_client_uses_explicit_certificate_verification_context(monkeypatch) -> None:
    expected_context = object()
    observed_context = None

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"ok"}}]}'

    def fake_urlopen(request, timeout, context=None):
        nonlocal observed_context
        observed_context = context
        return FakeResponse()

    monkeypatch.setattr("skilllift.llm_client._ssl_context", lambda: expected_context, raising=False)
    monkeypatch.setattr("skilllift.llm_client.urllib.request.urlopen", fake_urlopen)

    client = LLMClient("https://example.test/v1", "token", "model", max_retries=0)

    assert client.call_text("system", "user") == "ok"
    assert observed_context is expected_context


def test_llm_client_logs_read_timeout_classification(monkeypatch, capsys) -> None:
    def fake_urlopen(request, timeout, **kwargs):
        raise TimeoutError("The read operation timed out")

    monkeypatch.setattr("skilllift.llm_client.urllib.request.urlopen", fake_urlopen)

    client = LLMClient(
        "https://example.test/v4",
        "secret-token",
        "model",
        timeout=7,
        max_retries=0,
        provider="provider",
        role="skill_generator",
    )

    with pytest.raises(LLMOutputError):
        client.call_text("system", "user")

    output = capsys.readouterr().out
    assert "role=skill_generator" in output
    assert "kind=read_timeout" in output
    assert "timeout=7s" in output
    assert "secret-token" not in output


def test_create_llm_client_logs_sanitized_config(tmp_path, capsys) -> None:
    path = tmp_path / "models.json"
    path.write_text(
        json.dumps(
            {
                "providers": {
                    "zhipu": {
                        "baseUrl": "https://example.test/v4",
                        "apiKey": "secret-token",
                        "models": [{"id": "glm-5.1", "name": "zhipu/glm-5.1"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    create_llm_client(path, requested_model="zhipu/glm-5.1", role="skill_generator")

    output = capsys.readouterr().out
    assert "resolved config role=skill_generator" in output
    assert "provider=zhipu" in output
    assert "model=glm-5.1" in output
    assert "requested_model_matched=True" in output
    assert "base_url=https://example.test/v4" in output
    assert "api_key=set(len=12,sha256=" in output
    assert "api_key_source=literal" in output
    assert "secret-token" not in output


def test_resolve_llm_config_expands_key_from_dotenv(tmp_path, monkeypatch) -> None:
    path = tmp_path / "models.json"
    path.write_text(
        json.dumps(
            {
                "providers": {
                    "zhipu": {
                        "baseUrl": "https://example.test/v4",
                        "apiKey": "${MY_PROXY_API_KEY}",
                        "models": [{"id": "glm-5.1", "name": "zhipu/glm-5.1"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("MY_PROXY_API_KEY=dotenv-token\n", encoding="utf-8")
    monkeypatch.setattr(llm_client, "REPO_ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(model_config, "REPO_ENV_PATH", tmp_path / ".env")
    monkeypatch.delenv("MY_PROXY_API_KEY", raising=False)

    resolved = resolve_llm_config(path, requested_model="zhipu/glm-5.1")

    assert resolved.api_key == "dotenv-token"
    assert resolved.api_key_source == f"dotenv:{tmp_path / '.env'}"
    assert _dotenv_value("MY_PROXY_API_KEY") == "dotenv-token"


def test_resolve_llm_config_expands_base_url_and_model_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GPT_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("MY_PROXY_API_KEY", "token")
    monkeypatch.setenv("GPT_MODEL", "gpt-5.4")
    path = tmp_path / "models.json"
    path.write_text(
        json.dumps(
            {
                "providers": {
                    "proxy": {
                        "baseUrl": "${GPT_BASE_URL}",
                        "apiKey": "${MY_PROXY_API_KEY}",
                        "models": [{"id": "${GPT_MODEL}", "name": "proxy/gpt-5.4"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    resolved = resolve_llm_config(path, requested_model="proxy/gpt-5.4")

    assert resolved.base_url == "https://example.test/v1"
    assert resolved.api_key == "token"
    assert resolved.model == "gpt-5.4"
