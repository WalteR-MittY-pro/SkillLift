from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from skilllift_eval.runners.skillsbench_adapter import (
    SkillsBenchCommand,
    SkillsBenchSubprocessAdapter,
    extract_oracle_cell,
    find_exact_result_json,
)


AGENTCLAW_ROOT = Path(__file__).resolve().parents[2] / "skilllift"
if str(AGENTCLAW_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENTCLAW_ROOT))

from skilllift.portfolio import PortfolioRef, portfolio_manifest  # noqa: E402
from skilllift.coordinator import CandidateEvaluation, RewardSpec, TrialSpec  # noqa: E402
from skilllift.portfolio import PublicTask  # noqa: E402


SKILLSBENCH_PORTFOLIO_LOADER_VERSION = "skillsbench_portfolio_v3"
_PREBUILT_IMAGE_BUILD_LOCK = threading.Lock()


@dataclass(frozen=True)
class SkillsBenchPortfolioSettings:
    project_root: Path
    tasks_root: Path
    run_root: Path
    python_executable: Path
    model: str
    sandbox: str
    base_url: str
    api_key: str
    patch_source: Path
    agent: str = "openhands-sse"
    prebuilt_image_template: str | None = None
    terminal_threshold: float = 1.0


class SkillsBenchPortfolioAdapter:
    def __init__(
        self,
        settings: SkillsBenchPortfolioSettings,
        *,
        subprocess_adapter: SkillsBenchSubprocessAdapter | None = None,
    ) -> None:
        self.settings = settings
        self.subprocess_adapter = subprocess_adapter or SkillsBenchSubprocessAdapter()
        self.ledger_path = settings.run_root / "usage" / "usage.jsonl"

    def public_task(self, task_id: str) -> PublicTask:
        task_path = self._task_root(task_id) / "task.md"
        if not task_path.is_file():
            raise ValueError(f"SkillsBench task does not exist: {task_id}")
        return PublicTask(task_id, task_path.read_text(encoding="utf-8"), "task.md")

    def seed_portfolio(self, task_id: str) -> PortfolioRef:
        native_root = self._task_root(task_id) / "environment" / "skills"
        if native_root.is_dir():
            root = native_root
        else:
            root = self.settings.run_root / "empty_seeds" / task_id
            root.mkdir(parents=True, exist_ok=True)
        task_hash = hashlib.sha256((self._task_root(task_id) / "task.md").read_bytes()).hexdigest()
        config_hash = _stable_hash(
            {
                "loader_version": SKILLSBENCH_PORTFOLIO_LOADER_VERSION,
                "task_hash": task_hash,
                "model": self.settings.model,
                "sandbox": self.settings.sandbox,
                "patch_hash": _file_hash(self.settings.patch_source),
            }
        )
        return PortfolioRef.from_directory(
            task_id=task_id,
            root=root,
            config_hash=config_hash,
        )

    def reward_spec(self) -> RewardSpec:
        return RewardSpec(
            terminal_threshold=self.settings.terminal_threshold, minimum=0.0, maximum=1.0
        )

    def evaluate(
        self,
        task_id: str,
        portfolio: PortfolioRef,
        trial: TrialSpec,
    ) -> CandidateEvaluation:
        try:
            trial_root, deployment = self._prepare_trial(task_id, portfolio, trial)
            environment_manifest = self._prepare_environment_manifest(trial_root, task_id)
            command = SkillsBenchCommand(
                bench_executable=self.settings.python_executable,
                tasks_dir=self.settings.tasks_root,
                task_ids=(task_id,),
                agent=self.settings.agent,
                model=self.settings.model,
                sandbox=self.settings.sandbox,
                skill_mode="with-skill",
                skills_dir=deployment,
                jobs_dir=trial_root / "jobs",
                environment_manifest=environment_manifest,
            )
            cell, _, result_path = self.subprocess_adapter.execute(
                command,
                project_root=self.settings.project_root,
                task_id=task_id,
                skill_id=trial.candidate_id or trial.phase,
                base_url=self.settings.base_url,
                api_key=self.settings.api_key,
                ledger_path=self.ledger_path,
                usage_context={
                    "usage_record_id": trial.trial_id,
                    "phase": trial.phase,
                    "task_id": task_id,
                    "round_index": trial.round_index,
                    "candidate_id": trial.candidate_id,
                    "final_index": trial.final_index,
                    "attempt": trial.attempt,
                    "portfolio_hash": portfolio.tree_hash,
                    "selected_result": True,
                },
                exact_result=True,
            )
            self._validate_receipts(_receipt_root(result_path), portfolio)
            reward = float(cell["oracle_result"]["rewards"]["reward"])
            return self._commit_result(trial_root, task_id, portfolio, trial, result_path, reward)
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            return CandidateEvaluation.infrastructure_failure(str(exc))

    def recover(
        self,
        task_id: str,
        portfolio: PortfolioRef,
        trial: TrialSpec,
    ) -> CandidateEvaluation | None:
        trial_root = self._trial_root(task_id, trial.trial_id)
        marker = trial_root / "trial_result.json"
        if marker.is_file():
            try:
                return self._evaluation_from_marker(marker, task_id, portfolio, trial)
            except (OSError, ValueError, json.JSONDecodeError):
                return None
        jobs_root = trial_root / "jobs"
        deployment = trial_root / "deployment"
        if not jobs_root.is_dir() or not deployment.is_dir():
            return None
        try:
            result_path = find_exact_result_json(jobs_root, task_id)
            self._validate_receipts(_receipt_root(result_path), portfolio)
            cell = extract_oracle_cell(result_path, task_id, trial.candidate_id or trial.phase)
            reward = float(cell["oracle_result"]["rewards"]["reward"])
            return self._commit_result(trial_root, task_id, portfolio, trial, result_path, reward)
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def _prepare_trial(
        self,
        task_id: str,
        portfolio: PortfolioRef,
        trial: TrialSpec,
    ) -> tuple[Path, Path]:
        trial_root = self._trial_root(task_id, trial.trial_id)
        deployment = trial_root / "deployment"
        deployment_marker = trial_root / "deployment.json"
        marker_payload = {
            "trial_id": trial.trial_id,
            "task_id": task_id,
            "portfolio_hash": portfolio.tree_hash,
            "patch_hash": _file_hash(self.settings.patch_source),
        }
        if deployment.exists():
            if not deployment_marker.is_file() or _read_json(deployment_marker) != marker_payload:
                raise ValueError(f"existing SkillsBench deployment does not match trial {trial.trial_id}")
            return trial_root, deployment
        trial_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".deployment-", dir=trial_root) as temporary:
            staged = Path(temporary) / "skills"
            shutil.copytree(portfolio.root, staged, copy_function=shutil.copy2)
            shutil.copy2(self.settings.patch_source, staged / "oh_skill_patch.py")
            os.replace(staged, deployment)
        _write_json_atomic(deployment_marker, marker_payload)
        return trial_root, deployment

    def _prepare_environment_manifest(self, trial_root: Path, task_id: str) -> Path | None:
        template = self.settings.prebuilt_image_template
        if template is None:
            return None
        if "{task_id}" not in template:
            raise ValueError("prebuilt image template must contain {task_id}")
        image = template.replace("{task_id}", task_id)
        self._ensure_prebuilt_image(task_id, image)
        content = (
            "[environment]\n"
            f"name = {json.dumps(f'{task_id}-prebuilt')}\n"
            f"image = {json.dumps(image)}\n"
        )
        path = trial_root / "environment.toml"
        if path.is_file():
            if path.read_text(encoding="utf-8") != content:
                raise ValueError(f"existing environment manifest does not match trial task {task_id}")
            return path
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
        return path

    def _ensure_prebuilt_image(self, task_id: str, image: str) -> None:
        if self.settings.sandbox != "docker":
            return
        with _PREBUILT_IMAGE_BUILD_LOCK:
            inspected = subprocess.run(
                ["docker", "image", "inspect", image],
                capture_output=True,
                text=True,
                check=False,
            )
            if inspected.returncode == 0:
                return
            environment = (self._task_root(task_id) / "environment").resolve()
            built = subprocess.run(
                ["docker", "build", "--tag", image, str(environment)],
                capture_output=True,
                text=True,
                check=False,
            )
            if built.returncode != 0:
                detail = (built.stderr or built.stdout or "unknown Docker error").strip()
                raise RuntimeError(f"docker build failed for {image}: {detail}")

    def _validate_receipts(self, receipt_root: Path, portfolio: PortfolioRef) -> None:
        deployed = _read_json(receipt_root / "deployed_tree_receipt.json")
        loaded = _read_json(receipt_root / "loaded_skill_receipt.json")
        expected_manifest = list(portfolio_manifest(portfolio.root))
        if deployed.get("tree_hash") != portfolio.tree_hash or deployed.get("files") != expected_manifest:
            raise ValueError("SkillsBench deployed-tree receipt does not match the candidate Portfolio")

        actual_skills = loaded.get("skills")
        if not isinstance(actual_skills, list):
            raise ValueError("SkillsBench loaded-skill receipt is malformed")
        expected = {}
        for skill_md in sorted(portfolio.root.glob("*/SKILL.md")):
            content = skill_md.read_text(encoding="utf-8")
            expected[skill_md.parent.name] = {
                "skill_md_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "source_chars": len(content),
            }
        actual_by_name = {item.get("name"): item for item in actual_skills if isinstance(item, dict)}
        if set(actual_by_name) != set(expected) or len(actual_skills) != len(expected):
            raise ValueError("SkillsBench loaded-skill receipt has missing, duplicate, or unexpected skills")
        for name, values in expected.items():
            actual = actual_by_name[name]
            if any(actual.get(key) != value for key, value in values.items()):
                raise ValueError(f"SkillsBench loaded-skill receipt hash mismatch: {name}")
            if not isinstance(actual.get("injected_chars"), int) or actual["injected_chars"] < values["source_chars"]:
                raise ValueError(f"SkillsBench loaded-skill receipt token context is invalid: {name}")

    def _commit_result(
        self,
        trial_root: Path,
        task_id: str,
        portfolio: PortfolioRef,
        trial: TrialSpec,
        result_path: Path,
        reward: float,
    ) -> CandidateEvaluation:
        result_hash = _file_hash(result_path)
        marker = trial_root / "trial_result.json"
        payload = {
            "trial_id": trial.trial_id,
            "task_id": task_id,
            "portfolio_hash": portfolio.tree_hash,
            "result_path": str(result_path.resolve()),
            "result_hash": result_hash,
            "reward": reward,
        }
        if marker.exists() and _read_json(marker) != payload:
            raise ValueError(f"SkillsBench trial marker conflict: {trial.trial_id}")
        _write_json_atomic(marker, payload)
        return CandidateEvaluation.valid(
            reward,
            result_hash=result_hash,
            artifact_ref=str(marker.resolve()),
            usage_ref=str(self.ledger_path.resolve()),
        )

    def _evaluation_from_marker(
        self,
        marker: Path,
        task_id: str,
        portfolio: PortfolioRef,
        trial: TrialSpec,
    ) -> CandidateEvaluation:
        payload = _read_json(marker)
        expected = (payload.get("trial_id"), payload.get("task_id"), payload.get("portfolio_hash"))
        if expected != (trial.trial_id, task_id, portfolio.tree_hash):
            raise ValueError("SkillsBench trial marker identity mismatch")
        result_path = Path(payload["result_path"])
        if _file_hash(result_path) != payload.get("result_hash"):
            raise ValueError("SkillsBench result hash mismatch")
        cell = extract_oracle_cell(result_path, task_id, trial.candidate_id or trial.phase)
        reward = float(cell["oracle_result"]["rewards"]["reward"])
        if reward != float(payload["reward"]):
            raise ValueError("SkillsBench marker reward mismatch")
        return CandidateEvaluation.valid(
            reward,
            result_hash=payload["result_hash"],
            artifact_ref=str(marker.resolve()),
            usage_ref=str(self.ledger_path.resolve()),
        )

    def _task_root(self, task_id: str) -> Path:
        if not task_id or task_id in {".", ".."} or "/" in task_id or "\\" in task_id:
            raise ValueError(f"unsafe SkillsBench task id: {task_id!r}")
        return self.settings.tasks_root / task_id

    def _trial_root(self, task_id: str, trial_id: str) -> Path:
        if not trial_id or "/" in trial_id or "\\" in trial_id:
            raise ValueError(f"unsafe trial id: {trial_id!r}")
        return self.settings.run_root / "adapter_artifacts" / task_id / trial_id


def _stable_hash(payload: Any) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _receipt_root(result_path: Path) -> Path:
    return result_path.parent / "agent" / "skilllift-skills-receipts"


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload




def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
