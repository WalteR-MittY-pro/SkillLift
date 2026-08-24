from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TaskInput:
    task_id: str
    instruction: str
    task_path: str | None = None
    reference_material: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SkillVersion:
    version: int
    name: str
    files: dict[str, str]
    entrypoint: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def token(self) -> str:
        return f"s{self.version:03d}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactFile:
    path: str
    size_bytes: int
    sha256: str
    media_type: str
    text_excerpt: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactSnapshot:
    run_id: str
    root_path: str
    files: list[ArtifactFile]
    metadata: dict[str, Any] = field(default_factory=dict)

    def public_view(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "files": [item.to_dict() for item in self.files],
            "metadata": {
                key: value
                for key, value in self.metadata.items()
                if key in {"task_id", "file_count", "total_bytes"}
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TestAssertion:
    assertion_id: str
    description: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class TestSuiteVersion:
    version: int
    code: str
    assertions: list[TestAssertion]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def token(self) -> str:
        return f"v{self.version:03d}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AssertionResult:
    assertion_id: str
    passed: bool
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SurrogateResult:
    suite_version: int
    results: list[AssertionResult]
    stdout: str = ""
    stderr: str = ""

    @property
    def score(self) -> float:
        if not self.results:
            return 0.0
        return sum(item.passed for item in self.results) / len(self.results)

    @property
    def passed(self) -> bool:
        return bool(self.results) and all(item.passed for item in self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_version": self.suite_version,
            "results": [item.to_dict() for item in self.results],
            "score": self.score,
            "passed": self.passed,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


@dataclass(frozen=True)
class Diagnostic:
    failed_assertion_ids: list[str]
    root_cause: str
    suggestions: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OracleResult:
    run_id: str
    score: float
    passed: bool
    artifact_path: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def public_view(self) -> dict[str, bool]:
        return {"passed": self.passed}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LoopState:
    step: int
    skill_version: int
    suite_version: int | None
    surrogate_retries: int
    oracle_interventions: int
    context_ratio: float
    transition: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExperimentResult:
    final_skill: SkillVersion
    best_skill: SkillVersion
    best_oracle_score: float | None
    stop_reason: str
    surrogate_retries: int
    oracle_interventions: int
    steps: int
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "final_skill": self.final_skill.to_dict(),
            "best_skill": self.best_skill.to_dict(),
            "best_oracle_score": self.best_oracle_score,
            "stop_reason": self.stop_reason,
            "surrogate_retries": self.surrogate_retries,
            "oracle_interventions": self.oracle_interventions,
            "steps": self.steps,
            "summary": self.summary,
        }


def ensure_path(value: str | Path) -> Path:
    return value if isinstance(value, Path) else Path(value)
