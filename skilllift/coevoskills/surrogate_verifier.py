from __future__ import annotations

from typing import Any

from .errors import LLMResponseError, SurrogateSuiteError
from .prompts import verifier_diagnostic_prompt, verifier_suite_prompt
from .protocols import JsonLLM
from .schemas import (
    ArtifactSnapshot,
    Diagnostic,
    SurrogateResult,
    TaskInput,
    TestAssertion,
    TestSuiteVersion,
)
from .surrogate_runtime import validate_suite_code


class LLMSurrogateVerifier:
    def __init__(self, client: JsonLLM) -> None:
        self.client = client

    def initialize_suite(
        self, task: TaskInput, artifacts: ArtifactSnapshot
    ) -> TestSuiteVersion:
        return self._generate_suite(task, artifacts, previous=None, oracle_mismatch=False)

    def escalate_suite(
        self,
        task: TaskInput,
        artifacts: ArtifactSnapshot,
        suite: TestSuiteVersion,
    ) -> TestSuiteVersion:
        return self._generate_suite(task, artifacts, previous=suite, oracle_mismatch=True)

    def diagnose(
        self,
        task: TaskInput,
        artifacts: ArtifactSnapshot,
        suite: TestSuiteVersion,
        result: SurrogateResult,
    ) -> Diagnostic:
        system, user = verifier_diagnostic_prompt(task, artifacts, suite, result)
        payload = self.client.call_json(system, user, temperature=0.0)
        failed = payload.get("failed_assertion_ids")
        suggestions = payload.get("suggestions")
        root_cause = payload.get("root_cause")
        actual_failed = {item.assertion_id for item in result.results if not item.passed}
        if not isinstance(failed, list) or not failed or not set(map(str, failed)).issubset(actual_failed):
            raise LLMResponseError("diagnostic failed_assertion_ids must reference actual failures")
        if not isinstance(suggestions, list) or not suggestions:
            raise LLMResponseError("diagnostic suggestions must be a non-empty list")
        if not isinstance(root_cause, str) or not root_cause.strip():
            raise LLMResponseError("diagnostic root_cause must be non-empty")
        return Diagnostic(
            failed_assertion_ids=[str(item) for item in failed],
            root_cause=root_cause.strip(),
            suggestions=[str(item).strip() for item in suggestions if str(item).strip()],
        )

    def _generate_suite(
        self,
        task: TaskInput,
        artifacts: ArtifactSnapshot,
        *,
        previous: TestSuiteVersion | None,
        oracle_mismatch: bool,
    ) -> TestSuiteVersion:
        system, user = verifier_suite_prompt(task, artifacts, previous, oracle_mismatch)
        payload = self.client.call_json(system, user, temperature=0.1)
        try:
            return self._parse_suite_payload(payload, previous, oracle_mismatch)
        except (LLMResponseError, SurrogateSuiteError) as exc:
            repair_user = (
                f"{user}\n\n[Validation Error]\n{exc}\n"
                "Return one corrected JSON object only."
            )
            repaired = self.client.call_json(system, repair_user, temperature=0.0)
            return self._parse_suite_payload(repaired, previous, oracle_mismatch)

    def _parse_suite_payload(
        self,
        payload: dict[str, Any],
        previous: TestSuiteVersion | None,
        oracle_mismatch: bool,
    ) -> TestSuiteVersion:
        code = payload.get("code")
        raw_assertions = payload.get("assertions")
        if not isinstance(code, str) or not code.strip():
            raise LLMResponseError("surrogate suite code must be non-empty")
        validate_suite_code(code)
        if not isinstance(raw_assertions, list) or not raw_assertions:
            raise LLMResponseError("surrogate suite must declare at least one assertion")
        assertions: list[TestAssertion] = []
        seen: set[str] = set()
        for raw in raw_assertions:
            if not isinstance(raw, dict):
                raise LLMResponseError("each surrogate assertion must be an object")
            assertion_id = str(raw.get("assertion_id") or "").strip()
            description = str(raw.get("description") or "").strip()
            if not assertion_id or assertion_id in seen or not description:
                raise LLMResponseError("surrogate assertion ids must be unique and described")
            seen.add(assertion_id)
            assertions.append(TestAssertion(assertion_id, description))
        return TestSuiteVersion(
            version=0 if previous is None else previous.version + 1,
            code=code,
            assertions=assertions,
            metadata={"oracle_mismatch_triggered": oracle_mismatch},
        )
