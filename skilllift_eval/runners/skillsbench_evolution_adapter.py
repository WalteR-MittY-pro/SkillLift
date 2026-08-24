from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from skilllift_eval.runners.skillsbench_portfolio_adapter import (
    SkillsBenchPortfolioAdapter,
    SkillsBenchPortfolioSettings,
)


AGENTCLAW_ROOT = Path(__file__).resolve().parents[2] / "skilllift"
if str(AGENTCLAW_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENTCLAW_ROOT))

from skilllift.portfolio import PortfolioRef, portfolio_tree_hash  # noqa: E402
from skilllift.coordinator import CandidateEvaluation, TrialSpec  # noqa: E402


@dataclass(frozen=True)
class SkillsBenchEvolutionSettings:
    project_root: Path
    run_root: Path
    tasks_root: Path
    split_path: Path
    python_executable: Path
    model: str
    sandbox: str
    base_url: str
    api_key: str
    patch_source: Path
    agent: str
    framework_model: str
    use_stream: bool = False
    prebuilt_image_template: str | None = None
    algorithm_params: dict[str, Any] | None = None


@dataclass(frozen=True)
class SkillsBenchEvolutionEvaluation:
    evaluation: CandidateEvaluation
    result_path: Path
    result_payload: dict[str, Any]
    public_artifact_root: Path

    @property
    def reward(self) -> float:
        if self.evaluation.reward is None:
            raise RuntimeError(
                self.evaluation.infrastructure_error
                or "SkillsBench evaluation failed"
            )
        return float(self.evaluation.reward)


class SkillsBenchEvolutionAdapter:
    """Evaluate one generated overlay skill with the shared SkillsBench adapter."""

    def __init__(self, adapter: SkillsBenchPortfolioAdapter) -> None:
        self.adapter = adapter
        self._portfolio_lock = threading.Lock()

    def evaluate(
        self,
        task_id: str,
        *,
        overlay_name: str,
        files: dict[str, str],
        trial: TrialSpec,
    ) -> SkillsBenchEvolutionEvaluation:
        portfolio = self._portfolio(task_id, overlay_name, files)
        evaluation = self.adapter.recover(task_id, portfolio, trial)
        if evaluation is None:
            evaluation = self.adapter.evaluate(task_id, portfolio, trial)
        if not evaluation.is_valid or not evaluation.artifact_ref:
            raise RuntimeError(
                evaluation.infrastructure_error
                or "SkillsBench evaluation produced no artifact"
            )
        marker = _read_json(Path(evaluation.artifact_ref))
        result_path = Path(str(marker["result_path"]))
        result_payload = _read_json(result_path)
        public_root = self._public_artifacts(
            task_id, trial.trial_id, result_path, result_payload
        )
        return SkillsBenchEvolutionEvaluation(
            evaluation=evaluation,
            result_path=result_path,
            result_payload=result_payload,
            public_artifact_root=public_root,
        )

    def _portfolio(
        self,
        task_id: str,
        overlay_name: str,
        files: dict[str, str],
    ) -> PortfolioRef:
        _validate_overlay_name(overlay_name)
        normalized = _normalize_files(files, overlay_name)
        seed = self.adapter.seed_portfolio(task_id)
        if overlay_name in seed.curated_skill_names:
            raise ValueError(
                f"generated overlay conflicts with curated skill: {overlay_name}"
            )
        candidate_hash = _stable_hash(
            {
                "seed_hash": seed.tree_hash,
                "overlay_name": overlay_name,
                "files": normalized,
            }
        )
        root = (
            self.adapter.settings.run_root
            / "candidate_portfolios"
            / task_id
            / candidate_hash
        )
        with self._portfolio_lock:
            if not root.exists():
                root.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(
                    prefix=".candidate-", dir=root.parent
                ) as temporary:
                    staged = Path(temporary) / "portfolio"
                    shutil.copytree(seed.root, staged, copy_function=shutil.copy2)
                    overlay_root = staged / overlay_name
                    overlay_root.mkdir()
                    for relative, content in normalized.items():
                        destination = overlay_root / relative
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_text(content, encoding="utf-8")
                    os.replace(staged, root)
        portfolio = PortfolioRef.from_directory(
            task_id=task_id,
            root=root,
            seed_hash=seed.seed_hash,
            config_hash=seed.config_hash,
            curated_skill_names=seed.curated_skill_names,
            generated_skill_names={overlay_name},
            curated_asset_paths=seed.curated_asset_paths,
        )
        if portfolio.tree_hash != portfolio_tree_hash(root):
            raise ValueError(
                "SkillsBench candidate portfolio changed after materialization"
            )
        return portfolio

    def _public_artifacts(
        self,
        task_id: str,
        trial_id: str,
        result_path: Path,
        payload: dict[str, Any],
    ) -> Path:
        root = self.adapter.settings.run_root / "public_artifacts" / task_id / trial_id
        if root.exists():
            return root
        root.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".public-", dir=root.parent) as temporary:
            staged = Path(temporary) / "artifacts"
            staged.mkdir()
            source = result_path.parent / "artifacts"
            if source.is_dir():
                shutil.copytree(source, staged / "outputs", copy_function=shutil.copy2)
            public_result = {
                key: payload[key]
                for key in (
                    "task_name",
                    "n_tool_calls",
                    "n_skill_invocations",
                    "started_at",
                    "finished_at",
                )
                if key in payload
            }
            (staged / "run_summary.json").write_text(
                json.dumps(public_result, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            os.replace(staged, root)
        return root


def build_evolution_adapter(
    settings: SkillsBenchEvolutionSettings,
    task_root: Path,
) -> SkillsBenchEvolutionAdapter:
    return SkillsBenchEvolutionAdapter(
        SkillsBenchPortfolioAdapter(
            SkillsBenchPortfolioSettings(
                project_root=settings.project_root,
                tasks_root=settings.tasks_root,
                run_root=task_root,
                python_executable=settings.python_executable,
                model=settings.model,
                sandbox=settings.sandbox,
                base_url=settings.base_url,
                api_key=settings.api_key,
                patch_source=settings.patch_source,
                agent=settings.agent,
                prebuilt_image_template=settings.prebuilt_image_template,
            )
        )
    )


def _normalize_files(files: dict[str, str], overlay_name: str) -> dict[str, str]:
    if "SKILL.md" not in files:
        raise ValueError("generated SkillsBench overlay must contain SKILL.md")
    normalized: dict[str, str] = {}
    for raw_path, raw_content in files.items():
        path = PurePosixPath(str(raw_path).replace("\\", "/"))
        if (
            path.is_absolute()
            or not path.parts
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError(f"unsafe generated skill path: {raw_path!r}")
        if not isinstance(raw_content, str):
            raise ValueError(f"generated skill content must be text: {raw_path}")
        content = raw_content
        if path.as_posix() == "SKILL.md":
            content = _skillsbench_skill_markdown(content, overlay_name)
        normalized[path.as_posix()] = (
            content if content.endswith("\n") else content + "\n"
        )
    return dict(sorted(normalized.items()))


def _validate_overlay_name(name: str) -> None:
    if not name or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in name
    ):
        raise ValueError(f"invalid generated overlay name: {name!r}")


def _skillsbench_skill_markdown(content: str, name: str) -> str:
    body = content.strip()
    if body.startswith("---\n") and "\n---\n" in body[4:]:
        body = body.split("\n---\n", 1)[1].strip()
    if not body:
        raise ValueError("generated SkillsBench SKILL.md body must not be empty")
    return (
        "---\n"
        f"name: {name}\n"
        "description: Evolved task-solving guidance for the current SkillsBench task.\n"
        "triggers:\n"
        "  - Use this skill when completing the current benchmark task.\n"
        "---\n\n"
        f"{body}\n"
    )


def _stable_hash(payload: Any) -> str:
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload
