from __future__ import annotations

from typing import Any

from .errors import SkillPackageError
from .prompts import generator_initial_prompt, generator_refine_prompt
from .protocols import JsonLLM
from .schemas import Diagnostic, SkillVersion, TaskInput
from .skill_package import parse_skill_payload


class LLMSkillGenerator:
    def __init__(self, client: JsonLLM, *, max_context_chars: int) -> None:
        self.client = client
        self.max_context_chars = max_context_chars
        self.history: list[dict[str, Any]] = []
        self._context_chars = 0

    def initialize(self, task: TaskInput) -> SkillVersion:
        system, user = generator_initial_prompt(task)
        self._account(system, user)
        payload = self.client.call_json(system, user, temperature=0.2)
        skill = self._parse_with_repair(payload, system, user, version=0)
        self.history.append({"type": "initial_skill", "skill_version": skill.version})
        return skill

    def refine(
        self,
        task: TaskInput,
        skill: SkillVersion,
        diagnostic: Diagnostic,
    ) -> SkillVersion:
        feedback = {"type": "surrogate_diagnostic", **diagnostic.to_dict()}
        self.history.append(feedback)
        system, user = generator_refine_prompt(task, skill, diagnostic, self.history)
        self._account(system, user)
        payload = self.client.call_json(system, user, temperature=0.2)
        revised = self._parse_with_repair(
            payload,
            system,
            user,
            version=skill.version + 1,
            base=skill,
        )
        self.history.append({"type": "skill_revision", "skill_version": revised.version})
        return revised

    def note_oracle_failure(self) -> None:
        self.history.append({"type": "oracle_result", "passed": False})
        self._context_chars += len('{"type":"oracle_result","passed":false}')

    def context_ratio(self) -> float:
        return min(1.0, self._context_chars / self.max_context_chars)

    def export_state(self) -> dict[str, Any]:
        return {
            "history": list(self.history),
            "context_chars": self._context_chars,
        }

    def restore_state(self, payload: dict[str, Any]) -> None:
        history = payload.get("history", [])
        context_chars = payload.get("context_chars", 0)
        if not isinstance(history, list) or not isinstance(context_chars, int):
            raise ValueError("invalid CoEvoSkills generator checkpoint state")
        self.history = list(history)
        self._context_chars = max(0, context_chars)

    def _account(self, *values: str) -> None:
        self._context_chars = sum(len(value) for value in values)

    def _parse_with_repair(
        self,
        payload: dict[str, Any],
        system: str,
        user: str,
        *,
        version: int,
        base: SkillVersion | None = None,
    ) -> SkillVersion:
        try:
            return parse_skill_payload(payload, version=version, base=base)
        except SkillPackageError as exc:
            repair_user = (
                f"{user}\n\n[Validation Error]\n{exc}\n"
                "Return one corrected JSON object only."
            )
            self._account(system, repair_user)
            repaired = self.client.call_json(system, repair_user, temperature=0.0)
            return parse_skill_payload(repaired, version=version, base=base)
