from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
import math
from pathlib import Path
from types import UnionType
from typing import Any, TypeVar, Union, get_args, get_origin, get_type_hints

from .errors import SchemaError

T = TypeVar("T", bound="JsonDataclassMixin")
DEFAULT_OPENCLAW_MODELS_CONFIG = "models_config_openclaw.json"
DEFAULT_FRAMEWORK_MODELS_CONFIG = "models_config.json"


class JsonDataclassMixin:
    """JSON-friendly dataclass helper with nested v2 schema decoding."""

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)

    @classmethod
    def from_dict(cls: type[T], data: dict[str, Any]) -> T:
        if not isinstance(data, dict):
            raise SchemaError(f"{cls.__name__}.from_dict expected dict")
        hints = get_type_hints(cls)
        kwargs: dict[str, Any] = {}
        for item in fields(cls):
            if item.name in data:
                kwargs[item.name] = _decode_value(
                    data[item.name], hints.get(item.name, Any)
                )
        return cls(**kwargs)


@dataclass
class TaskSpec(JsonDataclassMixin):
    task_name: str
    task_description: str
    task_doc_path: str | None = None


@dataclass(frozen=True, order=True)
class SkillKey(JsonDataclassMixin):
    slot: int
    version: int

    def token(self) -> str:
        return f"s{self.slot:03d}_v{self.version:03d}"

    @classmethod
    def from_token(cls, value: str) -> "SkillKey":
        if isinstance(value, str) and value.startswith("s") and "_v" in value:
            left, right = value.split("_v", 1)
            return cls(slot=int(left[1:]), version=int(right))
        if isinstance(value, str) and ":" in value:
            slot, version = value.split(":", 1)
            return cls(slot=int(slot), version=int(version))
        raise SchemaError(f"invalid SkillKey token: {value!r}")


@dataclass
class EvoSkill(JsonDataclassMixin):
    key: SkillKey
    skill_name: str
    files: dict[str, str]
    entrypoint: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RubricCriterion(JsonDataclassMixin):
    rubric_id: str
    category: str
    criterion: str
    points: int


@dataclass
class Receipt(JsonDataclassMixin):
    version: int
    rubrics: list[RubricCriterion]
    maximum_score: int
    minimum_score: int
    baseline_score: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    removed_rubrics: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class VerifierScore(JsonDataclassMixin):
    skill: SkillKey
    receipt_version: int
    criterion_hits: dict[str, bool]
    raw_score: int
    normalized_score: float
    rank: int = -1
    rationale: str = ""
    criterion_evidence: dict[str, str] = field(default_factory=dict)
    positive_requirement_missing: list[str] = field(default_factory=list)
    negative_violation_present: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill": self.skill.to_dict(),
            "receipt_version": self.receipt_version,
            "criterion_hits": dict(self.criterion_hits),
            "raw_score": self.raw_score,
            "normalized_score": self.normalized_score,
            "rank": self.rank,
            "rationale": self.rationale,
            "criterion_evidence": dict(self.criterion_evidence),
            "positive_requirement_missing": self.positive_requirement_missing,
            "negative_violation_present": self.negative_violation_present,
        }

@dataclass
class OracleFeedback(JsonDataclassMixin):
    summary: str = ""
    produced_files: list[str] = field(default_factory=list)
    missing_files: list[str] = field(default_factory=list)
    stderr_excerpt: str = ""
    traceback_summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OracleScore(JsonDataclassMixin):
    skill: SkillKey
    oracle_score: float
    oracle_pass: int
    rank: int = -1
    feedback: OracleFeedback = field(default_factory=OracleFeedback)
    output_dir: str = ""


@dataclass
class RoundState(JsonDataclassMixin):
    step: int
    outer_round: int
    mode: str
    mode_iter: int
    skills: dict[SkillKey, EvoSkill]
    receipt: Receipt
    verifier_scores: dict[SkillKey, VerifierScore] = field(default_factory=dict)
    oracle_scores: dict[SkillKey, OracleScore] = field(default_factory=dict)
    verifier_rank: list[SkillKey] = field(default_factory=list)
    oracle_rank: list[SkillKey] = field(default_factory=list)
    rank_alignment: float | None = None
    diagnostics: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TaskRanking(JsonDataclassMixin):
    task_id: str
    domain: str
    oracle_rank: list[SkillKey]
    skill_scores: dict[SkillKey, OracleScore]
    has_signal: bool
    no_signal_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "domain": self.domain,
            "oracle_rank": [key.token() for key in self.oracle_rank],
            "skill_scores": {
                key.token(): value.to_dict() for key, value in self.skill_scores.items()
            },
            "has_signal": self.has_signal,
            "no_signal_reason": self.no_signal_reason,
        }


@dataclass
class ReceiptRevisionInput(JsonDataclassMixin):
    """Rubricator revision input.

    For tau2 batches, oracle_scores is the per-skill aggregate view of the n x k
    matrix, while per_task_rankings is the granular per-task evidence view.
    """

    task: TaskSpec
    receipt: Receipt
    skills: dict[SkillKey, EvoSkill]
    verifier_scores: dict[SkillKey, VerifierScore]
    oracle_scores: dict[SkillKey, OracleScore]
    verifier_rank: list[SkillKey]
    oracle_rank: list[SkillKey]
    rank_alignment: float | None
    per_task_rankings: list[TaskRanking] | None = None
    oracle_contrast: dict[str, Any] | None = None
    oracle_evidence: dict[str, Any] | None = None


@dataclass
class ReceiptRevisionAttempt(JsonDataclassMixin):
    attempt_id: str
    old_receipt_version: int
    accepted: bool
    error_codes: list[str] = field(default_factory=list)
    candidate_receipt: Receipt | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvidenceBudgetConfig(JsonDataclassMixin):
    level: str = "auto"
    max_total_chars: int = 60000
    max_skill_chars: int = 12000
    max_aux_file_chars: int = 2000
    max_excerpt_chars: int = 6000


@dataclass
class SkillLiftConfig(JsonDataclassMixin):
    exp_root: str = "skilllift_algorithm"
    exp_name: str = "skilllift_v2"
    model: str = ""
    models_config: str = DEFAULT_OPENCLAW_MODELS_CONFIG
    openclaw_models_config: str = ""
    framework_models_config: str = DEFAULT_FRAMEWORK_MODELS_CONFIG
    python_executable: str = "python3"
    wildclaw_root: str = ""
    output_root: str | None = None
    skill_count: int = 4
    outer_rounds: int = 3
    mode_a_iters: int = 1
    mode_b_iters: int = 1
    skill_format: str = "code_package"
    mode_a_min_score_threshold: float = 0.85
    rank_alignment_threshold: float = 0.9
    oracle_threshold: float = 0.9
    global_success_threshold: float = 0.9
    oracle_feedback_level: int = 2
    oracle_concurrency: int = 1
    oracle_rate_limit_retries: int = 2
    oracle_rate_limit_wait_seconds: float = 120.0
    initial_skill_path: str | None = None
    use_llm: bool = True
    skill_generator_model: str = ""
    rubricator_model: str = ""
    verifier_model: str = ""
    early_stop_non_skill_failures: int = 3
    agent_timeout_override: int = 0
    evidence_budget: EvidenceBudgetConfig = field(default_factory=EvidenceBudgetConfig)

    def __post_init__(self) -> None:
        for field_name in (
            "mode_a_min_score_threshold",
            "rank_alignment_threshold",
            "oracle_threshold",
            "global_success_threshold",
        ):
            try:
                value = float(getattr(self, field_name))
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"{field_name} must be in [0, 1]") from exc
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{field_name} must be in [0, 1]")

    def openclaw_models_config_path(self) -> str:
        """Config injected into OpenClaw and passed to eval/run_batch.py."""
        return (
            self.openclaw_models_config
            or self.models_config
            or DEFAULT_OPENCLAW_MODELS_CONFIG
        )

    def framework_models_config_path(self) -> str:
        """Config used by CoEvo framework LLM roles such as SG and Rubricator."""
        return self.framework_models_config or DEFAULT_FRAMEWORK_MODELS_CONFIG


@dataclass
class ExperimentResult(JsonDataclassMixin):
    task: TaskSpec
    config: SkillLiftConfig
    final_receipt: Receipt
    best_skill: EvoSkill
    best_skill_group: dict[SkillKey, EvoSkill]
    summary: dict[str, Any]
    exp_dir: str


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, SkillKey):
        return {"slot": value.slot, "version": value.version}
    if is_dataclass(value):
        return {
            item.name: _to_jsonable(getattr(value, item.name)) for item in fields(value)
        }
    if isinstance(value, dict):
        return {_json_key(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    return value


def _json_key(key: Any) -> str:
    if isinstance(key, SkillKey):
        return key.token()
    return str(key)


def _decode_value(value: Any, annotation: Any) -> Any:
    if annotation is Any:
        return value
    if _is_optional(annotation):
        if value is None:
            return None
        non_none = [arg for arg in get_args(annotation) if arg is not type(None)]
        return _decode_value(value, non_none[0] if non_none else Any)
    if annotation is SkillKey:
        if isinstance(value, SkillKey):
            return value
        if isinstance(value, dict):
            return SkillKey.from_dict(value)
        return SkillKey.from_token(str(value))
    if _is_dataclass_type(annotation) and isinstance(value, dict):
        return (
            annotation.from_dict(value)
            if issubclass(annotation, JsonDataclassMixin)
            else annotation(**value)
        )

    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in {list, tuple}:
        item_type = args[0] if args else Any
        return [_decode_value(item, item_type) for item in value]
    if origin is dict:
        key_type = args[0] if args else Any
        item_type = args[1] if len(args) > 1 else Any
        return {
            _decode_key(key, key_type): _decode_value(item, item_type)
            for key, item in (value or {}).items()
        }
    return value


def _decode_key(value: Any, annotation: Any) -> Any:
    if annotation is SkillKey:
        if isinstance(value, SkillKey):
            return value
        if isinstance(value, dict):
            return SkillKey.from_dict(value)
        return SkillKey.from_token(str(value))
    return value


def _is_optional(annotation: Any) -> bool:
    origin = get_origin(annotation)
    return origin in {Union, UnionType} and type(None) in get_args(annotation)


def _is_dataclass_type(annotation: Any) -> bool:
    return isinstance(annotation, type) and is_dataclass(annotation)
