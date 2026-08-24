from __future__ import annotations

import json
import os
import shutil
from dataclasses import is_dataclass
from pathlib import Path
from typing import Any

from .errors import PersistenceError
from .schemas import (
    SkillLiftConfig,
    EvoSkill,
    OracleScore,
    Receipt,
    ReceiptRevisionAttempt,
    RoundState,
    SkillKey,
    TaskSpec,
    VerifierScore,
    _to_jsonable,
)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 用 _to_jsonable 预处理，处理 dict[SkillKey, ...] 的 key（_json_default 只能处理 value）。
    path.write_text(
        json.dumps(_to_jsonable(payload), indent=2, sort_keys=True, ensure_ascii=False, default=_json_default) + "\n",
        encoding="utf-8",
    )


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(
            _to_jsonable(payload),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            default=_json_default,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def append_jsonl(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=_json_default))
        handle.write("\n")


def read_jsonl(path: Path) -> list[Any]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class ExperimentStore:
    def __init__(self, exp_dir: Path | str):
        self.exp_dir = Path(exp_dir)
        self.exp_dir.mkdir(parents=True, exist_ok=True)
        self.current_outer_round = 0

    def save_config(self, config: SkillLiftConfig) -> None:
        write_json(self.exp_dir / "config.json", config)

    @property
    def checkpoint_path(self) -> Path:
        return self.exp_dir / "checkpoint.json"

    def load_checkpoint(self, signature: dict[str, Any]) -> dict[str, Any] | None:
        if not self.checkpoint_path.is_file():
            return None
        payload = read_json(self.checkpoint_path)
        if payload.get("schema_version") != 1:
            raise ValueError("unsupported checkpoint schema")
        if payload.get("signature") != _to_jsonable(signature):
            raise ValueError("checkpoint does not match this run")
        return payload

    def save_checkpoint(self, payload: dict[str, Any]) -> Path:
        write_json_atomic(self.checkpoint_path, payload)
        return self.checkpoint_path

    def save_task_spec(self, task: TaskSpec) -> None:
        write_json(self.exp_dir / "task_spec.json", task)

    def save_receipt(self, receipt: Receipt, label: str) -> Path:
        path = self.exp_dir / "receipts" / f"{_safe_label(label)}.json"
        write_json(path, receipt)
        return path

    def save_skill(self, skill: EvoSkill, label: str) -> Path:
        path = self.exp_dir / "skills" / _safe_label(label) / f"{skill.key.token()}_{_safe_label(skill.skill_name)}.json"
        write_json(path, skill)
        return path

    def save_verifier_scores(self, scores: dict[SkillKey, VerifierScore], label: str) -> Path:
        path = self.exp_dir / "verifier_scores" / f"{_safe_label(label)}.json"
        write_json(path, _keyed_payload(scores))
        return path

    def save_oracle_scores(self, scores: dict[SkillKey, OracleScore], label: str) -> Path:
        path = self.exp_dir / "oracle_scores" / f"{_safe_label(label)}.json"
        write_json(path, _keyed_payload(scores))
        return path

    def save_round_state(self, state: RoundState) -> Path:
        suffix = "" if state.mode_iter == 0 else f"_iter_{state.mode_iter:03d}"
        path = self.exp_dir / "round_states" / f"step_{state.step:03d}_{state.mode}{suffix}.json"
        write_json(path, state)
        return path

    def save_revision_attempt(self, attempt: ReceiptRevisionAttempt, files: dict[str, str]) -> Path:
        root = self.exp_dir / "receipt_revision_attempts" / _safe_label(attempt.attempt_id)
        root.mkdir(parents=True, exist_ok=True)
        write_json(root / "metadata.json", attempt)
        for name, content in files.items():
            target = (root / _safe_relative_path(name)).resolve()
            if root.resolve() not in target.parents and target != root.resolve():
                raise PersistenceError(f"revision attempt file escapes directory: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_ensure_newline(str(content)), encoding="utf-8")
        return root

    def append_event(self, event: dict[str, Any]) -> None:
        append_jsonl(self.exp_dir / "events.jsonl", event)

    def save_summary(self, summary: dict[str, Any], markdown: str) -> None:
        write_json(self.exp_dir / "summary.json", summary)
        (self.exp_dir / "summary.md").write_text(_ensure_newline(markdown), encoding="utf-8")

    def reset_new_run(self) -> None:
        self.exp_dir.mkdir(parents=True, exist_ok=True)
        for child in self.exp_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()


def _keyed_payload(values: dict[SkillKey, Any]) -> dict[str, Any]:
    return {key.token(): value for key, value in sorted(values.items())}


def _json_default(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if is_dataclass(value):
        return value.__dict__
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, SkillKey):
        return value.token()
    raise TypeError(f"Object is not JSON serializable: {type(value)!r}")


def _safe_label(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(value)).strip("_") or "item"


def _safe_relative_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise PersistenceError(f"unsafe relative path: {value}")
    return path


def _ensure_newline(value: str) -> str:
    return value if value.endswith("\n") else value + "\n"
