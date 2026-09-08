from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import PersistenceError
from .ref import PortfolioPatchResult, PortfolioRef, apply_portfolio_patch, portfolio_tree_hash
from .prompts import RubricHypothesis, SearchDirection, SearchPlan


_ATTEMPT_FILE_RE = re.compile(r"^attempt-(\d+)\.json$")


STORE_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class CandidateRecord:
    candidate_id: str
    round_index: int
    direction: SearchDirection
    patch: str
    parent_hash: str
    candidate_hash: str | None
    patch_hash: str
    new_skill_names: tuple[str, ...]
    changed_paths: tuple[str, ...]
    is_valid: bool
    failure_kind: str | None
    error: str | None


class PortfolioStore:
    def __init__(self, run_root: Path | str) -> None:
        self.run_root = Path(run_root).resolve()

    def initialize(self, task_id: str, seed: PortfolioRef, algorithm_hash: str) -> dict[str, Any]:
        task_root = self.task_root(task_id)
        task_root.mkdir(parents=True, exist_ok=True)
        seed_payload = {
            "schema_version": STORE_SCHEMA_VERSION,
            "task_id": task_id,
            "root": str(seed.root),
            "tree_hash": seed.tree_hash,
            "seed_hash": seed.seed_hash,
            "config_hash": seed.config_hash,
            "algorithm_hash": algorithm_hash,
            "curated_skill_names": sorted(seed.curated_skill_names),
            "generated_skill_names": sorted(seed.generated_skill_names),
            "curated_asset_paths": sorted(seed.curated_asset_paths),
        }
        self._write_once(task_root / "seed.json", seed_payload)
        stored_seed = self._read_json(task_root / "seed.json")
        if stored_seed != seed_payload:
            raise PersistenceError("resume seed or algorithm fingerprint does not match the existing task run")
        if portfolio_tree_hash(seed.root) != seed.tree_hash:
            raise PersistenceError("seed portfolio changed after its run was initialized")

        state_path = task_root / "state.json"
        if not state_path.exists():
            self._write_json_atomic(
                state_path,
                {
                    "schema_version": STORE_SCHEMA_VERSION,
                    "task_id": task_id,
                    "algorithm_hash": algorithm_hash,
                    "status": "anchor_pending",
                    "parent_reward": None,
                    "next_round": 0,
                    "no_improvement_rounds": 0,
                    "accepted_candidates": [],
                    "history": [],
                    "direction_history": [],
                    "prohibited_novelty_keys": [],
                    "previous_plan": None,
                    "stop_reason": None,
                    "cohort": None,
                    "final_rewards": [],
                    "final_score": None,
                },
            )
        state = self.load_state(task_id)
        if state.get("algorithm_hash") != algorithm_hash or state.get("task_id") != task_id:
            raise PersistenceError("task state fingerprint does not match this run")
        return state

    def task_root(self, task_id: str) -> Path:
        if not task_id or task_id in {".", ".."} or "/" in task_id or "\\" in task_id:
            raise PersistenceError(f"unsafe task id: {task_id!r}")
        return self.run_root / "tasks" / task_id

    def load_state(self, task_id: str) -> dict[str, Any]:
        payload = self._read_json(self.task_root(task_id) / "state.json")
        if not isinstance(payload, dict):
            raise PersistenceError("task state must be a JSON object")
        return payload

    def save_state(self, task_id: str, state: dict[str, Any]) -> None:
        self._write_json_atomic(self.task_root(task_id) / "state.json", state)

    def load_seed(self, task_id: str) -> PortfolioRef:
        payload = self._read_json(self.task_root(task_id) / "seed.json")
        root = Path(payload["root"]).resolve()
        portfolio = PortfolioRef.from_directory(
            task_id=task_id,
            root=root,
            seed_hash=payload["seed_hash"],
            config_hash=payload["config_hash"],
            curated_skill_names=payload["curated_skill_names"],
            generated_skill_names=payload["generated_skill_names"],
            curated_asset_paths=payload.get("curated_asset_paths", []),
        )
        if portfolio.tree_hash != payload["tree_hash"]:
            raise PersistenceError("persisted seed tree hash no longer matches")
        return portfolio

    def save_plan(self, task_id: str, round_index: int, plan: SearchPlan) -> None:
        self._write_once(self._round_root(task_id, round_index) / "search_plan.json", plan.to_dict())

    def load_plan(self, task_id: str, round_index: int) -> SearchPlan | None:
        path = self._round_root(task_id, round_index) / "search_plan.json"
        if not path.exists():
            return None
        payload = self._read_json(path)
        try:
            rubrics = tuple(
                RubricHypothesis(
                    rubric_id=item["rubric_id"],
                    requirement=item["requirement"],
                    evidence_refs=tuple(item["evidence_refs"]),
                )
                for item in payload["rubrics"]
            )
            directions = tuple(
                SearchDirection(
                    direction_id=item["direction_id"],
                    rubric_id=item["rubric_id"],
                    hypothesis=item["hypothesis"],
                    capability=item["capability"],
                    target_scope=tuple(item["target_scope"]),
                    cross_skill_rationale=item.get("cross_skill_rationale"),
                    evidence_refs=tuple(item["evidence_refs"]),
                    novelty_key=item["novelty_key"],
                )
                for item in payload["directions"]
            )
            return SearchPlan(int(payload["receipt_version"]), rubrics, directions)
        except (KeyError, TypeError, ValueError) as exc:
            raise PersistenceError(f"invalid persisted SearchPlan: {path}") from exc

    def save_candidate(
        self,
        task_id: str,
        round_index: int,
        candidate_id: str,
        direction: SearchDirection,
        patch: str,
        parent_hash: str,
        result: PortfolioPatchResult | None,
        *,
        new_skill_names: tuple[str, ...],
        failure_kind: str | None,
        error: str | None,
    ) -> CandidateRecord:
        target = self._candidate_root(task_id, round_index, candidate_id)
        if target.exists():
            existing = self.load_candidate(task_id, round_index, candidate_id)
            if existing is None:
                raise PersistenceError(f"incomplete candidate record: {target}")
            return existing
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f".{candidate_id}-", dir=target.parent) as temporary:
            temp_root = Path(temporary) / "record"
            temp_root.mkdir()
            self._write_text_atomic(temp_root / "patch.diff", patch)
            self._write_json_atomic(temp_root / "direction.json", direction.to_dict())
            validation = {
                "candidate_id": candidate_id,
                "round_index": round_index,
                "parent_hash": parent_hash,
                "candidate_hash": result.portfolio.tree_hash if result else None,
                "patch_hash": result.patch_hash if result else hashlib.sha256(patch.encode("utf-8")).hexdigest(),
                "new_skill_names": list(new_skill_names),
                "changed_paths": list(result.changed_paths) if result else [],
                "is_valid": result is not None,
                "failure_kind": failure_kind,
                "error": error,
            }
            self._write_json_atomic(temp_root / "validation.json", validation)
            os.replace(temp_root, target)
        record = self.load_candidate(task_id, round_index, candidate_id)
        if record is None:
            raise PersistenceError(f"candidate record was not committed: {target}")
        return record

    def load_candidate(self, task_id: str, round_index: int, candidate_id: str) -> CandidateRecord | None:
        root = self._candidate_root(task_id, round_index, candidate_id)
        required = (root / "direction.json", root / "patch.diff", root / "validation.json")
        if not all(path.is_file() for path in required):
            return None
        direction_payload = self._read_json(root / "direction.json")
        validation = self._read_json(root / "validation.json")
        direction = SearchDirection(
            direction_id=direction_payload["direction_id"],
            rubric_id=direction_payload["rubric_id"],
            hypothesis=direction_payload["hypothesis"],
            capability=direction_payload["capability"],
            target_scope=tuple(direction_payload["target_scope"]),
            cross_skill_rationale=direction_payload.get("cross_skill_rationale"),
            evidence_refs=tuple(direction_payload["evidence_refs"]),
            novelty_key=direction_payload["novelty_key"],
        )
        patch = (root / "patch.diff").read_text(encoding="utf-8")
        patch_hash = hashlib.sha256(patch.encode("utf-8")).hexdigest()
        if patch_hash != validation["patch_hash"]:
            raise PersistenceError(f"candidate patch hash mismatch: {candidate_id}")
        return CandidateRecord(
            candidate_id=candidate_id,
            round_index=round_index,
            direction=direction,
            patch=patch,
            parent_hash=validation["parent_hash"],
            candidate_hash=validation.get("candidate_hash"),
            patch_hash=patch_hash,
            new_skill_names=tuple(validation["new_skill_names"]),
            changed_paths=tuple(validation["changed_paths"]),
            is_valid=bool(validation["is_valid"]),
            failure_kind=validation.get("failure_kind"),
            error=validation.get("error"),
        )

    def materialize_candidate(
        self,
        parent: PortfolioRef,
        record: CandidateRecord,
        destination: Path,
    ) -> PortfolioRef:
        if not record.is_valid or record.candidate_hash is None:
            raise PersistenceError(f"cannot materialize invalid candidate: {record.candidate_id}")
        if record.parent_hash != parent.tree_hash:
            raise PersistenceError(f"candidate parent hash mismatch: {record.candidate_id}")
        result = apply_portfolio_patch(
            parent,
            record.patch,
            destination,
            target_scope=record.direction.target_scope,
            new_skill_names=record.new_skill_names,
        )
        if result.portfolio.tree_hash != record.candidate_hash:
            raise PersistenceError(f"candidate reconstruction hash mismatch: {record.candidate_id}")
        return result.portfolio

    def materialize_parent(self, task_id: str, state: dict[str, Any], workspace: Path) -> PortfolioRef:
        current = self.load_seed(task_id)
        for index, accepted in enumerate(state.get("accepted_candidates", [])):
            record = self.load_candidate(task_id, int(accepted["round_index"]), accepted["candidate_id"])
            if record is None:
                raise PersistenceError(f"accepted candidate record is missing: {accepted}")
            current = self.materialize_candidate(current, record, workspace / f"accepted-{index:03d}")
        return current

    def save_round_result(self, task_id: str, round_index: int, payload: dict[str, Any]) -> None:
        self._write_once(self._round_root(task_id, round_index) / "round_result.json", payload)

    def cell_root(
        self,
        task_id: str,
        *,
        phase: str,
        round_index: int | None = None,
        candidate_id: str | None = None,
        final_index: int | None = None,
    ) -> Path:
        if phase == "anchor":
            return self.task_root(task_id) / "anchor"
        if phase == "candidate" and round_index is not None and candidate_id is not None:
            return self._candidate_root(task_id, round_index, candidate_id) / "evaluation"
        if phase == "final" and final_index is not None:
            return self.task_root(task_id) / "final" / f"trial-{final_index}"
        raise PersistenceError(f"invalid logical cell identity: {phase}")

    def load_cell_result(self, cell_root: Path, fingerprint: str) -> dict[str, Any] | None:
        path = cell_root / "result_ref.json"
        if not path.exists():
            return None
        payload = self._read_json(path)
        if payload.get("fingerprint") != fingerprint:
            raise PersistenceError(f"logical cell fingerprint mismatch: {path}")
        return payload

    def attempts(self, cell_root: Path, fingerprint: str) -> list[dict[str, Any]]:
        # Attempt indices are no longer capped at two: infrastructure
        # failures are retried, so scan whatever markers exist.
        attempts = []
        for path in sorted(
            (path for path in cell_root.glob("attempt-*.json") if _ATTEMPT_FILE_RE.fullmatch(path.name)),
            key=lambda path: int(_ATTEMPT_FILE_RE.fullmatch(path.name).group(1)),
        ):
            index = int(_ATTEMPT_FILE_RE.fullmatch(path.name).group(1))
            payload = self._read_json(path)
            if payload.get("fingerprint") != fingerprint or payload.get("attempt") != index:
                raise PersistenceError(f"attempt marker fingerprint mismatch: {path}")
            attempts.append(payload)
        return attempts

    def begin_attempt(
        self,
        cell_root: Path,
        *,
        fingerprint: str,
        attempt: int,
        trial_id: str,
    ) -> None:
        self._write_once(
            cell_root / f"attempt-{attempt}.json",
            {
                "fingerprint": fingerprint,
                "attempt": attempt,
                "trial_id": trial_id,
                "status": "started",
            },
        )

    def finish_attempt(
        self,
        cell_root: Path,
        *,
        fingerprint: str,
        attempt: int,
        trial_id: str,
        status: str,
        evaluation: dict[str, Any] | None,
    ) -> None:
        self._write_json_atomic(
            cell_root / f"attempt-{attempt}.json",
            {
                "fingerprint": fingerprint,
                "attempt": attempt,
                "trial_id": trial_id,
                "status": status,
                "evaluation": evaluation,
            },
        )

    def save_cell_result(self, cell_root: Path, payload: dict[str, Any]) -> None:
        self._write_once(cell_root / "result_ref.json", payload)

    def freeze_final(self, task_id: str, portfolio: PortfolioRef) -> PortfolioRef:
        destination = self.task_root(task_id) / "final_portfolio"
        if destination.exists():
            if portfolio_tree_hash(destination) != portfolio.tree_hash:
                raise PersistenceError("existing final portfolio has the wrong tree hash")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".final-", dir=destination.parent) as temporary:
                staged = Path(temporary) / "portfolio"
                shutil.copytree(portfolio.root, staged, copy_function=shutil.copy2)
                os.replace(staged, destination)
        return PortfolioRef.from_directory(
            task_id=task_id,
            root=destination,
            seed_hash=portfolio.seed_hash,
            config_hash=portfolio.config_hash,
            curated_skill_names=portfolio.curated_skill_names,
            generated_skill_names=portfolio.generated_skill_names,
            curated_asset_paths=portfolio.curated_asset_paths,
        )

    def metrics(self, task_id: str) -> tuple[int, int]:
        root = self.task_root(task_id)
        logical = sum(1 for _ in root.rglob("result_ref.json"))
        physical = sum(1 for _ in root.rglob("attempt-*.json"))
        return logical, physical

    def _round_root(self, task_id: str, round_index: int) -> Path:
        return self.task_root(task_id) / "rounds" / f"round-{round_index:03d}"

    def _candidate_root(self, task_id: str, round_index: int, candidate_id: str) -> Path:
        if not candidate_id or "/" in candidate_id or "\\" in candidate_id:
            raise PersistenceError(f"unsafe candidate id: {candidate_id!r}")
        return self._round_root(task_id, round_index) / "candidates" / candidate_id

    def _write_once(self, path: Path, payload: dict[str, Any]) -> None:
        if path.exists():
            if self._read_json(path) != payload:
                raise PersistenceError(f"immutable marker already exists with different content: {path}")
            return
        self._write_json_atomic(path, payload)

    @staticmethod
    def _read_json(path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PersistenceError(f"cannot read JSON marker: {path}") from exc

    @staticmethod
    def _write_json_atomic(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)

    @staticmethod
    def _write_text_atomic(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
