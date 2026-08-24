from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Literal
import copy
import hashlib
import json


def canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_hash(data: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def _clean_dict(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if v is not None}


@dataclass(frozen=True)
class MatrixCellKey:
    evaluation_mode: Literal["native_end_to_end", "budget_matched"]
    baseline: str
    benchmark: str
    model_label: str

    @property
    def matrix_cell_id(self) -> str:
        return "__".join(
            [
                self.evaluation_mode,
                self.baseline,
                self.benchmark,
                self.model_label.replace(".", "_"),
            ]
        )


@dataclass
class SkillBundle:
    skill_bundle_id: str
    baseline: str
    benchmark_target: str
    granularity: str
    skills: list[dict[str, Any]]
    created_at: str
    domain: str | None = None
    cluster_id: str | None = None
    source_round: str | None = None
    source_artifact_path: str | None = None
    skill_hash: str | None = None

    def __post_init__(self) -> None:
        if self.skill_hash is None:
            self.skill_hash = stable_hash({"skills": self.skills})

    def to_dict(self) -> dict[str, Any]:
        return _clean_dict(asdict(self))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillBundle":
        return cls(**data)


@dataclass
class LoadedSkillContext:
    kind: Literal["wildclaw_file_context", "tau2_prompt_context", "empty_context"]
    benchmark: str
    loader_type: str
    skill_hash: str
    manifest_path: str
    skill_bundle_id: str | None = None
    original_task_path: str | None = None
    generated_task_path: str | None = None
    host_skills_parent_dir: str | None = None
    loaded_skill_names: list[str] = field(default_factory=list)
    runtime_skill_dirs: list[str] = field(default_factory=list)
    container_skill_dir: str | None = None
    loaded_files: list[str] = field(default_factory=list)
    copy_or_mount_mode: str | None = None
    bundle_path: str | None = None
    prompt_template_id: str | None = None
    prompt_payload_path: str | None = None
    prompt_hash: str | None = None
    skills_block_hash: str | None = None
    domain: str | None = None
    granularity: str | None = None
    batch_size_k: int | None = None
    batch_task_ids: list[str] = field(default_factory=list)
    extra_run_args: list[str] = field(default_factory=list)
    extra_env: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LoadedSkillContext":
        ctx = cls(**data)
        if ctx.kind == "wildclaw_file_context" and ctx.benchmark != "wildclawbench":
            raise ValueError("wildclaw_file_context requires benchmark=wildclawbench")
        if ctx.kind == "tau2_prompt_context" and ctx.benchmark != "tau2":
            raise ValueError("tau2_prompt_context requires benchmark=tau2")
        if ctx.kind == "empty_context" and ctx.loader_type != "empty":
            raise ValueError("empty_context requires loader_type=empty")
        return ctx

    def validate_for_runner(self, benchmark: str) -> None:
        expected = {
            "wildclawbench": "wildclaw_file_context",
            "tau2": "tau2_prompt_context",
        }.get(benchmark)
        if self.kind == "empty_context":
            return
        if expected is None or self.kind != expected:
            raise ValueError(f"{self.kind} cannot be consumed by {benchmark} runner")

    def to_dict(self) -> dict[str, Any]:
        return _clean_dict(asdict(self))


@dataclass
class AlgorithmParamProfile:
    baseline: str
    profile_id: str
    paper_reference: Any
    paper_default_params: dict[str, Any]
    resolved_params: dict[str, Any]
    source_notes: list[dict[str, Any]] = field(default_factory=list)
    repo_default_params: dict[str, Any] = field(default_factory=dict)
    override_diff: dict[str, dict[str, Any]] = field(default_factory=dict)
    param_hash: str | None = None
    created_at: str | None = None

    def __post_init__(self) -> None:
        if not self.resolved_params:
            self.resolved_params = copy.deepcopy(self.paper_default_params)
        else:
            missing_from_resolved = [key for key in self.paper_default_params if key not in self.resolved_params]
            if missing_from_resolved and not self.override_diff:
                for key in missing_from_resolved:
                    self.resolved_params[key] = copy.deepcopy(self.paper_default_params[key])
        if not self.override_diff:
            self.override_diff = diff_params(self.paper_default_params, self.resolved_params)
        if self.param_hash is None or self.param_hash.startswith("pending") or self.param_hash.startswith("unresolved"):
            self.param_hash = stable_hash(
                {
                    "baseline": self.baseline,
                    "profile_id": self.profile_id,
                    "resolved_params": self.resolved_params,
                    "override_diff": self.override_diff,
                }
            )

    def with_overrides(self, overrides: dict[str, Any]) -> "AlgorithmParamProfile":
        resolved = copy.deepcopy(self.resolved_params)
        resolved.update(overrides)
        return AlgorithmParamProfile(
            baseline=self.baseline,
            profile_id=self.profile_id,
            paper_reference=self.paper_reference,
            paper_default_params=self.paper_default_params,
            resolved_params=resolved,
            source_notes=self.source_notes,
            repo_default_params=self.repo_default_params,
            created_at=self.created_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return _clean_dict(asdict(self))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AlgorithmParamProfile":
        allowed = {f.name for f in fields(cls)}
        normalized = {k: v for k, v in data.items() if k in allowed}
        if "paper_reference" not in normalized:
            normalized["paper_reference"] = data.get("paper_reference") or data.get("source") or "unknown"
        if "repo_default_params" not in normalized:
            normalized["repo_default_params"] = data.get("repo_default_params_observed", {})
        if normalized.get("override_diff") in (None, "{}") or not isinstance(normalized.get("override_diff", {}), dict):
            normalized["override_diff"] = {}
        return cls(**normalized)


def diff_params(base: dict[str, Any], resolved: dict[str, Any]) -> dict[str, dict[str, Any]]:
    diff: dict[str, dict[str, Any]] = {}
    for key in sorted(set(base) | set(resolved)):
        before = base.get(key)
        after = resolved.get(key)
        if before != after:
            diff[key] = {"from": before, "to": after}
    return diff


@dataclass
class ContextBudgetProfile:
    profile_id: str
    evaluation_mode: str
    visible_feedback_policy_id: str
    oracle_call_budget: str
    task_sample_policy_id: str
    round_budget: str
    token_budget: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskRunRecord:
    run_id: str
    matrix_cell_id: str
    baseline: str
    benchmark: str
    model_label: str
    model_endpoint_id: str
    provider_model_id: str
    endpoint_config_hash: str
    algorithm_param_hash: str
    benchmark_source_hash: str
    task_set_hash: str
    loader_version: str
    loader_manifest_schema_version: str
    score_parser_version: str
    run_config_hash: str
    task_id: str
    skill_hash: str
    prompt_hash: str | None
    loader_manifest_path: str
    raw_output_path: str
    score_path: str
    status: str
    evaluation_mode: str
    context_budget_profile: str
    visible_feedback_policy_id: str
    oracle_call_budget: str
    task_sample_policy_id: str
    round_budget: str
    token_budget: str
    domain: str | None = None
    batch_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    attempt: int = 1
    error_summary: str | None = None
    token_estimate: int | None = None
    token_actual: int | str = "unavailable"
    cost_actual: float | str = "unavailable"
    oracle_calls_used: int = 0
    context_input_chars: int = 0
    context_input_tokens_estimate: int = 0
    context_truncated: bool = False

    @staticmethod
    def resume_consistency_fields() -> list[str]:
        return [
            "provider_model_id",
            "endpoint_config_hash",
            "algorithm_param_hash",
            "benchmark_source_hash",
            "task_set_hash",
            "loader_version",
            "loader_manifest_schema_version",
            "score_parser_version",
            "run_config_hash",
        ]

    def to_dict(self) -> dict[str, Any]:
        return _clean_dict(asdict(self))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskRunRecord":
        normalized = dict(data)
        normalized.setdefault("prompt_hash", None)
        return cls(**normalized)
