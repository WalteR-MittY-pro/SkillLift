from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from experiments.revision2026.common import ROOT, load_protocol
from experiments.revision2026.vision_bridge import ImageReader, observe_messages


IMAGE = "data:image/png;base64,synthetic-test-image"


def response(text, *, calls=None):
    message = SimpleNamespace(content=text, tool_calls=calls)
    message.model_dump = lambda **kwargs: {"role": "assistant", "content": text, **({"tool_calls": []} if calls else {})}
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], model="fake-model",
                           usage=SimpleNamespace(model_dump=lambda: {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}))


def test_vision_receives_no_task_or_skill_and_reuses_only_identical_inline_images(tmp_path):
    requests = []
    def create(**kwargs):
        requests.append(kwargs)
        return response("Visible table: A = 42")
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    reader = ImageReader({"MODEL": "glm-5.3-flash"}, tmp_path / "vision_usage.jsonl", client=client)
    first = reader.describe(IMAGE)
    assert first == reader.describe(IMAGE) and len(requests) == 1
    assert requests[0]["messages"][0]["content"][1]["image_url"]["url"] == IMAGE
    assert "tools" not in requests[0] and "Visible table" in first
    reader.describe("https://example.test/changing-screenshot.png")
    reader.describe("https://example.test/changing-screenshot.png")
    assert len(requests) == 3  # Remote URLs may change contents.
    events = [json.loads(line) for line in reader.ledger.read_text().splitlines()]
    assert len(events) == 3 and all(e["kind"] == "vision_usage" and e["usage"]["total_tokens"] == 15 for e in events)
    assert IMAGE not in reader.ledger.read_text()


def test_native_message_conversion_preserves_tools_and_does_not_mutate_history():
    class Message:
        def __init__(self, content):
            self.content = content
        def model_copy(self, update):
            return Message(update["content"])
    text = SimpleNamespace(text="Already visible")
    original = Message([text, SimpleNamespace(image_urls=[IMAGE])])
    reader = SimpleNamespace(describe=lambda url: "Observed A=42")
    converted = observe_messages([original], reader, SimpleNamespace)
    assert converted[0].content[0] is text
    assert converted[0].content[1].text == "Observed A=42"
    assert original.content[1].image_urls == [IMAGE]
    assert all(not hasattr(part, "image_urls") for part in converted[0].content)


def test_openhands_installer_converts_before_text_model_formatter(tmp_path, monkeypatch):
    import sys
    from types import ModuleType
    from experiments.revision2026 import vision_bridge
    class LLM:
        def format_messages_for_llm(self, messages):
            return [[part.text for part in message.content] for message in messages]
        def vision_is_active(self):
            return False
    class Message:
        content = [SimpleNamespace(image_urls=[IMAGE])]
        def model_copy(self, update):
            return SimpleNamespace(**update)
    sdk, llm = ModuleType("openhands.sdk"), ModuleType("openhands.sdk.llm")
    sdk.LLM, llm.TextContent = LLM, SimpleNamespace
    monkeypatch.setitem(sys.modules, "openhands.sdk", sdk)
    monkeypatch.setitem(sys.modules, "openhands.sdk.llm", llm)
    monkeypatch.setattr(vision_bridge, "ImageReader", lambda *a, **k: SimpleNamespace(describe=lambda url: "Observed pixels"))
    for key, value in {"MODEL": "glm-5.3-flash", "BASE_URL": "https://example.test", "API_KEY": "synthetic"}.items():
        monkeypatch.setenv("SKILLLIFT_VISION_" + key, value)
    vision_bridge.install_openhands_vision()
    assert LLM().format_messages_for_llm([Message()]) == [["Observed pixels"]]
    assert LLM().vision_is_active() and LLM._skilllift_vision_installed
    vision_bridge.install_openhands_vision()  # idempotent startup imports
    assert LLM().format_messages_for_llm([Message()]) == [["Observed pixels"]]


def test_glm_solver_retries_the_request_and_converts_image_before_next_request(tmp_path, monkeypatch):
    import openai
    from experiments.revision2026 import docker_agent, resilience
    main_requests, vision_requests, tool_calls = [], [], []
    def create(**kwargs):
        if kwargs["model"] == "glm-5.3-flash":
            vision_requests.append(deepcopy(kwargs))
            return response("A visible chart")
        main_requests.append(deepcopy(kwargs))
        if len(main_requests) == 1:
            raise openai.APIConnectionError(request=SimpleNamespace())
        if len(main_requests) == 2:
            return response("Inspect image", calls=[SimpleNamespace(id="read-1", function=SimpleNamespace(name="read_image", arguments='{"path":"/image.png"}'))])
        return response("Done")
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(resilience.time, "sleep", lambda _: None)
    monkeypatch.setattr(docker_agent, "endpoint", lambda model: {"MODEL": "glm-5.3-flash" if model == "glm_vision" else "GLM-5.1", "BASE_URL": "https://example.test", "API_KEY": "synthetic"})
    def tool(*args):
        tool_calls.append(args)
        return "image", IMAGE
    monkeypatch.setattr(docker_agent, "tool_result", tool)
    protocol = load_protocol()
    status, messages = docker_agent.run_solver("fake-container", "PRIVATE TASK GOAL", None, "glm",
        {**protocol["solver"], "vision": protocol["vision"], "retry": protocol["retry"]}, tmp_path, tmp_path / "usage.jsonl")
    assert status == "completed" and len(main_requests) == 3 and len(tool_calls) == 1
    assert len(vision_requests) == 1 and "PRIVATE TASK GOAL" not in json.dumps(vision_requests)
    assert IMAGE not in json.dumps(main_requests) and "A visible chart" in json.dumps(main_requests[-1])
    assert any(message.get("role") == "tool" for message in messages)


def test_vision_failure_stays_an_error_and_has_unknown_cost(tmp_path):
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: response(""))))
    reader = ImageReader({"MODEL": "glm-5.3-flash"}, tmp_path / "usage.jsonl", client=client)
    with pytest.raises(RuntimeError, match="complete textual observation"):
        reader.describe(IMAGE)
    assert json.loads(reader.ledger.read_text())["status"] == "ERROR"
    assert not reader.cache


def test_native_glm_bridge_is_deployed_without_embedding_credentials(tmp_path, monkeypatch):
    from experiments.revision2026 import oracles
    monkeypatch.setattr(oracles, "endpoint", lambda model: {"MODEL": "glm-5.3-flash" if model == "glm_vision" else "GLM-5.1",
                                                           "BASE_URL": "https://example.test", "API_KEY": "synthetic-private-key"})
    # native_adapter sets process-local bridge env for its child; restore after this test.
    for suffix in ("MODEL", "BASE_URL", "API_KEY", "ATTEMPTS", "MAX_TOKENS"):
        monkeypatch.setenv("SKILLLIFT_VISION_" + suffix, "")
    adapter = oracles.native_adapter("skillsbench", "task", "glm", tmp_path, load_protocol())
    source = adapter.settings.patch_source.read_text()
    compile(source, "generated-oh-patch", "exec")
    assert "install_openhands_vision()" in source and "synthetic-private-key" not in source
    assert adapter.settings.model == "vllm/GLM-5.1"


def test_skillopt_bound_and_guard_leave_budget_unchanged():
    from experiments.revision2026.budget import OracleBudget, skillopt_budget
    from experiments.revision2026.optimization import skillopt_config
    protocol = load_protocol()
    assert skillopt_budget(protocol) == {"initial_selection": 2, "training": 3, "candidate_selection_max": 6, "minimum": 5, "maximum": 11}
    budget = OracleBudget(11)
    for _ in range(11):
        budget.consume()
    with pytest.raises(RuntimeError, match="11-execution"):
        budget.consume()
    cfg = skillopt_config(protocol, ROOT, ROOT / "seed", {"MODEL": "fake", "BASE_URL": "https://example.test"}, 0)
    assert not cfg["eval_test"] and not cfg["use_slow_update"] and not cfg["use_meta_skill"]
    protocol["skillopt"]["num_epochs"] = 2
    assert skillopt_budget(protocol)["maximum"] == 8


def test_skillopt_http_retry_is_not_multiplied_by_sdk_or_backend_loops(tmp_path, monkeypatch):
    import sys
    from types import ModuleType
    from experiments.revision2026 import optimization, resilience
    attempts, sdk_options, backend_retries = [], [], []
    def create(**kwargs):
        attempts.append(1)
        if len(attempts) < 4:
            raise TimeoutError("timed out")
        return "ok"
    class Client:
        chat = SimpleNamespace(completions=SimpleNamespace(create=create))
        def with_options(self, **kwargs):
            sdk_options.append(kwargs)
            return self
    backend = SimpleNamespace(_get_client=lambda role: Client())
    def impl(messages, max_completion_tokens, retries, stage, *, role):
        backend_retries.append(retries)
        return backend._get_client(role).chat.completions.create(messages=messages)
    backend._chat_messages_impl = impl
    parent, model = ModuleType("skillopt"), ModuleType("skillopt.model")
    model.openai_compatible_backend = backend
    monkeypatch.setitem(sys.modules, "skillopt", parent)
    monkeypatch.setitem(sys.modules, "skillopt.model", model)
    monkeypatch.setattr(resilience.time, "sleep", lambda _: None)
    optimization._harden_skillopt(load_protocol(), tmp_path / "usage.jsonl")
    assert backend._chat_messages_impl([], 100, 10, "stage", role="optimizer") == "ok"
    assert len(attempts) == 4 and backend_retries == [1] and sdk_options == [{"max_retries": 0}]


def test_run_all_supplementary_does_not_launch_historical_rebuild(tmp_path):
    import os
    import subprocess
    source = ROOT / "experiments/revision2026/run_all.sh"
    script = tmp_path / "experiments/revision2026/run_all.sh"
    script.parent.mkdir(parents=True)
    script.write_text(source.read_text())
    python = tmp_path / ".venv-revision2026/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> calls.log\n')
    python.chmod(0o755)
    env = {key: value for key, value in os.environ.items() if key not in {"LEGACY_ARCHIVE", "WORKERS", "RUN_ROOT"}}
    subprocess.run(["bash", str(script), "supplementary", "deepseek"], check=True, cwd=tmp_path, env=env, capture_output=True)
    calls = (tmp_path / "calls.log").read_text().splitlines()
    assert len(calls) == 4 and all("--models deepseek" in line for line in calls)
    assert all("--workers 8" in line for line in calls[1:])
    assert not any("table2" in line or "historical" in line or "wildclaw" in line for line in calls)


def test_vision_cost_is_included_once_and_separately_identified(tmp_path):
    from experiments.revision2026.common import write_json
    from experiments.revision2026.statistics import cost_report
    ledger = tmp_path / "training/skillsbench/glm/direct_oracle/run-0/task/vision_usage.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(json.dumps({"kind": "vision_usage", "model": "glm-5.3-flash", "usage": {"total_tokens": 15}}) + "\n")
    cost_report(tmp_path)
    import csv
    row = next(csv.DictReader((tmp_path / "report/cost_by_method.csv").open()))
    assert row["reported_tokens"] == "15" and row["vision_tokens"] == "15" and row["vision_calls"] == "1"
