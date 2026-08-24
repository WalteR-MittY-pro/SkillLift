from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence

from ..errors import SkillLiftError, LLMOutputError
from ..llm_client import _parse_json_response
from .ref import PortfolioRef, portfolio_manifest


class PortfolioPromptError(SkillLiftError):
    """Raised when a portfolio LLM contract or context pack is invalid."""


class PortfolioContextLimitError(PortfolioPromptError):
    """Raised before an LLM call when the complete evidence pack cannot fit."""


@dataclass(frozen=True)
class PublicTask:
    task_id: str
    text: str
    evidence_ref: str = "task.md"


@dataclass(frozen=True)
class RubricHypothesis:
    rubric_id: str
    requirement: str
    evidence_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rubric_id": self.rubric_id,
            "requirement": self.requirement,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class SearchDirection:
    direction_id: str
    rubric_id: str
    hypothesis: str
    capability: str
    target_scope: tuple[str, ...]
    cross_skill_rationale: str | None
    evidence_refs: tuple[str, ...]
    novelty_key: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction_id": self.direction_id,
            "rubric_id": self.rubric_id,
            "hypothesis": self.hypothesis,
            "capability": self.capability,
            "target_scope": list(self.target_scope),
            "cross_skill_rationale": self.cross_skill_rationale,
            "evidence_refs": list(self.evidence_refs),
            "novelty_key": self.novelty_key,
        }

    def semantic_key(self) -> tuple[str, str, tuple[str, ...]]:
        return self.rubric_id, self.capability, self.target_scope


@dataclass(frozen=True)
class SearchPlan:
    receipt_version: int
    rubrics: tuple[RubricHypothesis, ...]
    directions: tuple[SearchDirection, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_version": self.receipt_version,
            "rubrics": [rubric.to_dict() for rubric in self.rubrics],
            "directions": [direction.to_dict() for direction in self.directions],
        }


@dataclass(frozen=True)
class ScalarOutcome:
    candidate_id: str
    direction_id: str
    reward: float | None
    reward_delta: float | None
    rank_group: int | None
    is_valid: bool
    is_accepted: bool

    def __post_init__(self) -> None:
        if not self.candidate_id or not self.direction_id:
            raise PortfolioPromptError("scalar outcome identifiers must not be empty")
        if self.is_valid:
            if self.reward is None or self.reward_delta is None or self.rank_group is None:
                raise PortfolioPromptError("valid scalar outcomes require reward, delta, and rank_group")
            if not math.isfinite(float(self.reward)) or not math.isfinite(float(self.reward_delta)):
                raise PortfolioPromptError("scalar rewards must be finite")
        elif any(value is not None for value in (self.reward, self.reward_delta, self.rank_group)):
            raise PortfolioPromptError("indeterminate outcomes must use null scalar fields")
        if self.is_accepted and not self.is_valid:
            raise PortfolioPromptError("an indeterminate candidate cannot be accepted")

    @classmethod
    def indeterminate(cls, candidate_id: str, direction_id: str) -> ScalarOutcome:
        return cls(candidate_id, direction_id, None, None, None, False, False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "direction_id": self.direction_id,
            "reward": self.reward,
            "reward_delta": self.reward_delta,
            "rank_group": self.rank_group,
            "is_valid": self.is_valid,
            "is_accepted": self.is_accepted,
        }


@dataclass(frozen=True)
class PromptBundle:
    system: str
    user: str


class LLMRubricatorPlanner:
    def __init__(
        self,
        client: Any,
        *,
        max_input_chars: int | None = None,
        core_evidence_on_overflow: bool = False,
    ) -> None:
        self.client = client
        self.max_input_chars = max_input_chars
        self.core_evidence_on_overflow = core_evidence_on_overflow

    def plan(
        self,
        task: PublicTask,
        parent: PortfolioRef,
        previous_plan: SearchPlan | None,
        history: Sequence[ScalarOutcome],
        direction_history: Sequence[SearchDirection],
        prohibited_novelty_keys: frozenset[str],
        verifier_feedback: Sequence[dict[str, Any]] | None = None,
    ) -> SearchPlan:
        prompt = build_rubricator_prompt(
            task,
            parent,
            previous_plan=previous_plan,
            history=history,
            direction_history=direction_history,
            verifier_feedback=verifier_feedback,
            max_input_chars=self.max_input_chars,
            core_evidence_on_overflow=self.core_evidence_on_overflow,
        )
        text = self.client.call_text(prompt.system, prompt.user, temperature=0.0)
        return parse_search_plan_text(
            text,
            portfolio=parent,
            public_task_ref=task.evidence_ref,
            previous_plan=previous_plan,
            prohibited_novelty_keys=prohibited_novelty_keys,
        )


class LLMPatchSkillGenerator:
    def __init__(self, client: Any, *, max_input_chars: int | None = None) -> None:
        self.client = client
        self.max_input_chars = max_input_chars

    def generate(
        self,
        task: PublicTask,
        direction: SearchDirection,
        parent: PortfolioRef,
    ) -> str:
        prompt = build_skill_generator_prompt(
            task,
            direction,
            parent,
            max_input_chars=self.max_input_chars,
        )
        return self.client.call_text(prompt.system, prompt.user, temperature=0.3)


def parse_search_plan_text(
    text: str,
    *,
    portfolio: PortfolioRef,
    public_task_ref: str,
    previous_plan: SearchPlan | None = None,
    prohibited_novelty_keys: Iterable[str] = (),
) -> SearchPlan:
    try:
        payload = _parse_json_response(text, repair=True)
    except LLMOutputError as exc:
        raise PortfolioPromptError(f"Rubricator response is not repairable JSON: {exc}") from exc
    return parse_search_plan(
        payload,
        portfolio=portfolio,
        public_task_ref=public_task_ref,
        previous_plan=previous_plan,
        prohibited_novelty_keys=prohibited_novelty_keys,
    )


def parse_search_plan(
    payload: dict[str, Any],
    *,
    portfolio: PortfolioRef,
    public_task_ref: str,
    previous_plan: SearchPlan | None = None,
    prohibited_novelty_keys: Iterable[str] = (),
) -> SearchPlan:
    if not isinstance(payload, dict):
        raise PortfolioPromptError("SearchPlan must be a JSON object")
    version = payload.get("receipt_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise PortfolioPromptError("receipt_version must be a positive integer")

    rubrics_by_id = {
        rubric.rubric_id: rubric for rubric in previous_plan.rubrics
    } if previous_plan else {}
    for item in payload.get("rubrics", []):
        rubric = _parse_rubric(item)
        if rubric is None:
            continue
        existing = rubrics_by_id.get(rubric.rubric_id)
        if existing is not None and existing.requirement != rubric.requirement:
            continue
        rubrics_by_id[rubric.rubric_id] = rubric

    prohibited = frozenset(prohibited_novelty_keys)
    directions: list[SearchDirection] = []
    semantic_keys: set[tuple[str, str, tuple[str, ...]]] = set()
    direction_ids: set[str] = set()
    novelty_keys: set[str] = set()
    raw_directions = payload.get("directions", [])
    if not isinstance(raw_directions, list):
        raw_directions = []
    for item in raw_directions:
        direction = _parse_direction(item, frozenset(rubrics_by_id))
        if direction is None:
            continue
        if (
            direction.direction_id in direction_ids
            or direction.novelty_key in novelty_keys
            or direction.novelty_key in prohibited
            or direction.semantic_key() in semantic_keys
        ):
            continue
        directions.append(direction)
        direction_ids.add(direction.direction_id)
        novelty_keys.add(direction.novelty_key)
        semantic_keys.add(direction.semantic_key())
        if len(directions) == 3:
            break

    return SearchPlan(
        receipt_version=version,
        rubrics=tuple(sorted(rubrics_by_id.values(), key=lambda item: item.rubric_id)),
        directions=tuple(directions),
    )


def serialize_search_history(history: Sequence[ScalarOutcome]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for outcome in history:
        if not isinstance(outcome, ScalarOutcome):
            raise TypeError("SearchHistory accepts only ScalarOutcome values")
        serialized.append(outcome.to_dict())
    return serialized


def build_rubricator_prompt(
    task: PublicTask,
    portfolio: PortfolioRef,
    *,
    previous_plan: SearchPlan | None,
    history: Sequence[ScalarOutcome],
    direction_history: Sequence[SearchDirection] = (),
    verifier_feedback: Sequence[dict[str, Any]] | None = None,
    max_input_chars: int | None = None,
    core_evidence_on_overflow: bool = False,
) -> PromptBundle:
    system = (
        "You are the Rubricator search planner. Return exactly one SearchPlan JSON object and no prose. "
        "Ground every rubric and falsifiable direction in the public task or current Portfolio. "
        "Propose at most three substantively different directions. Never infer hidden evaluator behavior. "
        "Each direction improves the SKILL PORTFOLIO: target_scope must be paths from Portfolio Manifest "
        "(a file like \"find-bugs/SKILL.md\", a script like \"find-bugs/scripts/check.sh\", "
        "or a skill directory like \"find-bugs/\"), never task source code paths."
    )
    feedback_section = (
        "[Verifier Feedback]\n"
        "Each item is a verifier score. positive_requirement_missing lists missing required behaviors; "
        "negative_violation_present lists explicit forbidden behaviors found. These are derived from criterion_hits.\n"
        f"{_json(list(verifier_feedback))}"
        if verifier_feedback
        else ""
    )
    def prompt_with(evidence: str) -> PromptBundle:
        user = "\n\n".join([
            f"[Public Task: {task.evidence_ref}]\n{task.text}",
            f"[Portfolio Manifest]\n{_json(_manifest_with_origins(portfolio))}",
            f"[Current Portfolio Files]\n{evidence}",
            f"[Previous SearchPlan]\n{_json(previous_plan.to_dict() if previous_plan else None)}",
            f"[Prior Direction History]\n{_json([item.to_dict() for item in direction_history])}",
            f"[Scalar SearchHistory]\n{_json(serialize_search_history(history))}",
            feedback_section,
            "[Output Contract]\n"
            '{"receipt_version": int, "rubrics": [{"rubric_id": str, "requirement": str, '
            '"evidence_refs": [str]}], "directions": [{"direction_id": str, "rubric_id": str, '
            '"hypothesis": str, "capability": str, "target_scope": [str], '
            '"cross_skill_rationale": str|null, "evidence_refs": [str], "novelty_key": str}]}.',
        ])
        return PromptBundle(system, user)

    prompt = prompt_with(_complete_evidence_pack(portfolio))
    if (
        core_evidence_on_overflow
        and max_input_chars is not None
        and len(prompt.system) + len(prompt.user) > max_input_chars
    ):
        prompt = prompt_with(_core_evidence_pack(portfolio))
    return _preflight(prompt, max_input_chars)


def build_skill_generator_prompt(
    task: PublicTask,
    direction: SearchDirection,
    portfolio: PortfolioRef,
    *,
    max_input_chars: int | None = None,
) -> PromptBundle:
    system = (
        "You are the Skill Generator. Return exactly one unified diff against the immutable parent Portfolio, "
        "optionally wrapped in one diff fence, and no prose. Do not repeat unchanged files or metadata."
    )
    target_files = []
    for entry in portfolio_manifest(portfolio.root):
        path = str(entry["path"])
        if any(_path_in_scope(path, scope) for scope in direction.target_scope):
            target_files.append(_file_section(path, portfolio.root / path))
    user = "\n\n".join(
        [
            f"[Public Task: {task.evidence_ref}]\n{task.text}",
            f"[Direction]\n{_json(direction.to_dict())}",
            f"[Portfolio Manifest]\n{_json(_manifest_with_origins(portfolio))}",
            "[Target Files]\n" + ("\n\n".join(target_files) if target_files else "<new target; no parent files>"),
            "[Patch Rules]\nChange only target_scope. Preserve curated skill identities. "
            "Use UTF-8 text under SKILL.md, scripts, or references; do not add dependencies or environments.",
        ]
    )
    return _preflight(PromptBundle(system, user), max_input_chars)


def _parse_rubric(item: Any) -> RubricHypothesis | None:
    if not isinstance(item, dict):
        return None
    rubric_id = _nonempty(item.get("rubric_id"))
    requirement = _nonempty(item.get("requirement"))
    evidence_refs = _evidence_refs(item.get("evidence_refs"))
    if rubric_id is None or requirement is None or evidence_refs is None:
        return None
    return RubricHypothesis(rubric_id, requirement, evidence_refs)


def _parse_direction(
    item: Any,
    rubric_ids: frozenset[str],
) -> SearchDirection | None:
    if not isinstance(item, dict):
        return None
    values = {
        key: _nonempty(item.get(key))
        for key in ("direction_id", "rubric_id", "hypothesis", "capability", "novelty_key")
    }
    if any(value is None for value in values.values()) or values["rubric_id"] not in rubric_ids:
        return None
    evidence_refs = _evidence_refs(item.get("evidence_refs"))
    target_scope = _target_scope(item.get("target_scope"))
    if evidence_refs is None or target_scope is None:
        return None
    cross_skill_rationale = item.get("cross_skill_rationale")
    if cross_skill_rationale is not None:
        cross_skill_rationale = _nonempty(cross_skill_rationale)
        if cross_skill_rationale is None:
            return None
    top_level_skills = {PurePosixPath(scope).parts[0] for scope in target_scope}
    if len(top_level_skills) > 1 and cross_skill_rationale is None:
        return None
    return SearchDirection(
        direction_id=values["direction_id"] or "",
        rubric_id=values["rubric_id"] or "",
        hypothesis=values["hypothesis"] or "",
        capability=values["capability"] or "",
        target_scope=target_scope,
        cross_skill_rationale=cross_skill_rationale,
        evidence_refs=evidence_refs,
        novelty_key=values["novelty_key"] or "",
    )


def _evidence_refs(raw: Any) -> tuple[str, ...] | None:
    if not isinstance(raw, list) or not raw:
        return None
    refs = tuple(item for item in raw if isinstance(item, str) and item)
    if len(refs) != len(raw) or len(set(refs)) != len(refs):
        return None
    return refs


def _target_scope(raw: Any) -> tuple[str, ...] | None:
    if not isinstance(raw, list) or not raw:
        return None
    normalized: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            return None
        path = PurePosixPath(item.strip().replace("\\", "/").rstrip("/"))
        if path.is_absolute() or not path.parts or ".." in path.parts:
            return None
        if any(not part for part in path.parts):
            return None
        normalized.add(path.as_posix())
    return tuple(sorted(normalized))


def _nonempty(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _complete_evidence_pack(portfolio: PortfolioRef) -> str:
    return "\n\n".join(
        _file_section(str(entry["path"]), portfolio.root / str(entry["path"]))
        for entry in portfolio_manifest(portfolio.root)
    )


def _core_evidence_pack(portfolio: PortfolioRef) -> str:
    paths = (
        str(entry["path"])
        for entry in portfolio_manifest(portfolio.root)
        if _is_core_evidence_path(str(entry["path"]))
    )
    return "\n\n".join(_file_section(path, portfolio.root / path) for path in paths)


def _is_core_evidence_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return len(parts) == 1 or (len(parts) == 2 and parts[1] == "SKILL.md")


def _file_section(path: str, file_path: Path) -> str:
    return f'--- path: {path} ---\n{file_path.read_text(encoding="utf-8").rstrip()}\n--- end: {path} ---'


def _manifest_with_origins(portfolio: PortfolioRef) -> list[dict[str, Any]]:
    rows = []
    for entry in portfolio_manifest(portfolio.root):
        row = dict(entry)
        skill_name = PurePosixPath(str(entry["path"])).parts[0]
        if skill_name in portfolio.curated_skill_names:
            row["origin"] = "curated"
        elif skill_name in portfolio.generated_skill_names:
            row["origin"] = "generated"
        else:
            row["origin"] = "curated_asset"
        rows.append(row)
    return rows


def _path_in_scope(path: str, scope: str) -> bool:
    return path == scope or path.startswith(f"{scope}/")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _preflight(prompt: PromptBundle, max_input_chars: int | None) -> PromptBundle:
    if max_input_chars is not None and len(prompt.system) + len(prompt.user) > max_input_chars:
        raise PortfolioContextLimitError("complete evidence pack exceeds the configured model context")
    return prompt
