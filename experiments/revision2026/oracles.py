from __future__ import annotations

import json
from pathlib import Path
import sys
import uuid

from .common import ROOT, append_event, digest, endpoint, read_json
from .sources import source_path


def clean_messages(messages):
    result = []
    for message in messages:
        if message.get("role") not in {"user", "assistant", "tool", "toolResult"}:
            continue
        content = message.get("content", "")
        calls = []
        if isinstance(content, list):
            calls = [{"id": block.get("id", ""), "type": "function", "function": {"name": block.get("name", ""), "arguments": json.dumps(block.get("arguments", {}))}}
                     for block in content if isinstance(block, dict) and block.get("type") == "toolCall"]
            content = "\n".join(str(block.get("text", "")) for block in content if isinstance(block, dict) and block.get("type") == "text")
        row = {"role": "tool" if message["role"] == "toolResult" else message["role"], "content": str(content)}
        for key in ("tool_calls", "tool_call_id", "name"):
            if key in message:
                row[key] = message[key]
        if message.get("toolCallId"):
            row["tool_call_id"] = message["toolCallId"]
        if calls:
            row["tool_calls"] = calls
        result.append(row)
    return result


def execution_root(marker_path):
    """Resolve one exact trial; never recursively pick another run's latest output."""
    marker = read_json(marker_path)
    if marker.get("result_path"):
        return Path(marker["result_path"]).parent
    if marker.get("task_run_record_path"):
        record = read_json(marker["task_run_record_path"])
        candidates = set()
        for field in ("score_path", "raw_output_path"):
            if record.get(field):
                file = Path(record[field])
                payload = read_json(file)
                if payload.get("output_dir"):
                    output = Path(payload["output_dir"])
                    if (output / "chat.jsonl").is_file():
                        candidates.add(output.resolve())
                if (file.parent / "chat.jsonl").is_file():
                    candidates.add(file.parent.resolve())
        if len(candidates) > 1:
            raise ValueError("Ambiguous WildClaw execution roots in one logical trial")
        if candidates:
            return candidates.pop()
    return Path(marker_path).parent


def public_conversation(root):
    """Read public solver events in an exact trial root, excluding thought/config."""
    root = Path(root)
    acp = root / "agent/acp_trajectory.jsonl"
    if not acp.is_file():
        acp = root / "trajectory/acp_trajectory.jsonl"
    if acp.is_file():
        messages = []
        for line in acp.read_text().splitlines():
            event = json.loads(line)
            if event.get("type") in {"user_message", "agent_message"}:
                messages.append({"role": "user" if event["type"] == "user_message" else "assistant", "content": event.get("text", "")})
            elif event.get("type") == "tool_call":
                content = "\n".join(str(block.get("content", {}).get("text", "")) for block in event.get("content", []) if isinstance(block.get("content"), dict))
                messages.append({"role": "tool", "tool_call_id": event.get("tool_call_id", ""),
                                 "content": str(event.get("title", "")) + "\n" + content})
        return clean_messages(messages)
    chat = root / "chat.jsonl"
    if chat.is_file():
        return clean_messages([event["message"] for line in chat.read_text().splitlines()
                               if (event := json.loads(line)).get("type") == "message" and isinstance(event.get("message"), dict)])
    for relative in ["agent/trajectory.json", "agent/conversation.json", "conversation.json"]:
        path = root / relative
        if path.is_file():
            value = read_json(path)
            return clean_messages(value if isinstance(value, list) else value.get("messages", []))
    return []


def verifier_diagnostics(root):
    """Expose actual criterion outcomes, not assertion source/expected answers."""
    path = Path(root) / "verifier/ctrf.json"
    if not path.is_file():
        return []
    data = read_json(path)
    return [{"criterion": str(row.get("name", "")), "status": row.get("status")}
            for row in data.get("results", {}).get("tests", []) if isinstance(row, dict)]


def checked_native_result(evaluation):
    """A native infrastructure failure must not become an apparently valid zero."""
    if not evaluation.is_valid or not evaluation.artifact_ref:
        return evaluation
    marker = read_json(evaluation.artifact_ref)
    if not marker.get("result_path"):
        from .verification_audit import scoring_errors
        if marker.get("score_path"):
            raw = read_json(marker["score_path"])
            errors = scoring_errors(raw.get("scores", {}))
            if errors:
                from skilllift.coordinator import CandidateEvaluation
                return CandidateEvaluation.infrastructure_failure("WildClaw judge failed: " + ", ".join(errors))
        return evaluation
    raw = read_json(marker["result_path"])
    error = raw.get("error")
    if not error:
        return evaluation
    kind = str(error.get("exception_type", error.get("type", ""))) if isinstance(error, dict) else str(error)
    # A reached solver budget remains a real task outcome; do not retry it for a
    # higher score. Other errors (API, setup, grader) require an explicit retry.
    allowed = ("AgentTimeoutError", "AgentTimeLimitExceeded", "MaxIterations", "MaxSteps")
    if any(name in kind for name in allowed):
        return evaluation
    from skilllift.coordinator import CandidateEvaluation
    return CandidateEvaluation.infrastructure_failure("Native result contains a non-budget execution error; inspect the original trial result")


class TrainingOracle:
    def __init__(self, *, benchmark, family, model, seed, protocol, root, ledger, native=None, instance=None):
        self.benchmark, self.family, self.model = benchmark, family, model
        self.seed, self.protocol, self.root, self.ledger = Path(seed), protocol, Path(root), Path(ledger)
        self.native, self.instance = native, instance
        self.last_feedback = {}
        self.last_conversation = []
        self.calls = 0
        self.valid_rewards = []  # (trial phase, reward) of every valid training execution

    def public_task(self, task_id):
        from skilllift.portfolio import PublicTask
        if task_id != self.family:
            raise ValueError("Unexpected training task")
        if self.instance:
            if self.instance.number != 1:
                raise ValueError("Held-out instance passed to optimizer")
            return PublicTask(task_id, (self.instance.path / "instruction.md").read_text(), "instruction.md")
        return self.native.public_task(task_id)

    def seed_portfolio(self, task_id):
        from skilllift.portfolio import PortfolioRef
        return PortfolioRef.from_directory(task_id=task_id, root=self.seed, config_hash=digest(self.protocol))

    def reward_spec(self):
        from skilllift.coordinator import RewardSpec
        return RewardSpec(terminal_threshold=1.0, minimum=0.0, maximum=1.0)

    def recover(self, *args):
        # Incomplete family attempts restart: the legacy coordinator does not safely
        # persist all Mode-A refinements. Never reuse a training attempt's inner state.
        return None

    def evaluate(self, task_id, portfolio, trial):
        from skilllift.coordinator import CandidateEvaluation
        if task_id != self.family:
            raise ValueError("Unexpected training task")
        self.calls += 1
        if self.instance:
            from .docker_agent import evaluate
            if self.instance.number != 1:
                raise ValueError("Held-out instance passed to optimizer")
            folder = self.root / "oracle" / f"{self.calls:05d}-{uuid.uuid4().hex[:8]}"
            result = evaluate(self.instance, portfolio.root, self.model, self.protocol["solver"], folder, self.ledger, self.root)
            self.last_conversation = public_conversation(folder)
            self.last_feedback = result.get("public_feedback", {"status": result["status"], "error_type": result.get("error_type")})
            self.last_feedback["solver_conversation"] = json.dumps(self.last_conversation, ensure_ascii=False)[-60000:]
            self.last_feedback["oracle_criterion_outcomes"] = verifier_diagnostics(folder)
            if result["status"] != "OK":
                return CandidateEvaluation.infrastructure_failure(result.get("error", "Oracle failed"))
            self.valid_rewards.append((getattr(trial, "phase", None), float(result["reward"])))
            return CandidateEvaluation.valid(result["reward"], result_hash=digest(result), artifact_ref=str(folder / "result.json"), usage_ref=str(self.ledger))
        result = checked_native_result(self.native.evaluate(task_id, portfolio, trial))
        trial_root = execution_root(result.artifact_ref) if result.artifact_ref else self.root / "unavailable-public-trace"
        self.last_conversation = public_conversation(trial_root)
        self.last_feedback = {"reward": result.reward, "infrastructure_error": result.infrastructure_error,
                              "solver_conversation": json.dumps(self.last_conversation, ensure_ascii=False)[-60000:],
                              "trace_missing": not bool(self.last_conversation), "trace_root": str(trial_root)}
        self.last_feedback["oracle_criterion_outcomes"] = verifier_diagnostics(trial_root)
        append_event(self.ledger, {"kind": "native_oracle_rollout", "phase": "training",
                                  "trial": trial.to_dict() if hasattr(trial, "to_dict") else vars(trial),
                                  "result": result.to_dict(), "feedback_source": "solver artifacts only"})
        if result.is_valid:
            self.valid_rewards.append((getattr(trial, "phase", None), float(result.reward)))
        return result


def process_best(valid_rewards):
    """Table 2 metric: best oracle score of any evolved or seed skill observed during evolution.

    SkillLift's internal final re-runs of its winner are kept separate so the two
    arms are compared on the same anchor + candidate executions."""
    search = [reward for phase, reward in valid_rewards if phase != "final"]
    every = [reward for _, reward in valid_rewards]
    return {"best_excluding_internal_final": max(search) if search else None,
            "best_including_internal_final": max(every) if every else None,
            "valid_executions": len(every)}


def native_adapter(benchmark, task_id, model, root, protocol):
    """Existing native harnesses are shared unchanged by the two ablation arms."""
    ep = endpoint(model)
    root = Path(root)
    if benchmark == "skillsbench":
        from skilllift_eval.runners.skillsbench_portfolio_adapter import SkillsBenchPortfolioAdapter, SkillsBenchPortfolioSettings
        patch = ROOT / "scripts" / "oh_skill_patch.py"
        if not patch.is_file():
            raise ValueError("Missing checked-in scripts/oh_skill_patch.py")
        return SkillsBenchPortfolioAdapter(SkillsBenchPortfolioSettings(
            project_root=ROOT, tasks_root=source_path("skillsbench") / "tasks", run_root=root,
            python_executable=Path(sys.executable), model="vllm/" + ep["MODEL"], sandbox="docker",
            base_url=ep["BASE_URL"], api_key=ep["API_KEY"], patch_source=patch, agent="openhands-sse"))
    if benchmark != "wildclawbench":
        raise ValueError(benchmark)
    from skilllift.adapters.wildclawbench import WildClawPortfolioAdapter
    from skilllift_eval.runners.wildclaw import WildClawRunner, WildClawRunSettings
    from skilllift_eval.model_endpoints import ModelEndpointProfile, endpoint_config_hash
    profile = ModelEndpointProfile(model_endpoint_id=model, model_label=model, provider="openai-completions",
                                   provider_model_id=ep["MODEL"], base_url=ep["BASE_URL"], api_key=ep["API_KEY"], stream=model == "glm")
    key = digest(protocol)

    def settings(folder):
        return WildClawRunSettings(run_root=folder, wildclaw_root=source_path("wildclawbench"), model_endpoint=profile,
            matrix_cell_id=key, model_label=model, endpoint_config_hash=endpoint_config_hash(profile), algorithm_param_hash=key,
            benchmark_source_hash=protocol["source_revisions"]["wildclawbench"], task_set_hash=key, run_config_hash=key,
            evaluation_mode="native_end_to_end", context_budget_profile="native_default", visible_feedback_policy_id="public_sanitized_v1",
            oracle_call_budget="unbounded", task_sample_policy_id="full_native_v1", round_budget="paper_default", token_budget="unbounded",
            tasks_mode="single", task_filter=task_id, baseline="revision2026", thinking="high", rate_limit_retries=0)

    return WildClawPortfolioAdapter(wildclaw_root=source_path("wildclawbench"), run_root=root, task_filter=task_id,
                                   settings_factory=settings, runner=WildClawRunner(), config_hash=key)


def native_task_ids(benchmark):
    if benchmark == "skillsbench":
        paths = sorted((source_path(benchmark) / "tasks").glob("*/task.md"))
        return [path.parent.name for path in paths]
    from skilllift_eval.runners.skilllift_wildclaw_tasks import discover_wildclaw_tasks
    return [task.task_id for task in discover_wildclaw_tasks(source_path(benchmark), (1, 2, 3, 4, 5, 6))]
