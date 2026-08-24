from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
from typing import Any

from skilllift_eval.runners.skillsbench_report import append_usage_record


@dataclass(frozen=True)
class SkillsBenchCommand:
    bench_executable: Path
    tasks_dir: Path
    task_ids: tuple[str, ...]
    agent: str
    model: str
    sandbox: str
    skill_mode: str
    skills_dir: Path
    jobs_dir: Path
    usage_tracking: str = "required"
    environment_manifest: Path | None = None

    def argv(self) -> list[str]:
        command = [
            str(self.bench_executable),
            "-m",
            "skilllift_eval.skillsbench_cli",
            "benchflow",
            "eval",
            "run",
            "--tasks-dir",
            str(self.tasks_dir),
            "--agent",
            self.agent,
            "--model",
            self.model,
            "--sandbox",
            self.sandbox,
            "--skill-mode",
            self.skill_mode,
            "--skills-dir",
            str(self.skills_dir),
            "--jobs-dir",
            str(self.jobs_dir),
            "--usage-tracking",
            self.usage_tracking,
        ]
        for task_id in self.task_ids:
            command.extend(["--include", task_id])
        if self.environment_manifest is not None:
            command.extend(["--environment-manifest", str(self.environment_manifest)])
        return command

    def to_dict(self) -> dict[str, Any]:
        return {
            "bench_executable": str(self.bench_executable),
            "tasks_dir": str(self.tasks_dir),
            "task_ids": list(self.task_ids),
            "agent": self.agent,
            "model": self.model,
            "sandbox": self.sandbox,
            "skill_mode": self.skill_mode,
            "skills_dir": str(self.skills_dir),
            "jobs_dir": str(self.jobs_dir),
            "usage_tracking": self.usage_tracking,
            "environment_manifest": (
                str(self.environment_manifest) if self.environment_manifest is not None else None
            ),
            "argv": self.argv(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillsBenchCommand":
        return cls(
            bench_executable=Path(data["bench_executable"]),
            tasks_dir=Path(data["tasks_dir"]),
            task_ids=tuple(data["task_ids"]),
            agent=str(data["agent"]),
            model=str(data["model"]),
            sandbox=str(data["sandbox"]),
            skill_mode=str(data["skill_mode"]),
            skills_dir=Path(data["skills_dir"]),
            jobs_dir=Path(data["jobs_dir"]),
            usage_tracking=str(data.get("usage_tracking", "required")),
            environment_manifest=(
                Path(data["environment_manifest"]) if data.get("environment_manifest") else None
            ),
        )


class SkillsBenchSubprocessAdapter:
    def execute(
        self,
        command: SkillsBenchCommand,
        *,
        project_root: Path,
        task_id: str,
        skill_id: str,
        base_url: str,
        api_key: str,
        ledger_path: Path,
        usage_context: dict[str, Any],
        exact_result: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any], Path]:
        before_hash = directory_hash(command.skills_dir)
        command.jobs_dir.mkdir(parents=True, exist_ok=False)
        log_path = command.jobs_dir / "benchflow.log"
        env = os.environ.copy()
        env["BENCHFLOW_PROVIDER_BASE_URL"] = base_url
        env["BENCHFLOW_PROVIDER_API_KEY"] = api_key
        env.update(provider_auth_env(command.model, api_key))
        env["PYTHONPATH"] = os.pathsep.join(
            [str(project_root), env.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)
        with log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                command.argv(),
                cwd=project_root,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
        after_hash = directory_hash(command.skills_dir)
        if after_hash != before_hash:
            raise RuntimeError(f"candidate skill changed during SkillsBench execution: {skill_id}")
        if completed.returncode != 0:
            raise RuntimeError(
                f"SkillsBench failed for {task_id}/{skill_id} with exit code {completed.returncode}; log={log_path}"
            )
        result_path = (
            find_exact_result_json(command.jobs_dir, task_id)
            if exact_result
            else find_result_json(command.jobs_dir, task_id)
        )
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        if payload.get("skill_mode") != "with-skill" or not payload.get("effective_skills_dir"):
            raise RuntimeError(f"SkillsBench did not load candidate skill for {task_id}/{skill_id}")
        cell = extract_oracle_cell(result_path, task_id, skill_id)
        usage = extract_usage_record(
            payload,
            source="benchmark",
            role="openhands",
            phase=str(usage_context["phase"]),
            usage_record_id=str(usage_context["usage_record_id"]),
            **{key: value for key, value in usage_context.items() if key not in {"phase", "usage_record_id"}},
        )
        append_usage_record(ledger_path, usage)
        return cell, payload, result_path

    def reuse(
        self,
        jobs_scope: Path,
        command: SkillsBenchCommand,
        *,
        project_root: Path,
        task_id: str,
        skill_id: str,
        ledger_path: Path,
        usage_context: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], Path] | None:
        del project_root
        try:
            result_path = find_result_json(jobs_scope, task_id)
        except ValueError:
            return None
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if payload.get("skill_mode") != "with-skill" or not payload.get("effective_skills_dir"):
            return None
        if Path(str(payload.get("effective_skills_dir"))).resolve() != command.skills_dir.resolve():
            return None
        try:
            cell = extract_oracle_cell(result_path, task_id, skill_id)
        except ValueError:
            return None
        usage = extract_usage_record(
            payload,
            source="benchmark",
            role="openhands",
            phase=str(usage_context["phase"]),
            usage_record_id=str(usage_context["usage_record_id"]),
            **{key: value for key, value in usage_context.items() if key not in {"phase", "usage_record_id"}},
        )
        append_usage_record(ledger_path, usage)
        return cell, payload, result_path


def register_openhands_sse() -> Any:
    register_benchflow_prebuilt_cleanup()
    from benchflow.agents import registry

    existing = registry.AGENTS.get("openhands-sse")
    if existing is not None:
        return existing
    base = registry.AGENTS["openhands"]
    # Skill-injection patch installer: runs after deploy_skills has copied
    # oh_skill_patch.py into /skills, before the ACP server starts. Use an
    # agent-writable sitecustomize directory so the patch auto-imports without
    # writing to the root-owned OpenHands installation.
    patch_install = (
        "export OH_SKILLS_RECEIPT_ROOT=/logs/agent/skilllift-skills-receipts && "
        "if [ -f /skills/oh_skill_patch.py ]; then "
        "mkdir -p /tmp/skilllift-oh-patch && "
        "cp /skills/oh_skill_patch.py /tmp/skilllift-oh-patch/oh_skill_patch.py && "
        "printf 'import oh_skill_patch\\n' > /tmp/skilllift-oh-patch/sitecustomize.py && "
        'export PYTHONPATH="/tmp/skilllift-oh-patch${PYTHONPATH:+:$PYTHONPATH}"; '
        "fi && "
    )
    streaming_launch = (
        'OH_PYTHON=$(head -n 1 "$(command -v openhands)" | sed "s/^#!//") && '
        '"$OH_PYTHON" -c \'import os; from pathlib import Path; '
        "from openhands.sdk import LLM; from openhands_cli.utils import get_default_cli_agent; "
        'model = os.environ["LLM_MODEL"]; model = model if "/" in model else f"openai/{model}"; '
        'llm = LLM(model=model, api_key=os.environ["LLM_API_KEY"], '
        'base_url=os.environ.get("LLM_BASE_URL") or None, usage_id="agent"); '
        'agent = get_default_cli_agent(llm); '
        'Path.home().joinpath(".openhands", "agent_settings.json").write_text('
        'agent.model_dump_json(context={"expose_secrets": True}))\' && '
        + patch_install +
        '"$OH_PYTHON" -c \'import asyncio; from openhands_cli.acp_impl.agent import run_acp_server; '
        "asyncio.run(run_acp_server(initial_confirmation_mode=\"always-approve\", streaming_enabled=True))'"
    )
    launch_cmd = base.launch_cmd.replace(
        "openhands acp --always-approve --override-with-envs", streaming_launch
    )
    if launch_cmd == base.launch_cmd:
        raise RuntimeError("BenchFlow OpenHands launch command is incompatible with SSE registration")
    # Also patch the base "openhands" config so direct ``--agent openhands`` runs
    # get skill injection too.
    base_acp_marker = "openhands acp --always-approve --override-with-envs"
    if base_acp_marker in base.launch_cmd:
        base_launch_patched = base.launch_cmd.replace(
            base_acp_marker, patch_install + base_acp_marker, 1
        )
        registry.AGENTS["openhands"] = replace(base, launch_cmd=base_launch_patched)
        registry.AGENT_LAUNCH["openhands"] = base_launch_patched
    config = replace(
        base,
        name="openhands-sse",
        description="OpenHands agent via streaming ACP",
        launch_cmd=launch_cmd,
        env_mapping={**base.env_mapping, "BENCHFLOW_PROVIDER_MODEL": "LLM_MODEL"},
    )
    registry.AGENTS[config.name] = config
    registry.AGENT_INSTALLERS[config.name] = config.install_cmd
    registry.AGENT_LAUNCH[config.name] = config.launch_cmd
    return config


def register_benchflow_prebuilt_cleanup(sandbox_cls: type[Any] | None = None) -> None:
    if sandbox_cls is None:
        from benchflow.sandbox.docker import DockerSandbox

        sandbox_cls = DockerSandbox
    if getattr(sandbox_cls, "_skilllift_preserves_prebuilt_images", False):
        return

    original = sandbox_cls._run_docker_compose_command

    async def run_without_prebuilt_image_removal(
        self,
        command: list[str],
        check: bool = True,
        timeout_sec: int | None = None,
    ) -> Any:
        effective = list(command)
        if getattr(self, "_use_prebuilt", False):
            if effective[:1] == ["down"]:
                # Preserve locally prebuilt images by stripping `--rmi all`
                # from `compose down`. The prewarm pipeline owns image lifecycle.
                for index in range(len(effective) - 1):
                    if effective[index : index + 2] == ["--rmi", "all"]:
                        del effective[index : index + 2]
                        break
            elif effective[:1] == ["up"] and "--pull" not in effective:
                # Force `--pull never`: prewarm guarantees the image is local, so
                # compose must never contact the registry (which fails on 403/timeout
                # from mirrors like daocloud and turns into anchor_indeterminate).
                effective[1:1] = ["--pull", "never"]
        return await original(self, effective, check=check, timeout_sec=timeout_sec)

    sandbox_cls._run_docker_compose_command = run_without_prebuilt_image_removal
    sandbox_cls._skilllift_preserves_prebuilt_images = True


def _has_usable_reward(payload: dict[str, Any]) -> bool:
    """A result is usable when the verifier produced a reward in [0, 1].

    Mirrors :func:`extract_oracle_cell`: a wall-clock timeout that still ran the
    verifier yields a valid reward, while an install/transport failure that
    prevented the verifier from running leaves no reward at all.
    """
    rewards = payload.get("rewards")
    reward = rewards.get("reward") if isinstance(rewards, dict) else None
    return isinstance(reward, (int, float)) and 0.0 <= float(reward) <= 1.0


def find_result_json(jobs_dir: Path, task_id: str) -> Path:
    matches: list[tuple[Path, dict[str, Any]]] = []
    for path in jobs_dir.rglob("result.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("task_name") == task_id:
            matches.append((path, payload))
    valid = [(path, payload) for path, payload in matches if _has_usable_reward(payload)]
    if valid:
        # When a job ran more than once (concurrent rollouts or a resume retry), more
        # than one usable result may exist. Prefer the most recent started_at so a
        # fresh re-run wins over a stale earlier attempt.
        return max(valid, key=lambda item: str(item[1].get("started_at") or ""))[0]
    if matches:
        return max(matches, key=lambda item: str(item[1].get("started_at") or ""))[0]
    raise ValueError(
        f"expected one valid result.json for {task_id} under {jobs_dir}, found 0 of {len(matches)}"
    )


def find_exact_result_json(trial_jobs_dir: Path, task_id: str) -> Path:
    return find_result_json(trial_jobs_dir, task_id)


def extract_oracle_cell(result_path: Path, task_id: str, skill_id: str) -> dict[str, Any]:
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    if payload.get("task_name") != task_id:
        raise ValueError(
            f"invalid SkillsBench result for {task_id}: task_name={payload.get('task_name')!r}"
        )
    # The reward is computed by the verifier from the agent's final artifacts, so it
    # stays authoritative even when the run was cut short by a wall-clock/agent
    # timeout or carries a partial trajectory. Accept any result with a valid
    # reward and only reject results where the reward is absent or malformed,
    # which signals a genuinely unusable run (e.g. verifier never executed).
    rewards = payload.get("rewards")
    if not isinstance(rewards, dict) or not isinstance(rewards.get("reward"), (int, float)):
        failures = [
            key
            for key in (
                "error",
                "error_category",
                "verifier_error",
                "verifier_error_category",
                "transport_error_info",
                "api_error_info",
                "agent_timeout_info",
                "verifier_timeout_info",
            )
            if payload.get(key) is not None
        ]
        raise ValueError(
            f"invalid SkillsBench result for {task_id}: no usable reward, failures={failures}"
        )
    reward = float(rewards["reward"])
    if not 0.0 <= reward <= 1.0:
        raise ValueError(f"SkillsBench reward outside [0,1] for {task_id}: {reward}")
    return {
        "task_id": task_id,
        "skill_id": skill_id,
        "oracle_result": {
            "rewards": rewards,
            "n_tool_calls": int(payload.get("n_tool_calls") or 0),
            "n_skill_invocations": int(payload.get("n_skill_invocations") or 0),
        },
    }


def extract_usage_record(
    payload: dict[str, Any],
    *,
    source: str,
    role: str,
    phase: str,
    usage_record_id: str,
    **context: Any,
) -> dict[str, Any]:
    tracking = payload.get("usage_tracking") or {}
    agent_result = payload.get("agent_result") or {}
    enabled = tracking.get("status") == "enabled"
    total_tokens = _optional_number(agent_result.get("total_tokens")) if enabled else None
    parts = [
        _optional_number(agent_result.get("n_input_tokens")),
        _optional_number(agent_result.get("n_output_tokens")),
        _optional_number(agent_result.get("n_cache_read_tokens")),
        _optional_number(agent_result.get("n_cache_creation_tokens")),
    ]
    breakdown_available = enabled and any(value not in (None, 0) for value in parts)
    if not breakdown_available:
        parts = [None, None, None, None]
    cost = _optional_number(agent_result.get("cost_usd")) if enabled else None
    if cost is None and enabled:
        cost = _optional_number((payload.get("final_metrics") or {}).get("total_cost_usd"))
    return {
        "usage_record_id": usage_record_id,
        "source": source,
        "role": role,
        "phase": phase,
        **context,
        "input_tokens": parts[0],
        "output_tokens": parts[1],
        "cache_read_tokens": parts[2],
        "cache_creation_tokens": parts[3],
        "total_tokens": total_tokens,
        "cost_usd": cost,
        "usage_source": tracking.get("usage_source") if enabled else None,
        "breakdown_available": breakdown_available,
    }


def _optional_number(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) else None


def provider_auth_env(model: str, api_key: str) -> dict[str, str]:
    if model.startswith("vllm/"):
        return {"OPENAI_API_KEY": api_key}
    return {}


def directory_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root)
        if ".skilllift-receipts" in relative.parts:
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
