from __future__ import annotations

import contextvars
import json
import os
import re
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from skilllift.ranking import (
    build_rank_contrast,
    build_criterion_contrast,
    assign_tie_aware_ranks,
    rank_oracle_scores,
    rank_score_groups,
    tie_aware_rank_alignment,
)
from skilllift.schemas import (
    EvoSkill,
    OracleFeedback,
    OracleScore,
    Receipt,
    ReceiptRevisionInput,
    SkillKey,
    TaskRanking,
    TaskSpec,
    VerifierScore,
)
from skilllift_eval.schemas import SkillBundle, stable_hash
from skilllift_eval.skills.tau2_loader import Tau2SkillLoader


TaskRunner = Callable[[dict[str, Any], Any, int], Any]
FusionRunner = Callable[[str, str], str]
FUSION_PROMPT_TEMPLATE_ID = "skilllift_tau2_master_skill_fusion_v1"
EMPTY_MESSAGE_ERROR_MARKERS = (
    "AssistantMessage must have either content or tool_calls",
    "UserMessage must have either content or tool_calls",
)
TAU2_ORACLE_EMPTY_MESSAGE_MAX_ATTEMPTS = 3
TAU2_NL_ASSERTION_LLM_ARG_KEYS = frozenset(
    {
        "api_key",
        "api_base",
        "base_url",
        "custom_llm_provider",
        "timeout",
        "temperature",
        "num_retries",
    }
)

# Per-task token usage accumulator. Set in `_default_tau2_run_single_task`,
# incremented in `_patch_tau2_completion_for_noargs`, read by `_save_trajectory`.
# None means "not currently inside a tracked task".
_tau2_token_usage: contextvars.ContextVar[Optional[dict[str, Any]]] = contextvars.ContextVar(
    "skilllift_tau2_token_usage", default=None
)


@dataclass(frozen=True)
class SkillLiftTau2TrainConfig:
    run_root: Path
    manifest_path: Path
    model: str
    oracle_threshold: float = 0.9
    token_budget: str = "native_default"
    seed: int = 42
    max_steps: int = 100
    domain_policy_by_domain: dict[str, str] = field(default_factory=dict)
    tau2_llm_args: dict[str, Any] = field(default_factory=dict)
    save_trajectory: bool = field(
        default_factory=lambda: not os.environ.get("SKILLLIFT_NO_TRAJECTORY")
    )


@dataclass(frozen=True)
class Tau2TrainCluster:
    cluster_id: str
    domain: str
    semantic_key: str
    task_ids: list[str]
    issue_type: str = ""


TaskLoader = Callable[[Tau2TrainCluster], list[Any]]


@dataclass(frozen=True)
class Tau2SkillBatchResult:
    task_skill_scores: dict[tuple[str, SkillKey], OracleScore]
    per_task_rankings: list[TaskRanking]
    aggregate_oracle_scores: dict[SkillKey, OracleScore]
    aggregate_oracle_rank: list[SkillKey]


@dataclass(frozen=True)
class SkillLiftTau2TrainResult:
    clusters: list[Tau2TrainCluster]
    artifact_paths: list[Path]


@dataclass(frozen=True)
class SkillLiftTau2SmokeResult:
    artifact_path: Path
    cluster_artifact_path: Path
    cluster: Tau2TrainCluster


@dataclass(frozen=True)
class SkillLiftTau2FusionResult:
    artifact_path: Path
    master_skill_path: Path
    best_skill_bundle_path: Path


@dataclass(frozen=True)
class SkillLiftTau2TestEvalResult:
    artifact_path: Path
    master_skill_path: Path
    best_skill_bundle_path: Path
    cluster: Tau2TrainCluster


class SkillLiftTau2TrainRunner:
    def __init__(
        self,
        *,
        task_loader: TaskLoader,
        run_single_task: TaskRunner | None = None,
    ) -> None:
        self._task_loader = task_loader
        self._run_single_task = run_single_task or _default_tau2_run_single_task

    def run(
        self,
        config: SkillLiftTau2TrainConfig,
        *,
        skills: dict[SkillKey, EvoSkill],
        source_train_run_id: str,
    ) -> SkillLiftTau2TrainResult:
        clusters = load_train_clusters(config.manifest_path)
        artifact_paths: list[Path] = []
        for cluster in clusters:
            tasks = self._task_loader(cluster)
            batch_result = evaluate_tau2_skill_batch(
                tasks,
                skills,
                config,
                domain=cluster.domain,
                cluster_id=cluster.cluster_id,
                run_single_task=self._run_single_task,
            )
            artifact_paths.append(
                write_train_cluster_artifact(
                    config=config,
                    cluster_id=cluster.cluster_id,
                    domain=cluster.domain,
                    task_ids=cluster.task_ids,
                    skills=skills,
                    batch_result=batch_result,
                    source_train_run_id=source_train_run_id,
                )
            )
        return SkillLiftTau2TrainResult(clusters=clusters, artifact_paths=artifact_paths)


def run_train_evolve_smoke(
    config: SkillLiftTau2TrainConfig,
    *,
    skills: dict[SkillKey, EvoSkill],
    task_loader: TaskLoader,
    run_single_task: TaskRunner | None = None,
    source_train_run_id: str,
    smoke_id: str = "smoke-min",
) -> SkillLiftTau2SmokeResult:
    clusters = load_train_clusters(config.manifest_path)
    if not clusters:
        raise ValueError("tau2 train_evolve smoke requires at least one train cluster")
    cluster = _select_smoke_cluster(clusters)
    tasks = task_loader(cluster)
    batch_result = evaluate_tau2_skill_batch(
        tasks,
        skills,
        config,
        domain=cluster.domain,
        cluster_id=cluster.cluster_id,
        run_single_task=run_single_task,
    )
    cluster_artifact_path = write_train_cluster_artifact(
        config=config,
        cluster_id=cluster.cluster_id,
        domain=cluster.domain,
        task_ids=cluster.task_ids,
        skills=skills,
        batch_result=batch_result,
        source_train_run_id=source_train_run_id,
    )
    artifact_path = _write_train_evolve_smoke_artifact(
        config=config,
        smoke_id=smoke_id,
        cluster=cluster,
        skills=skills,
        batch_result=batch_result,
        cluster_artifact_path=cluster_artifact_path,
    )
    return SkillLiftTau2SmokeResult(
        artifact_path=artifact_path,
        cluster_artifact_path=cluster_artifact_path,
        cluster=cluster,
    )


def run_master_skill_fusion_smoke(
    config: SkillLiftTau2TrainConfig,
    *,
    cluster_artifact_paths: list[Path | str],
    domain: str,
    action_types: list[str],
    fusion_model: str,
    fusion_llm: FusionRunner,
    repair_metrics: dict[str, int],
) -> SkillLiftTau2FusionResult:
    cluster_skills = _load_cluster_skills(cluster_artifact_paths)
    prompt = _build_master_skill_fusion_prompt(
        domain=domain,
        action_types=action_types,
        cluster_skills=cluster_skills,
    )
    raw_output = fusion_llm(prompt, fusion_model)
    try:
        master_skill = _extract_master_skill(raw_output)
    except Exception:
        _write_failed_fusion_artifact(
            config=config,
            domain=domain,
            prompt=prompt,
            fusion_model=fusion_model,
            raw_output=raw_output,
        )
        raise
    _validate_master_skill(master_skill)
    return _write_master_skill_fusion_artifact(
        config=config,
        domain=domain,
        action_types=action_types,
        cluster_artifact_paths=[Path(path) for path in cluster_artifact_paths],
        cluster_skills=cluster_skills,
        prompt=prompt,
        fusion_model=fusion_model,
        master_skill=master_skill,
        repair_metrics=repair_metrics,
    )


def run_heldout_test_eval(
    config: SkillLiftTau2TrainConfig,
    *,
    source_fusion_artifact_path: Path | str,
    task_loader: TaskLoader,
    run_single_task: TaskRunner | None = None,
    no_skill_test_score: float | None = None,
    eval_id: str = "smoke-min",
    resume_enabled: bool = False,
) -> SkillLiftTau2TestEvalResult:
    clusters = load_test_eval_clusters(config.manifest_path)
    if not clusters:
        raise ValueError("tau2 held-out test_eval requires at least one test cluster")
    cluster = _select_smoke_cluster(clusters)
    master_bundle, master_skill_path, best_skill_bundle_path, fusion_artifact = _load_master_skill_bundle(
        source_fusion_artifact_path
    )
    if master_bundle.domain and master_bundle.domain != cluster.domain:
        raise ValueError("MASTER_SKILL domain does not match selected test_eval cluster")

    tasks = task_loader(cluster)
    if not tasks:
        raise ValueError("tau2 held-out test_eval requires at least one loaded test task")

    task_runner = run_single_task or _default_tau2_run_single_task
    per_task_scores: list[dict[str, Any]] = []
    prompt_hashes: dict[str, str] = {}
    for task in tasks:
        task_id = _task_id(task)
        task_score_path = _test_eval_task_score_path(config, cluster, task_id)
        run_config = _tau2_test_eval_prompt_context(
            task=task,
            domain=cluster.domain,
            bundle=master_bundle,
            config=config,
            cluster_id=cluster.cluster_id,
        )
        prompt_hashes[task_id] = str(run_config["tau2_prompt_context"].get("prompt_hash") or "")
        if resume_enabled and task_score_path.is_file():
            per_task_scores.append(json.loads(task_score_path.read_text(encoding="utf-8")))
            continue
        simulation = task_runner(run_config, task, config.seed)
        score = _test_eval_score(
            task_id=task_id,
            simulation=simulation,
            threshold=config.oracle_threshold,
        )
        per_task_scores.append(score)
        _write_json(task_score_path, score)

    artifact_path = _write_heldout_test_eval_artifact(
        config=config,
        eval_id=eval_id,
        cluster=cluster,
        tasks=tasks,
        per_task_scores=per_task_scores,
        prompt_hashes=prompt_hashes,
        master_bundle=master_bundle,
        master_skill_path=master_skill_path,
        best_skill_bundle_path=best_skill_bundle_path,
        source_fusion_artifact_path=Path(source_fusion_artifact_path),
        fusion_artifact=fusion_artifact,
        no_skill_test_score=no_skill_test_score,
    )
    return SkillLiftTau2TestEvalResult(
        artifact_path=artifact_path,
        master_skill_path=master_skill_path,
        best_skill_bundle_path=best_skill_bundle_path,
        cluster=cluster,
    )


def load_train_clusters(manifest_path: Path | str) -> list[Tau2TrainCluster]:
    payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if str(payload.get("version") or "") != "v2.4":
        raise ValueError("unsupported manifest version: expected v2.4")
    if payload.get("split") != "train" or payload.get("phase") != "train_evolve":
        raise ValueError("skilllift tau2 train runner requires split=train and phase=train_evolve")

    clusters: list[Tau2TrainCluster] = []
    for domain, entries in (payload.get("domains") or {}).items():
        for entry in entries or []:
            task_ids = [str(task_id) for task_id in entry.get("task_ids") or []]
            clusters.append(
                Tau2TrainCluster(
                    cluster_id=str(entry.get("cluster_id") or ""),
                    domain=str(domain),
                    semantic_key=str(entry.get("semantic_key") or entry.get("issue_type") or ""),
                    issue_type=str(entry.get("issue_type") or ""),
                    task_ids=task_ids,
                )
            )
    return clusters


def load_test_eval_clusters(manifest_path: Path | str) -> list[Tau2TrainCluster]:
    payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if str(payload.get("version") or "") != "v2.4":
        raise ValueError("unsupported manifest version: expected v2.4")
    if payload.get("split") != "test" or payload.get("phase") != "test_eval":
        raise ValueError("skilllift tau2 held-out runner requires split=test and phase=test_eval")

    clusters: list[Tau2TrainCluster] = []
    for domain, entries in (payload.get("domains") or {}).items():
        for entry in entries or []:
            task_ids = [str(task_id) for task_id in entry.get("task_ids") or []]
            clusters.append(
                Tau2TrainCluster(
                    cluster_id=str(entry.get("cluster_id") or ""),
                    domain=str(domain),
                    semantic_key=str(entry.get("semantic_key") or entry.get("issue_type") or ""),
                    issue_type=str(entry.get("issue_type") or ""),
                    task_ids=task_ids,
                )
            )
    return clusters


def _select_smoke_cluster(clusters: list[Tau2TrainCluster]) -> Tau2TrainCluster:
    telecom = [cluster for cluster in clusters if cluster.domain == "telecom"]
    candidates = telecom or clusters
    return sorted(candidates, key=lambda cluster: (-len(cluster.task_ids), cluster.cluster_id))[0]


def evaluate_tau2_skill_batch(
    cluster_tasks: list[Any],
    skills: dict[SkillKey, EvoSkill],
    config: SkillLiftTau2TrainConfig,
    store: Any | None = None,
    *,
    domain: str | None = None,
    cluster_id: str = "",
    run_single_task: TaskRunner | None = None,
) -> Tau2SkillBatchResult:
    del store
    task_runner = run_single_task or _default_tau2_run_single_task
    task_skill_scores: dict[tuple[str, SkillKey], OracleScore] = {}
    task_skill_behavior_summaries: dict[tuple[str, SkillKey], dict[str, Any]] = {}

    for task in cluster_tasks:
        task_id = _task_id(task)
        task_domain = domain or _task_domain(task)
        for key, skill in sorted(skills.items()):
            run_config = _tau2_prompt_context(
                task=task,
                domain=task_domain,
                skill=skill,
                config=config,
                cluster_id=cluster_id,
            )
            simulation = _run_tau2_task_with_empty_message_retries(
                task_runner,
                run_config,
                task,
                config.seed,
            )
            task_skill_scores[(task_id, key)] = _oracle_score_from_simulation(
                skill_key=key,
                task_id=task_id,
                simulation=simulation,
                threshold=config.oracle_threshold,
            )
            task_skill_behavior_summaries[(task_id, key)] = _public_execution_behavior_summary(
                simulation
            )

    per_task_rankings = _build_per_task_rankings(cluster_tasks, skills, task_skill_scores, domain=domain)
    aggregate_scores = _aggregate_oracle_scores(
        skills,
        task_skill_scores,
        task_skill_behavior_summaries,
    )
    aggregate_rank = rank_oracle_scores(aggregate_scores)
    ranks = assign_tie_aware_ranks(
        {key: score.oracle_score for key, score in aggregate_scores.items()}
    )
    for key in aggregate_rank:
        aggregate_scores[key].rank = ranks[key]

    return Tau2SkillBatchResult(
        task_skill_scores=task_skill_scores,
        per_task_rankings=per_task_rankings,
        aggregate_oracle_scores=aggregate_scores,
        aggregate_oracle_rank=aggregate_rank,
    )


def _run_tau2_task_with_empty_message_retries(
    task_runner: TaskRunner,
    run_config: dict[str, Any],
    task: Any,
    seed: int,
) -> Any:
    last_error: ValueError | None = None
    for attempt in range(1, TAU2_ORACLE_EMPTY_MESSAGE_MAX_ATTEMPTS + 1):
        try:
            return task_runner(run_config, task, seed)
        except ValueError as exc:
            if not any(marker in str(exc) for marker in EMPTY_MESSAGE_ERROR_MARKERS):
                raise
            last_error = exc
            print(
                "[skilllift_tau2] retrying tau2 oracle after empty message "
                f"attempt={attempt}/{TAU2_ORACLE_EMPTY_MESSAGE_MAX_ATTEMPTS} "
                f"task_id={run_config.get('task_id')} "
                f"skill={run_config.get('skilllift_skill_key')}",
                flush=True,
            )
    if last_error is not None:
        raise last_error
    raise RuntimeError("tau2 oracle retry loop exited without result or error")


def build_receipt_revision_input(
    *,
    task: TaskSpec,
    receipt: Receipt,
    skills: dict[SkillKey, EvoSkill],
    verifier_scores: dict[SkillKey, VerifierScore],
    batch_result: Tau2SkillBatchResult,
) -> ReceiptRevisionInput:
    verifier_values = {
        key: score.normalized_score for key, score in verifier_scores.items()
    }
    oracle_values = {
        key: score.oracle_score for key, score in batch_result.aggregate_oracle_scores.items()
    }
    verifier_rank = [key for group in rank_score_groups(verifier_values) for key in group]
    oracle_rank = [key for group in rank_score_groups(oracle_values) for key in group]
    alignment = tie_aware_rank_alignment(verifier_values, oracle_values)
    return ReceiptRevisionInput(
        task=task,
        receipt=receipt,
        skills=skills,
        verifier_scores=verifier_scores,
        oracle_scores=batch_result.aggregate_oracle_scores,
        verifier_rank=verifier_rank,
        oracle_rank=oracle_rank,
        rank_alignment=alignment,
        per_task_rankings=batch_result.per_task_rankings,
        oracle_contrast={
            **build_rank_contrast(verifier_values, oracle_values),
            "criterion_contrast": build_criterion_contrast(
                verifier_scores, receipt
            ),
        },
    )


def write_train_cluster_artifact(
    *,
    config: SkillLiftTau2TrainConfig,
    cluster_id: str,
    domain: str,
    task_ids: list[str],
    skills: dict[SkillKey, EvoSkill],
    batch_result: Tau2SkillBatchResult,
    source_train_run_id: str,
) -> Path:
    artifact_dir = config.run_root / "train_evolve" / domain / cluster_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / "cluster_train_artifact.json"
    payload = {
        "skilllift_phase": "train_evolve",
        "tau2_split": "train",
        "cluster_id": cluster_id,
        "domain": domain,
        "task_ids": list(task_ids),
        "per_task_rankings": [ranking.to_dict() for ranking in batch_result.per_task_rankings],
        "aggregate_oracle_scores": {
            key.token(): score.to_dict()
            for key, score in batch_result.aggregate_oracle_scores.items()
        },
        "aggregate_oracle_rank": [key.token() for key in batch_result.aggregate_oracle_rank],
        "cluster_level_skill_bundles": {
            key.token(): _cluster_skill_bundle_metadata(
                skill=skill,
                source_train_run_id=source_train_run_id,
                source_model=config.model,
            )
            for key, skill in sorted(skills.items())
        },
        "cluster_level_skill_contents": {
            key.token(): {
                "files": {"SKILL.md": skill.files.get("SKILL.md", "")},
                "entrypoint": skill.entrypoint,
            }
            for key, skill in sorted(skills.items())
        },
    }
    _write_json(path, payload)
    return path


def _load_cluster_skills(cluster_artifact_paths: list[Path | str]) -> dict[str, dict[str, Any]]:
    skills: dict[str, dict[str, Any]] = {}
    for artifact_path in cluster_artifact_paths:
        payload = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
        cluster_id=str(payload.get("cluster_id") or "")
        metadata_by_key = payload.get("cluster_level_skill_bundles") or {}
        content_by_key = payload.get("cluster_level_skill_contents") or {}
        for skill_key, metadata in metadata_by_key.items():
            files = (content_by_key.get(skill_key) or {}).get("files") or {}
            skill_text = str(files.get("SKILL.md") or "")
            if not skill_text:
                raise ValueError(f"cluster artifact is missing SKILL.md content for {skill_key}")
            composite_key = f"{cluster_id}/{skill_key}" if cluster_id else str(skill_key)
            skills[composite_key] = {
                "skill_key": str(skill_key),
                "cluster_id": cluster_id,
                "domain": str(payload.get("domain") or ""),
                "skill_hash": str((metadata or {}).get("skill_hash") or ""),
                "prompt_hash": str((metadata or {}).get("prompt_hash") or ""),
                "content": skill_text,
            }
    if not skills:
        raise ValueError("MASTER_SKILL fusion requires at least one cluster skill")
    return skills


def _build_master_skill_fusion_prompt(
    *,
    domain: str,
    action_types: list[str],
    cluster_skills: dict[str, dict[str, Any]],
) -> str:
    cluster_skill_blocks = []
    for skill_key, skill in sorted(cluster_skills.items()):
        cluster_skill_blocks.append(
            "\n".join(
                [
                    f"### Source Skill: {skill_key}",
                    f"- cluster_id: {skill['cluster_id']}",
                    f"- skill_hash: {skill['skill_hash']}",
                    "```markdown",
                    skill["content"],
                    "```",
                ]
            )
        )
    return "\n".join(
        [
            "Role: You are a senior agent-skill architect and rule-fusion specialist.",
            "",
            "Task:",
            f'Merge the train-only cluster skills for domain "{domain}" into one clear, conflict-free MASTER_SKILL.md.',
            "",
            "Inputs:",
            f"- Required action types: {', '.join(action_types)}",
            "- Cluster skills:",
            "\n\n".join(cluster_skill_blocks),
            "",
            "Rules:",
            '1. Organize by action type and include an "Action Checkpoint Index".',
            "2. Deduplicate overlapping rules.",
            "3. If rules conflict, keep the safer rule: verify preconditions, avoid invented actions, and check results after actions.",
            "4. Do not add business rules not supported by the input skills or public policy.",
            "5. Do not use XML/HTML tags.",
            '6. If two safer rules cannot be reconciled, preserve both under a "## Disputed" heading with the source skill hash; do not silently drop either.',
            '7. If an action type in the required list is not covered by any input skill, emit "## Coverage Gap: <action_type>" with no fabricated rules.',
            "",
            "Output:",
            "Return only the MASTER_SKILL.md body wrapped in exactly five backticks:",
            "`````",
            "# MASTER_SKILL",
            "...",
            "`````",
        ]
    )


def _extract_master_skill(raw_output: str) -> str:
    marker = "`````"
    # LLMs often close a 5-backtick fence with fewer backticks (commonly ```).
    # Accept any 3+ backtick run as the closing fence to tolerate this.
    closing_markers = ("```", "````", "`````")
    lines = raw_output.splitlines()
    start = None
    end = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if start is None:
            if stripped == marker or (stripped.startswith(marker) and not stripped.startswith(marker + "`")):
                start = index
            continue
        if stripped in closing_markers and not stripped.startswith(marker + "`"):
            end = index
    if start is None or end is None:
        raise ValueError("fusion output must wrap MASTER_SKILL.md in fenced backticks (3-5)")
    if end <= start:
        raise ValueError("fusion output must wrap MASTER_SKILL.md in fenced backticks (3-5)")
    body = "\n".join(lines[start + 1:end]).strip()
    if not body:
        raise ValueError("fusion output MASTER_SKILL.md body is empty")
    return body


def _sanitize_noargs_tool_calls(response: Any, tools_schema: Any) -> Any:
    if not tools_schema:
        return response
    no_arg_tool_names = _no_arg_tool_names(tools_schema)
    if not no_arg_tool_names:
        return response
    for tool_call in _response_tool_calls(response):
        function = _get_value(tool_call, "function")
        if not function:
            continue
        name = _get_value(function, "name")
        if name not in no_arg_tool_names:
            continue
        arguments = _get_value(function, "arguments")
        if _is_noargs_only(arguments):
            _set_value(function, "arguments", "{}" if isinstance(arguments, str) else {})
    return response


def _accumulate_token_usage(response: Any) -> None:
    """Accumulate token usage from a litellm completion response into the
    current task's contextvar counter. No-op when no task is active.

    `response.usage` is a pydantic `Usage` with prompt_tokens /
    completion_tokens / total_tokens. Some providers omit it; missing or
    non-positive values flip the `incomplete` flag instead of failing.
    """
    counter = _tau2_token_usage.get()
    if counter is None:
        return
    usage = getattr(response, "usage", None)
    if usage is None:
        counter["incomplete"] = True
        return
    for field_name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = getattr(usage, field_name, None)
        if isinstance(value, (int, float)) and value > 0:
            counter[field_name] = counter.get(field_name, 0) + int(value)
        else:
            counter["incomplete"] = True


@contextmanager
def _patch_tau2_completion_for_noargs():
    _ensure_tau2_importable()
    import tau2.utils.llm_utils as llm_utils

    original_completion = llm_utils.completion

    def patched_completion(*args: Any, **kwargs: Any) -> Any:
        response = original_completion(*args, **kwargs)
        _accumulate_token_usage(response)
        return _sanitize_noargs_tool_calls(response, kwargs.get("tools"))

    llm_utils.completion = patched_completion
    try:
        yield
    finally:
        llm_utils.completion = original_completion


def _install_tau2_nl_assertion_llm_defaults(run_config: dict[str, Any]) -> None:
    _ensure_tau2_importable()
    import tau2.config as tau2_config
    import tau2.evaluator.evaluator_nl_assertions as nl_assertions

    model = str(run_config["llm_agent"])
    raw_args = dict(run_config.get("llm_args_agent") or {})
    llm_args = {
        key: value
        for key, value in raw_args.items()
        if key in TAU2_NL_ASSERTION_LLM_ARG_KEYS
    }
    tau2_config.DEFAULT_LLM_NL_ASSERTIONS = model
    tau2_config.DEFAULT_LLM_NL_ASSERTIONS_ARGS = dict(llm_args)
    nl_assertions.DEFAULT_LLM_NL_ASSERTIONS = model
    nl_assertions.DEFAULT_LLM_NL_ASSERTIONS_ARGS = dict(llm_args)


def _no_arg_tool_names(tools_schema: Any) -> set[str]:
    names: set[str] = set()
    for tool_schema in tools_schema or []:
        function = _get_value(tool_schema, "function")
        if not function:
            continue
        parameters = _get_value(function, "parameters") or {}
        properties = _get_value(parameters, "properties")
        if properties == {}:
            name = _get_value(function, "name")
            if name:
                names.add(str(name))
    return names


def _response_tool_calls(response: Any) -> list[Any]:
    choices = _get_value(response, "choices") or []
    if not choices:
        return []
    message = _get_value(choices[0], "message")
    if not message:
        return []
    return list(_get_value(message, "tool_calls") or [])


def _is_noargs_only(arguments: Any) -> bool:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return False
    return isinstance(arguments, dict) and set(arguments) == {"_noargs"}


def _get_value(container: Any, key: str) -> Any:
    if isinstance(container, dict):
        return container.get(key)
    return getattr(container, key, None)


def _set_value(container: Any, key: str, value: Any) -> None:
    if isinstance(container, dict):
        container[key] = value
    else:
        setattr(container, key, value)


def _validate_master_skill(master_skill: str) -> None:
    allowed_placeholders_removed = master_skill.replace("<action_type>", "")
    if re.search(r"</?[A-Za-z][A-Za-z0-9:_-]*(?:\s[^<>]*)?>", allowed_placeholders_removed):
        raise ValueError("MASTER_SKILL.md must not contain XML/HTML tags")


def _write_master_skill_fusion_artifact(
    *,
    config: SkillLiftTau2TrainConfig,
    domain: str,
    action_types: list[str],
    cluster_artifact_paths: list[Path],
    cluster_skills: dict[str, dict[str, Any]],
    prompt: str,
    fusion_model: str,
    master_skill: str,
    repair_metrics: dict[str, int],
) -> SkillLiftTau2FusionResult:
    artifact_dir = config.run_root / "master_skill_fusion" / domain
    artifact_dir.mkdir(parents=True, exist_ok=True)
    master_skill_path = artifact_dir / "MASTER_SKILL.md"
    master_skill_content = master_skill.rstrip() + "\n"
    master_skill_path.write_text(master_skill_content, encoding="utf-8")

    output_skill_hash = stable_hash({"SKILL.md": master_skill_content})
    bundle = SkillBundle(
        skill_bundle_id=f"{domain}_MASTER_SKILL",
        baseline="skilllift",
        benchmark_target="tau2",
        granularity="domain",
        domain=domain,
        source_round="train_evolve_fusion",
        source_artifact_path=str(artifact_dir.resolve()),
        skills=[
            {
                "id": "MASTER_SKILL",
                "title": "MASTER_SKILL",
                "content": master_skill_content,
                "metadata": {"fusion_prompt_template_id": FUSION_PROMPT_TEMPLATE_ID},
            }
        ],
        created_at="1970-01-01T00:00:00Z",
        skill_hash=output_skill_hash,
    )
    best_skill_bundle_path = artifact_dir / "best_skill_bundle.json"
    _write_json(best_skill_bundle_path, bundle.to_dict())

    input_skill_hashes = {
        skill_key: skill["skill_hash"]
        for skill_key, skill in sorted(cluster_skills.items())
    }
    coverage = _action_checkpoint_coverage(action_types, master_skill)
    d12_metrics = _d12_repair_metrics(repair_metrics)
    artifact_path = artifact_dir / "fusion_artifact.json"
    payload = {
        "skilllift_phase": "train_evolve",
        "tau2_split": "train",
        "domain": domain,
        "fusion_model": fusion_model,
        "fusion_prompt_template_id": FUSION_PROMPT_TEMPLATE_ID,
        "fusion_prompt_hash": stable_hash(
            {"template_id": FUSION_PROMPT_TEMPLATE_ID, "prompt": prompt}
        ),
        "input_skill_hashes": input_skill_hashes,
        "output_skill_hash": output_skill_hash,
        "action_checkpoint_coverage": coverage,
        "d12_repair_metrics": d12_metrics,
        "cluster_artifact_paths": [str(path.resolve()) for path in cluster_artifact_paths],
        "master_skill_path": str(master_skill_path.resolve()),
        "best_skill_bundle_path": str(best_skill_bundle_path.resolve()),
    }
    _write_json(artifact_path, payload)
    return SkillLiftTau2FusionResult(
        artifact_path=artifact_path,
        master_skill_path=master_skill_path,
        best_skill_bundle_path=best_skill_bundle_path,
    )


def _write_failed_fusion_artifact(
    *,
    config: SkillLiftTau2TrainConfig,
    domain: str,
    prompt: str,
    fusion_model: str,
    raw_output: str,
) -> None:
    artifact_dir = config.run_root / "master_skill_fusion" / domain
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "fusion_prompt.txt").write_text(prompt, encoding="utf-8")
    (artifact_dir / "fusion_raw_output.txt").write_text(str(raw_output), encoding="utf-8")
    _write_json(
        artifact_dir / "fusion_failure_artifact.json",
        {
            "skilllift_phase": "train_evolve",
            "tau2_split": "train",
            "domain": domain,
            "fusion_model": fusion_model,
            "fusion_prompt_template_id": FUSION_PROMPT_TEMPLATE_ID,
            "fusion_prompt_hash": stable_hash(
                {"template_id": FUSION_PROMPT_TEMPLATE_ID, "prompt": prompt}
            ),
            "raw_output_path": str((artifact_dir / "fusion_raw_output.txt").resolve()),
            "failure": "fusion_output_parse_error",
        },
    )


def _action_checkpoint_coverage(action_types: list[str], master_skill: str) -> dict[str, list[str]]:
    lowered = master_skill.lower()
    gap_actions = _coverage_gap_actions(master_skill)
    covered = sorted(
        action
        for action in action_types
        if action.lower() in lowered and action.lower() not in gap_actions
    )
    uncovered = sorted(action for action in action_types if action not in covered)
    return {"covered": covered, "uncovered": uncovered}


def _coverage_gap_actions(master_skill: str) -> set[str]:
    prefix = "## coverage gap:"
    gap_actions: set[str] = set()
    for line in master_skill.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith(prefix):
            gap_action = stripped[len(prefix):].strip()
            if gap_action:
                gap_actions.add(gap_action)
    return gap_actions


def _d12_repair_metrics(repair_metrics: dict[str, int]) -> dict[str, float | int]:
    parse_error_count = int(repair_metrics.get("parse_error_count", 0))
    total_generation_attempts = int(repair_metrics.get("total_generation_attempts", 0))
    repair_trigger_rate = (
        parse_error_count / total_generation_attempts
        if total_generation_attempts
        else 0.0
    )
    return {
        "parse_error_count": parse_error_count,
        "total_generation_attempts": total_generation_attempts,
        "repair_trigger_rate": repair_trigger_rate,
    }


def _load_master_skill_bundle(source_fusion_artifact_path: Path | str) -> tuple[SkillBundle, Path, Path, dict[str, Any]]:
    fusion_artifact_path = Path(source_fusion_artifact_path)
    fusion_artifact = json.loads(fusion_artifact_path.read_text(encoding="utf-8"))
    master_skill_path = Path(str(fusion_artifact.get("master_skill_path") or ""))
    best_skill_bundle_path = Path(str(fusion_artifact.get("best_skill_bundle_path") or ""))
    if not master_skill_path.is_file() or not best_skill_bundle_path.is_file():
        raise ValueError("held-out test_eval requires MASTER_SKILL.md and best_skill_bundle paths")

    master_skill_content = master_skill_path.read_text(encoding="utf-8")
    bundle = SkillBundle.from_dict(json.loads(best_skill_bundle_path.read_text(encoding="utf-8")))
    if bundle.granularity != "domain":
        raise ValueError("held-out test_eval requires a domain-level MASTER_SKILL bundle")
    if len(bundle.skills) != 1:
        raise ValueError("held-out test_eval requires exactly one MASTER_SKILL")
    skill = bundle.skills[0]
    if str(skill.get("id") or "") != "MASTER_SKILL":
        raise ValueError("held-out test_eval requires MASTER_SKILL as the only skill")
    if str(skill.get("content") or "") != master_skill_content:
        raise ValueError("MASTER_SKILL.md and best_skill_bundle content disagree")
    master_skill_hash = stable_hash({"SKILL.md": master_skill_content})
    if fusion_artifact.get("output_skill_hash") and fusion_artifact["output_skill_hash"] != master_skill_hash:
        raise ValueError("MASTER_SKILL hash does not match fusion artifact output_skill_hash")
    if bundle.skill_hash != master_skill_hash:
        raise ValueError("MASTER_SKILL hash does not match best_skill_bundle skill_hash")
    return bundle, master_skill_path, best_skill_bundle_path, fusion_artifact


def _tau2_test_eval_prompt_context(
    *,
    task: Any,
    domain: str,
    bundle: SkillBundle,
    config: SkillLiftTau2TrainConfig,
    cluster_id: str = "",
) -> dict[str, Any]:
    task_id = _task_id(task)
    domain_policy = config.domain_policy_by_domain.get(domain, "")
    if not domain_policy:
        raise ValueError(f"missing public domain policy for tau2 domain: {domain}")
    context = Tau2SkillLoader(
        artifact_root=config.run_root / "tau2_prompt_context" / "test_eval" / domain / task_id
    ).prepare(
        bundle=bundle,
        domain=domain,
        task_ids=[task_id],
        domain_policy=domain_policy,
    )
    return {
        "domain": domain,
        "task_split_name": "test",
        "agent": "skill_injected",
        "user": "user_simulator",
        "llm_agent": config.model,
        "llm_args_agent": dict(config.tau2_llm_args),
        "llm_user": config.model,
        "llm_args_user": dict(config.tau2_llm_args),
        "max_steps": config.max_steps,
        "seed": config.seed,
        "skilllift_phase": "test_eval",
        "skilllift_skill_key": "MASTER_SKILL",
        "skilllift_cluster_id": cluster_id or task_id,
        "run_root": str(config.run_root),
        "save_trajectory": config.save_trajectory,
        "master_skill_hash": bundle.skill_hash,
        "tau2_prompt_context": context.to_dict(),
        "task_id": task_id,
    }


def _test_eval_score(
    *,
    task_id: str,
    simulation: Any,
    threshold: float,
) -> dict[str, Any]:
    reward = _reward(simulation)
    passed = 1 if reward >= threshold else 0
    return {
        "task_id": task_id,
        "reward": reward,
        "pass": passed,
        "summary": f"reward={reward:.3f} pass={passed}",
    }


def _write_heldout_test_eval_artifact(
    *,
    config: SkillLiftTau2TrainConfig,
    eval_id: str,
    cluster: Tau2TrainCluster,
    tasks: list[Any],
    per_task_scores: list[dict[str, Any]],
    prompt_hashes: dict[str, str],
    master_bundle: SkillBundle,
    master_skill_path: Path,
    best_skill_bundle_path: Path,
    source_fusion_artifact_path: Path,
    fusion_artifact: dict[str, Any],
    no_skill_test_score: float | None,
) -> Path:
    artifact_dir = config.run_root / "test_eval" / cluster.domain / cluster.cluster_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / "heldout_test_score.json"
    rewards = [float(score["reward"]) for score in per_task_scores]
    heldout_test_score = sum(rewards) / len(rewards) if rewards else 0.0
    score_delta = (
        heldout_test_score - no_skill_test_score
        if no_skill_test_score is not None
        else None
    )
    task_ids = [_task_id(task) for task in tasks]
    payload = {
        "skilllift_phase": "test_eval",
        "tau2_split": "test",
        "train_split": "train",
        "eval_split": "test",
        "split_role": "heldout_test",
        "eval_id": eval_id,
        "domain": cluster.domain,
        "cluster_id": cluster.cluster_id,
        "task_ids": task_ids,
        "per_task_scores": per_task_scores,
        "heldout_test_score": heldout_test_score,
        "skilllift_loaded_skill_test_score": heldout_test_score,
        "no_skill_test_score": no_skill_test_score,
        "heldout_test_score_delta": score_delta,
        "master_skill_path": str(master_skill_path.resolve()),
        "master_skill_hash": master_bundle.skill_hash,
        "best_skill_bundle_path": str(best_skill_bundle_path.resolve()),
        "source_fusion_artifact_path": str(source_fusion_artifact_path.resolve()),
        "skill_artifact_source_run_id": str(fusion_artifact.get("source_train_run_id") or ""),
        "skill_artifact_hash": master_bundle.skill_hash,
        "fusion_prompt_hash": str(fusion_artifact.get("fusion_prompt_hash") or ""),
        "prompt_hashes": prompt_hashes,
        "task_set_hash": stable_hash(
            {
                "domain": cluster.domain,
                "split": "test",
                "phase": "test_eval",
                "manifest_version": "v2.4",
                "cluster_id": cluster.cluster_id,
                "task_ids": task_ids,
            }
        ),
        "loaded_skill_artifact": {
            "files": ["SKILL.md"],
            "entrypoint": None,
            "has_python": False,
            "granularity": master_bundle.granularity,
        },
    }
    _write_json(artifact_path, payload)
    return artifact_path


def _test_eval_task_score_path(
    config: SkillLiftTau2TrainConfig,
    cluster: Tau2TrainCluster,
    task_id: str,
) -> Path:
    return (
        config.run_root
        / "test_eval"
        / cluster.domain
        / cluster.cluster_id
        / "task_scores"
        / f"{task_id}.json"
    )


def _write_train_evolve_smoke_artifact(
    *,
    config: SkillLiftTau2TrainConfig,
    smoke_id: str,
    cluster: Tau2TrainCluster,
    skills: dict[SkillKey, EvoSkill],
    batch_result: Tau2SkillBatchResult,
    cluster_artifact_path: Path,
) -> Path:
    artifact_dir = config.run_root / "train_evolve_smoke" / cluster.domain / cluster.cluster_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / "smoke_artifact.json"
    rubricator_input = {
        "oracle_scores": {
            key.token(): score.to_dict()
            for key, score in batch_result.aggregate_oracle_scores.items()
        },
        "oracle_rank": [key.token() for key in batch_result.aggregate_oracle_rank],
        "per_task_rankings": [
            ranking.to_dict()
            for ranking in batch_result.per_task_rankings
        ],
    }
    actual_task_count = len(batch_result.per_task_rankings)
    oracle_calls_used = len(batch_result.task_skill_scores)
    payload = {
        "skilllift_phase": "train_evolve",
        "tau2_split": "train",
        "smoke_scope": smoke_id,
        "cluster_id": cluster.cluster_id,
        "domain": cluster.domain,
        "task_ids": list(cluster.task_ids),
        "actual_task_count": actual_task_count,
        "actual_skill_count": len(skills),
        "oracle_scores": rubricator_input["oracle_scores"],
        "oracle_rank": rubricator_input["oracle_rank"],
        "per_task_rankings": rubricator_input["per_task_rankings"],
        "rubricator_input": rubricator_input,
        "round_metrics": {
            "round_id": "outer_000_mode_b",
            "round_type": "train_evolve_smoke",
            "oracle_calls_used": oracle_calls_used,
            "repair_parse_errors": 0,
            "artifact_paths": {
                "cluster_train_artifact": str(cluster_artifact_path.resolve()),
            },
        },
        "skill_artifacts": {
            key.token(): {
                "files": sorted(skill.files),
                "entrypoint": skill.entrypoint,
                "skill_hash": stable_hash({"SKILL.md": skill.files.get("SKILL.md", "")}),
                "prompt_hash": skill.metadata.get("prompt_hash", ""),
                "source_model": skill.metadata.get("source_model", ""),
            }
            for key, skill in sorted(skills.items())
        },
    }
    _write_json(artifact_path, payload)
    return artifact_path


def _build_per_task_rankings(
    cluster_tasks: list[Any],
    skills: dict[SkillKey, EvoSkill],
    task_skill_scores: dict[tuple[str, SkillKey], OracleScore],
    *,
    domain: str | None = None,
) -> list[TaskRanking]:
    rankings: list[TaskRanking] = []
    for task in cluster_tasks:
        task_id = _task_id(task)
        scores = {
            key: task_skill_scores[(task_id, key)]
            for key in sorted(skills)
        }
        score_pairs = {
            (score.oracle_score, score.oracle_pass)
            for score in scores.values()
        }
        has_signal = len(score_pairs) > 1
        rankings.append(
            TaskRanking(
                task_id=task_id,
                domain=domain or _task_domain(task),
                oracle_rank=rank_oracle_scores(scores) if has_signal else [],
                skill_scores=scores,
                has_signal=has_signal,
                no_signal_reason=None if has_signal else "all skills tied on oracle_score/oracle_pass",
            )
        )
    return rankings


def _aggregate_oracle_scores(
    skills: dict[SkillKey, EvoSkill],
    task_skill_scores: dict[tuple[str, SkillKey], OracleScore],
    task_skill_behavior_summaries: dict[tuple[str, SkillKey], dict[str, Any]] | None = None,
) -> dict[SkillKey, OracleScore]:
    aggregate: dict[SkillKey, OracleScore] = {}
    for key in sorted(skills):
        scores = [
            score
            for (task_id, skill_key), score in task_skill_scores.items()
            if skill_key == key
        ]
        mean_score = sum(score.oracle_score for score in scores) / len(scores) if scores else 0.0
        mean_pass = sum(score.oracle_pass for score in scores) / len(scores) if scores else 0.0
        metadata: dict[str, Any] = {
            "aggregation": "per_skill_mean",
            "task_count": len(scores),
            "pass_aggregation": "majority_ge_0.5",
        }
        if task_skill_behavior_summaries:
            behavior_summary = _aggregate_execution_behavior_summaries(
                [
                    summary
                    for (_task_id, skill_key), summary in task_skill_behavior_summaries.items()
                    if skill_key == key
                ]
            )
            if behavior_summary:
                metadata["execution_behavior_summary"] = behavior_summary
        aggregate[key] = OracleScore(
            key,
            mean_score,
            1 if mean_pass >= 0.5 else 0,
            feedback=OracleFeedback(
                summary=f"mean_reward={mean_score:.3f} across {len(scores)} tau2 train tasks",
                metadata=metadata,
            ),
        )
    return aggregate


def _tau2_prompt_context(
    *,
    task: Any,
    domain: str,
    skill: EvoSkill,
    config: SkillLiftTau2TrainConfig,
    cluster_id: str = "",
) -> dict[str, Any]:
    task_id = _task_id(task)
    prompt_context = _build_tau2_prompt_context(
        task_id=task_id,
        domain=domain,
        task_skill=skill,
        domain_policy=config.domain_policy_by_domain.get(domain, ""),
        run_root=config.run_root,
    )
    resolved_cluster_id = cluster_id or task_id
    return {
        "domain": domain,
        "task_split_name": "train",
        "agent": "skill_injected",
        "user": "user_simulator",
        "llm_agent": config.model,
        "llm_args_agent": dict(config.tau2_llm_args),
        "llm_user": config.model,
        "llm_args_user": dict(config.tau2_llm_args),
        "max_steps": config.max_steps,
        "seed": config.seed,
        "skilllift_phase": "train_evolve",
        "skilllift_skill_key": skill.key.token(),
        "skilllift_cluster_id": resolved_cluster_id,
        "run_root": str(config.run_root),
        "save_trajectory": config.save_trajectory,
        "tau2_prompt_context": prompt_context,
        "task_id": task_id,
    }


def _build_tau2_prompt_context(
    *,
    task_id: str,
    domain: str,
    task_skill: EvoSkill,
    domain_policy: str,
    run_root: Path,
) -> dict[str, Any]:
    if not domain_policy:
        raise ValueError(f"missing public domain policy for tau2 domain: {domain}")
    bundle = SkillBundle(
        skill_bundle_id=str(task_id),
        baseline="skilllift",
        benchmark_target="tau2",
        granularity="domain",
        domain=domain,
        cluster_id=task_id,
        source_round="train_evolve",
        source_artifact_path=str(run_root / "train_evolve" / domain / task_id),
        skills=[
            {
                "id": task_skill.key.token(),
                "title": task_skill.skill_name,
                "content": task_skill.files.get("SKILL.md", ""),
                "metadata": {},
            }
        ],
        created_at="1970-01-01T00:00:00Z",
    )
    context = Tau2SkillLoader(artifact_root=run_root / "tau2_prompt_context" / domain / task_id / task_skill.key.token()).prepare(
        bundle=bundle,
        domain=domain,
        task_ids=[task_id],
        domain_policy=domain_policy,
    )
    return context.to_dict()


def _oracle_score_from_simulation(
    *,
    skill_key: SkillKey,
    task_id: str,
    simulation: Any,
    threshold: float,
) -> OracleScore:
    score = _reward(simulation)
    passed = 1 if score >= threshold else 0
    return OracleScore(
        skill_key,
        score,
        passed,
        feedback=OracleFeedback(
            summary=f"reward={score:.3f} pass={passed}",
            metadata={
                "task_id": task_id,
                "feedback_source": "tau2_structured_reward",
            },
        ),
    )


def _public_execution_behavior_summary(simulation: Any) -> dict[str, Any]:
    events = _public_behavior_events(simulation)
    counts = _behavior_counts(events)
    termination_reason = _termination_reason(simulation)
    return {
        "termination_reason": termination_reason,
        "counts": counts,
        "patterns": _behavior_patterns(counts),
    }


def _aggregate_execution_behavior_summaries(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    if not summaries:
        return {}
    termination_counts: dict[str, int] = {}
    totals = {
        "observe": 0,
        "agent_action": 0,
        "external_action": 0,
        "retry_or_recheck": 0,
        "stall_after_action": 0,
    }
    for summary in summaries:
        reason = str(summary.get("termination_reason") or "unknown")
        termination_counts[reason] = termination_counts.get(reason, 0) + 1
        counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
        for key in totals:
            totals[key] += int(counts.get(key) or 0)
    task_count = len(summaries)
    averages = {key: round(value / task_count, 2) for key, value in totals.items()}
    return {
        "task_count": task_count,
        "termination_counts": termination_counts,
        "average_counts": averages,
        "patterns": _behavior_patterns(averages),
    }


def _public_behavior_events(simulation: Any) -> list[str]:
    events: list[str] = []
    for message in _simulation_messages(simulation):
        tool_calls = _message_tool_calls(message)
        if tool_calls:
            events.extend(_tool_behavior_event(tool_call) for tool_call in tool_calls)
            continue
        if _message_mentions_retry_or_recheck(message):
            events.append("retry_or_recheck")
    return events


def _behavior_counts(events: list[str]) -> dict[str, int]:
    counts = {
        "observe": events.count("observe"),
        "agent_action": events.count("agent_action"),
        "external_action": events.count("external_action"),
        "retry_or_recheck": events.count("retry_or_recheck"),
        "stall_after_action": 0,
    }
    action_indexes = [
        index
        for index, event in enumerate(events)
        if event in {"agent_action", "external_action"}
    ]
    if action_indexes and "retry_or_recheck" in events[action_indexes[-1] + 1 :]:
        counts["stall_after_action"] = 1
    return counts


def _behavior_patterns(counts: dict[str, float | int]) -> list[str]:
    patterns: list[str] = []
    actions = float(counts.get("agent_action", 0) or 0) + float(counts.get("external_action", 0) or 0)
    retries = float(counts.get("retry_or_recheck", 0) or 0)
    observations = float(counts.get("observe", 0) or 0)
    if actions <= 0 and (observations > 0 or retries > 0):
        patterns.append("observation_or_retry_without_observable_action")
    if float(counts.get("stall_after_action", 0) or 0) > 0:
        patterns.append("retry_or_recheck_after_final_observable_action")
    if actions > 0 and retries <= actions:
        patterns.append("observable_actions_progressed")
    return patterns


def _simulation_messages(simulation: Any) -> list[Any]:
    if hasattr(simulation, "get_messages"):
        return list(simulation.get_messages())
    if isinstance(simulation, dict):
        return list(simulation.get("messages") or [])
    return list(getattr(simulation, "messages", []) or [])


def _message_tool_calls(message: Any) -> list[Any]:
    if isinstance(message, dict):
        return list(message.get("tool_calls") or [])
    return list(getattr(message, "tool_calls", None) or [])


def _tool_behavior_event(tool_call: Any) -> str:
    name = _tool_call_name(tool_call).lower()
    requestor = _tool_call_requestor(tool_call)
    if name.startswith(("get_", "check_", "list_", "search_", "lookup_", "find_", "can_")):
        return "observe"
    if requestor == "user":
        return "external_action"
    return "agent_action"


def _tool_call_name(tool_call: Any) -> str:
    if isinstance(tool_call, dict):
        function = tool_call.get("function")
        if isinstance(function, dict):
            return str(function.get("name") or "")
        return str(tool_call.get("name") or "")
    function = getattr(tool_call, "function", None)
    if function is not None:
        return str(getattr(function, "name", "") or "")
    return str(getattr(tool_call, "name", "") or "")


def _tool_call_requestor(tool_call: Any) -> str:
    if isinstance(tool_call, dict):
        return str(tool_call.get("requestor") or "assistant")
    return str(getattr(tool_call, "requestor", "assistant") or "assistant")


def _message_mentions_retry_or_recheck(message: Any) -> bool:
    if isinstance(message, dict):
        content = str(message.get("content") or "")
    else:
        content = str(getattr(message, "content", "") or "")
    text = content.lower()
    return any(
        phrase in text
        for phrase in (
            "try again",
            "retry",
            "recheck",
            "check again",
            "test again",
            "send again",
            "please try",
        )
    )


def _termination_reason(simulation: Any) -> str:
    if isinstance(simulation, dict):
        return str(simulation.get("termination_reason") or "unknown")
    return str(getattr(simulation, "termination_reason", None) or "unknown")


def _cluster_skill_bundle_metadata(
    *,
    skill: EvoSkill,
    source_train_run_id: str,
    source_model: str,
) -> dict[str, Any]:
    skill_hash = stable_hash({"SKILL.md": skill.files.get("SKILL.md", "")})
    return {
        "skill_hash": skill_hash,
        "source_train_run_id": source_train_run_id,
        "source_split": "train",
        "source_model": source_model,
        "prompt_hash": skill.metadata.get("prompt_hash", ""),
        "files": ["SKILL.md"],
    }


def _reward(simulation: Any) -> float:
    reward_info = getattr(simulation, "reward_info", None)
    if isinstance(reward_info, dict):
        return float(reward_info.get("reward") or 0.0)
    if hasattr(reward_info, "model_dump"):
        dumped = reward_info.model_dump(mode="json")
        return float(dumped.get("reward") or 0.0)
    return float(getattr(reward_info, "reward", 0.0) or 0.0)


def _task_id(task: Any) -> str:
    if isinstance(task, dict):
        return str(task.get("id") or task.get("task_id") or "")
    return str(getattr(task, "id", getattr(task, "task_id", "")))


def _task_domain(task: Any) -> str:
    if isinstance(task, dict):
        return str(task.get("domain") or "")
    return str(getattr(task, "domain", ""))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _simulation_to_trajectory_dict(simulation: Any) -> dict[str, Any]:
    """Extract trajectory-relevant fields from a SimulationRun.

    Uses pydantic's model_dump when available so nested Message / ToolCall /
    RewardInfo objects are serialized with their full structure. Falls back to
    a dict-like copy for non-pydantic test doubles.
    """
    dumped: dict[str, Any]
    if hasattr(simulation, "model_dump"):
        try:
            dumped = simulation.model_dump(mode="json")
        except TypeError:
            dumped = simulation.model_dump()
    elif isinstance(simulation, dict):
        dumped = dict(simulation)
    else:
        dumped = {k: getattr(simulation, k, None) for k in (
            "id", "task_id", "timestamp", "termination_reason", "duration",
            "reward_info", "messages", "info", "agent_cost", "provider_session_id",
        )}
    keep = (
        "id", "timestamp", "termination_reason", "duration",
        "reward_info", "messages", "info", "agent_cost", "provider_session_id",
    )
    out = {k: dumped.get(k) for k in keep}
    if "id" in out:
        out["simulation_id"] = out.pop("id")
    reward_info = dumped.get("reward_info") or {}
    if isinstance(reward_info, dict):
        out["reward"] = float(reward_info.get("reward") or 0.0)
    else:
        out["reward"] = _reward(simulation)
    return out


def _save_trajectory(
    *,
    simulation: Any,
    phase: str,
    domain: str,
    cluster_id: str,
    task_id: str,
    skill_key: str,
    seed: int,
    run_root: Any,
    token_usage: dict[str, Any],
) -> None:
    """Persist a single task's trajectory + token usage to disk.

    Failures are logged to stderr and swallowed; trajectory dumping is a
    read-only side effect and must never break the main pipeline.
    """
    try:
        payload = {
            "schema_version": "tau2_trajectory_v1",
            "skilllift_phase": phase,
            "domain": domain,
            "cluster_id": cluster_id,
            "task_id": task_id,
            "skill_key": skill_key,
            "seed": seed,
            **_simulation_to_trajectory_dict(simulation),
            "token_usage": dict(token_usage),
        }
        run_root_path = Path(run_root) if run_root is not None else None
        if run_root_path is None:
            return
        out_dir = run_root_path / "tau2_trajectories" / phase / domain / cluster_id
        safe_skill = re.sub(r"[^A-Za-z0-9_.-]+", "_", skill_key).strip("._-") or "skill"
        path = out_dir / f"task_{task_id}__{safe_skill}.json"
        _write_json(path, payload)
    except Exception as exc:
        print(
            f"[skilllift_tau2] WARN trajectory save failed "
            f"task={task_id} skill={skill_key}: {type(exc).__name__}: {exc}",
            flush=True,
            file=sys.stderr,
        )


def _default_tau2_run_single_task(run_config: dict[str, Any], task: Any, seed: int) -> Any:
    _ensure_tau2_importable()
    from tau2.data_model.simulation import TextRunConfig
    from tau2.run import run_single_task

    config = TextRunConfig(
        domain=run_config["domain"],
        task_split_name=run_config["task_split_name"],
        agent=run_config["agent"],
        user=run_config["user"],
        llm_agent=run_config["llm_agent"],
        llm_user=run_config["llm_user"],
        llm_args_agent=run_config["llm_args_agent"],
        llm_args_user=run_config["llm_args_user"],
        max_steps=run_config["max_steps"],
        seed=run_config["seed"],
        tau2_prompt_context=run_config["tau2_prompt_context"],
    )
    _install_tau2_nl_assertion_llm_defaults(run_config)

    token_usage: dict[str, Any] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "incomplete": False,
    }
    token_token = _tau2_token_usage.set(token_usage)
    try:
        with _patch_tau2_completion_for_noargs():
            simulation = run_single_task(config, task, seed=seed)
    finally:
        _tau2_token_usage.reset(token_token)

    if run_config.get("save_trajectory", True):
        _save_trajectory(
            simulation=simulation,
            phase=run_config.get("skilllift_phase", ""),
            domain=run_config.get("domain", ""),
            cluster_id=run_config.get("skilllift_cluster_id", ""),
            task_id=run_config.get("task_id", ""),
            skill_key=run_config.get("skilllift_skill_key", ""),
            seed=seed,
            run_root=run_config.get("run_root"),
            token_usage=token_usage,
        )
    return simulation


def _ensure_tau2_importable() -> None:
    src = str(Path(__file__).resolve().parents[2] / "tau2-bench" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
