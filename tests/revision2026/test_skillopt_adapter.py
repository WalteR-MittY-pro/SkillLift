"""Exercise the pinned official trainer, replacing only paid HTTP and rollouts."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from experiments.revision2026 import docker_agent, optimization
from experiments.revision2026.common import load_protocol, read_json, write_json
from experiments.revision2026.oracles import TrainingOracle
from experiments.revision2026.sources import Instance, PINS, verify_source


def test_tool_actions_and_out_of_order_observations_reach_official_formatter():
    pytest.importorskip("skillopt")
    from skillopt.gradient.reflect import fmt_trajectory

    messages = [
        {"role": "system", "content": "private configuration"},
        {"role": "assistant", "content": "Inspect both files", "tool_calls": [
            {"id": "a", "function": {"name": "shell", "arguments": '{"command":"cat input.csv"}'}},
            {"id": "b", "function": {"name": "shell", "arguments": '{"command":"cat schema.json"}'}},
        ]},
        {"role": "tool", "tool_call_id": "b", "content": "schema output"},
        {"role": "tool", "tool_call_id": "a", "content": "CSV output"},
        {"role": "assistant", "content": "检查完成"},
    ]
    original = deepcopy(messages)
    converted = optimization.skillopt_trajectory(messages)
    actions = [row for row in converted if row.get("type") == "tool_call"]
    assert actions[0]["obs"] == "CSV output"
    assert actions[1]["obs"] == "schema output"
    formatted = fmt_trajectory(converted)
    assert all(text in formatted for text in ("cat input.csv", "cat schema.json", "CSV output", "schema output", "检查完成"))
    assert "private configuration" not in formatted
    assert messages == original


@pytest.fixture
def official_setup(tmp_path, monkeypatch):
    pytest.importorskip("skillopt")
    import httpx
    from openai import OpenAI
    from skillopt.model import openai_compatible_backend as backend

    assert verify_source("skillopt").resolve() in Path(backend.__file__).resolve().parents
    seed = tmp_path / "seed"
    (seed / "procedure").mkdir(parents=True)
    (seed / "procedure/SKILL.md").write_text("# Procedure\nRead inputs. 检查输入。\n", encoding="utf-8")
    (seed / "procedure/helper.txt").write_text("preserve this asset", encoding="utf-8")
    task = tmp_path / "task"
    task.mkdir()
    (task / "instruction.md").write_text("Produce an output from the public input.", encoding="utf-8")
    protocol = load_protocol()
    protocol["solver"]["extra_body_by_model"] = {"glm": {"thinking": {"type": "disabled"}}}
    ledger = tmp_path / "usage.jsonl"
    oracle = TrainingOracle(benchmark="skilllearnbench", family="demo", model="glm", seed=seed,
                            protocol=protocol, root=tmp_path / "oracle", ledger=ledger,
                            instance=Instance("demo", 1, task))
    secret = "synthetic-test-only-key"
    monkeypatch.setattr(optimization, "endpoint", lambda _: {"MODEL": "test-model", "BASE_URL": "https://test.invalid/v1", "API_KEY": secret})
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["model"] == "test-model"
        assert payload["stream"] is False and payload["thinking"] == {"type": "disabled"}
        text = json.dumps({"patch": {"reasoning": "Check outputs before submission", "edits": [
            {"op": "append", "content": "Verify output dimensions before submission."}
        ]}})
        return httpx.Response(200, json={"id": "offline", "object": "chat.completion", "created": 0, "model": "test-model",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130}})

    http_client = httpx.Client(transport=httpx.MockTransport(respond))
    client = OpenAI(base_url="https://test.invalid/v1", api_key=secret, http_client=http_client)
    monkeypatch.setattr(backend, "_get_client", lambda _: client)
    # Restore the adapter's HTTP wrapper at fixture teardown as well.
    monkeypatch.setattr(backend, "_chat_messages_impl", backend._chat_messages_impl)
    monkeypatch.setattr(backend, "_revision_failure_state", None, raising=False)
    executions = []

    def evaluate(instance, portfolio, model, settings, output, *args):
        assert instance.number == 1
        content = (Path(portfolio) / "procedure/SKILL.md").read_text(encoding="utf-8")
        executions.append(content)
        write_json(output / "conversation.json", [
            {"role": "user", "content": "Read the public input."},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "cmd", "type": "function", "function": {"name": "shell", "arguments": '{"command":"inspect-output --dimensions"}'}}
            ]},
            {"role": "tool", "tool_call_id": "cmd", "content": "Observed dimensions: 12 by 8"},
        ])
        reward = 0.75 if "Verify output dimensions" in content else 0.25
        result = {"status": "OK", "reward": reward, "public_feedback": {
            "reward": reward, "official_assertion": "must never reach optimizer"
        }}
        write_json(output / "result.json", result)
        return result

    monkeypatch.setattr(docker_agent, "evaluate", evaluate)
    yield oracle, protocol, ledger, requests, executions, secret
    client.close()


def test_official_trainer_updates_and_selects_skill_using_training_only(tmp_path, official_setup):
    oracle, protocol, ledger, requests, executions, secret = official_setup
    winner, meta = optimization.run_skillopt(oracle, protocol, tmp_path / "training", "glm", ledger, 0)
    assert "Verify output dimensions" in (winner / "procedure/SKILL.md").read_text(encoding="utf-8")
    assert (winner / "procedure/helper.txt").read_text() == "preserve this asset"
    assert meta["revision"] == PINS["skillopt"][1]
    # The official trainer reuses the second candidate's selection score when
    # the third round proposes exactly the same skill again.
    assert meta["logical_oracle_executions"] == len(executions) == 9
    assert meta["oracle_budget"]["maximum"] == 11
    summary = read_json(tmp_path / "training/official/summary.json")
    assert summary["best_selection_hard"] == 0.75
    assert summary["total_steps"] == 3 and summary["best_step"] == 1
    assert requests
    prompt = json.dumps(requests, ensure_ascii=False)
    assert "inspect-output --dimensions" in prompt and "Observed dimensions" in prompt
    assert "must never reach optimizer" not in prompt
    config = read_json(tmp_path / "training/official/config.json")
    assert config["eval_test"] is False and config["test_env_num"] == 0
    assert secret not in json.dumps(config)
    events = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert sum(e["usage"]["total_tokens"] for e in events if e["kind"] == "skillopt_api_usage") == 130 * len(requests)


def test_official_trainer_rejects_infrastructure_failure_instead_of_freezing(tmp_path, official_setup, monkeypatch):
    oracle, protocol, ledger, requests, executions, secret = official_setup
    monkeypatch.setattr(docker_agent, "evaluate", lambda *args: {"status": "ERROR", "error": "invalid fixture environment"})
    with pytest.raises(RuntimeError, match="SkillOpt oracle failed"):
        optimization.run_skillopt(oracle, protocol, tmp_path / "training", "glm", ledger, 0)
    assert not (tmp_path / "training/winner").exists()
    assert not requests
