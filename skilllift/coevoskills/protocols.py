from __future__ import annotations

from typing import Any, Protocol

from .schemas import (
    ArtifactSnapshot,
    Diagnostic,
    OracleResult,
    SkillVersion,
    SurrogateResult,
    TaskInput,
    TestSuiteVersion,
)


class JsonLLM(Protocol):
    request_count: int
    total_tokens: int

    def call_json(
        self, system_prompt: str, user_prompt: str, temperature: float = 0.0
    ) -> dict[str, Any]: ...


class SkillGeneratorBackend(Protocol):
    def initialize(self, task: TaskInput) -> SkillVersion: ...

    def refine(
        self,
        task: TaskInput,
        skill: SkillVersion,
        diagnostic: Diagnostic,
    ) -> SkillVersion: ...

    def note_oracle_failure(self) -> None: ...

    def context_ratio(self) -> float: ...


class SurrogateVerifierBackend(Protocol):
    def initialize_suite(
        self, task: TaskInput, artifacts: ArtifactSnapshot
    ) -> TestSuiteVersion: ...

    def escalate_suite(
        self,
        task: TaskInput,
        artifacts: ArtifactSnapshot,
        suite: TestSuiteVersion,
    ) -> TestSuiteVersion: ...

    def diagnose(
        self,
        task: TaskInput,
        artifacts: ArtifactSnapshot,
        suite: TestSuiteVersion,
        result: SurrogateResult,
    ) -> Diagnostic: ...


class ArtifactRolloutBackend(Protocol):
    def rollout(self, task: TaskInput, skill: SkillVersion) -> ArtifactSnapshot: ...


class OracleBackend(Protocol):
    def evaluate(self, task: TaskInput, skill: SkillVersion) -> OracleResult: ...


class SurrogateRuntimeBackend(Protocol):
    def run(
        self, suite: TestSuiteVersion, artifacts: ArtifactSnapshot
    ) -> SurrogateResult: ...


class TokenBudgetBackend(Protocol):
    def checkpoint(self, *, turns_completed: int, stage: str) -> bool:
        """Persist current usage and return whether the hard limit is exhausted."""
        ...
