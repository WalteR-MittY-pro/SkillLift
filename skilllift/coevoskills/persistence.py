from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .config import CoEvoSkillsConfig
from .schemas import (
    ArtifactSnapshot,
    Diagnostic,
    ExperimentResult,
    LoopState,
    OracleResult,
    SkillVersion,
    SurrogateResult,
    TaskInput,
    TestSuiteVersion,
)
from .skill_package import materialize_skill


class ExperimentStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def reset(self) -> None:
        if self.root.exists():
            for child in self.root.iterdir():
                shutil.rmtree(child) if child.is_dir() else child.unlink()

    @property
    def checkpoint_path(self) -> Path:
        return self.root / "checkpoint.json"

    def load_checkpoint(
        self, task: TaskInput, config: CoEvoSkillsConfig
    ) -> dict[str, Any] | None:
        if not self.checkpoint_path.exists():
            return None
        payload = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != 1:
            raise ValueError("unsupported CoEvoSkills checkpoint schema")
        if payload.get("task") != task.to_dict():
            raise ValueError("CoEvoSkills checkpoint task does not match this run")
        if payload.get("config") != config.to_dict():
            raise ValueError("CoEvoSkills checkpoint config does not match this run")
        return payload

    def save_checkpoint(self, payload: dict[str, Any]) -> Path:
        write_json_atomic(self.checkpoint_path, payload)
        return self.checkpoint_path

    def save_initial(self, task: TaskInput, config: CoEvoSkillsConfig) -> None:
        write_json(self.root / "task_public.json", task)
        write_json(self.root / "config.json", config)

    def save_skill(self, skill: SkillVersion) -> Path:
        path = self.root / "skills" / skill.token
        materialize_skill(skill, path)
        write_json(path / "metadata.json", skill)
        return path

    def save_snapshot(self, index: int, snapshot: ArtifactSnapshot) -> Path:
        path = self.root / "rollouts" / f"surrogate_{index:03d}" / "record.json"
        write_json(path, snapshot)
        return path

    def save_suite(self, suite: TestSuiteVersion) -> Path:
        root = self.root / "verifier" / "suites" / suite.token
        root.mkdir(parents=True, exist_ok=True)
        (root / "test_surrogate.py").write_text(_ensure_newline(suite.code), encoding="utf-8")
        write_json(root / "manifest.json", suite)
        return root

    def save_surrogate_result(self, index: int, result: SurrogateResult) -> Path:
        path = self.root / "verifier" / "assertion_results" / f"step_{index:03d}.json"
        write_json(path, result)
        return path

    def save_diagnostic(self, index: int, diagnostic: Diagnostic) -> Path:
        path = self.root / "verifier" / "diagnostics" / f"step_{index:03d}.json"
        write_json(path, diagnostic)
        return path

    def save_oracle(self, index: int, result: OracleResult) -> Path:
        path = self.root / "oracle_private" / f"oracle_{index:03d}" / "record.json"
        write_json(path, result)
        return path

    def save_state(self, state: LoopState) -> Path:
        path = self.root / "states" / f"step_{state.step:03d}.json"
        write_json(path, state)
        return path

    def append_event(self, payload: dict[str, Any]) -> None:
        path = self.root / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_jsonable(payload), ensure_ascii=False, sort_keys=True) + "\n")

    def save_result(self, result: ExperimentResult) -> None:
        write_json(self.root / "summary.json", result)
        final_root = self.root / "final_skill"
        if final_root.exists():
            shutil.rmtree(final_root)
        materialize_skill(result.best_skill, final_root)
        lines = [
            "# CoEvoSkills Result",
            "",
            f"- Stop reason: `{result.stop_reason}`",
            f"- Best skill: `{result.best_skill.token}`",
            f"- Best Oracle score: `{result.best_oracle_score}`",
            f"- Oracle interventions: `{result.oracle_interventions}`",
            f"- Surrogate retries: `{result.surrogate_retries}`",
        ]
        (self.root / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _jsonable(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return _jsonable(value.to_dict())
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _ensure_newline(value: str) -> str:
    return value if value.endswith("\n") else value + "\n"
