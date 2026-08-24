from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

from ..errors import OracleAdapterError
from ..portfolio import PortfolioRef
from ..coordinator import CandidateEvaluation, RewardSpec, TrialSpec
from ..portfolio import PublicTask
from ..schemas import SkillLiftConfig, EvoSkill
from ..task_loader import parse_task_metadata


def build_run_batch_command(task_path: Path, model: str, config: SkillLiftConfig, skill_dir: Path) -> list[str]:
    openclaw_models_config = config.openclaw_models_config_path()
    command = [
        _resolve_python_executable(config.python_executable),
        "eval/run_batch.py",
        "--task",
        str(task_path),
        "--model",
        model,
        "--models-config",
        str(_repo_relative_path(openclaw_models_config).resolve()),
    ]
    command.extend(
        [
            "--rate-limit-retries",
            str(config.oracle_rate_limit_retries),
            "--rate-limit-wait-seconds",
            str(config.oracle_rate_limit_wait_seconds),
            "--skill-dir",
            str(skill_dir),
        ]
    )
    return command


def run_wildclawbench_task(command: list[str], config: SkillLiftConfig) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if config.output_root:
        env["OUTPUT_SUBDIR"] = str(Path(config.output_root).resolve())
    return subprocess.run(command, cwd=_wildclaw_root(config), text=True, capture_output=True, check=False, env=env)


def preflight_validate_oracle_run(task_path: Path, skill_dir: Path, config: SkillLiftConfig) -> None:
    if not skill_dir.exists() or not skill_dir.is_dir():
        raise OracleAdapterError(f"skill_dir is missing or not a directory: {skill_dir}")
    if not (skill_dir / "SKILL.md").exists():
        raise OracleAdapterError(f"skill_dir must point to a single runtime skill containing SKILL.md: {skill_dir}")
    if not task_path.exists():
        raise OracleAdapterError(f"task markdown is missing: {task_path}")
    task_id = str(parse_task_metadata(task_path)["task_id"])
    if "__skilllift_s" not in task_id:
        raise OracleAdapterError("temporary task id must include __skilllift_s")
    openclaw_models_config = config.openclaw_models_config_path()
    if not _repo_relative_path(openclaw_models_config).exists():
        raise OracleAdapterError(f"openclaw_models_config not found: {openclaw_models_config}")
    wildclaw_root = _wildclaw_root(config)
    if not (wildclaw_root / "eval" / "run_batch.py").exists():
        raise OracleAdapterError(f"eval/run_batch.py not found under wildclaw_root: {wildclaw_root}")


def resolve_wildclaw_output_dir(
    task_path: Path,
    output_root: Path | None,
    min_mtime: float,
    config: SkillLiftConfig,
) -> Path:
    task = parse_task_metadata(task_path)
    root = output_root if output_root is not None else _wildclaw_root(config) / "output"
    candidates = [
        root / Path(task_path).parent.name / task["task_id"],
        root / task["category"] / task["task_id"],
    ]
    children: list[Path] = []
    for parent in candidates:
        if parent.is_dir():
            children.extend(path for path in parent.iterdir() if path.is_dir() and path.stat().st_mtime >= min_mtime)
    if not children:
        raise OracleAdapterError(f"No fresh output runs found for task_id={task['task_id']} under {root}")
    return max(children, key=lambda path: path.stat().st_mtime)


def compute_skill_package_hash(skill: EvoSkill) -> str:
    payload = json.dumps(skill.to_dict(), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_models_config_hash(models_config: Path | str) -> str:
    path = _repo_relative_path(models_config)
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evo_skill_to_skill_bundle_dict(
    skill: EvoSkill,
    *,
    task_id: str,
    created_at: str,
    source_artifact_path: str | None = None,
) -> dict[str, Any]:
    skill_id = _skill_id(skill)
    content = _skill_bundle_content(skill)
    return {
        "skill_bundle_id": f"skilllift-{task_id}-{skill.key.token()}",
        "baseline": "skilllift",
        "benchmark_target": "wildclawbench",
        "granularity": "task",
        "source_round": skill.key.token(),
        "source_artifact_path": source_artifact_path,
        "skills": [
            {
                "id": skill_id,
                "title": skill.skill_name,
                "content": content,
                "metadata": {
                    "skilllift_skill_key": skill.key.token(),
                    "entrypoint": skill.entrypoint,
                    "file_names": sorted(skill.files),
                },
            }
        ],
        "created_at": created_at,
    }


def _skill_bundle_content(skill: EvoSkill) -> str:
    parts = [skill.files.get("SKILL.md", "").strip()]
    aux_files = []
    for rel_path, content in sorted(skill.files.items()):
        if rel_path == "SKILL.md":
            continue
        aux_files.append(f"### {rel_path}\n\n```text\n{content.rstrip()}\n```")
    if aux_files:
        parts.extend(["## CoEvo Auxiliary Files", *aux_files])
    if skill.entrypoint:
        parts.append(f"Entrypoint: `{skill.entrypoint}`")
    return "\n\n".join(part for part in parts if part).strip() + "\n"


def _skill_id(skill: EvoSkill) -> str:
    raw = f"{skill.key.token()}-{skill.skill_name}"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-._")
    return safe or skill.key.token()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _wildclaw_root(config: SkillLiftConfig) -> Path:
    raw = config.wildclaw_root or os.environ.get("WILDCLAWBENCH_ROOT", "")
    if raw:
        return Path(raw).expanduser().resolve()
    sibling = _repo_root().parent / "WildClawBench"
    if sibling.exists():
        return sibling.resolve()
    return _repo_root()


def _repo_relative_path(path: Path | str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute() or candidate.exists():
        return candidate
    return _repo_root() / candidate


def _resolve_python_executable(value: str) -> str:
    candidate = Path(value).expanduser()
    if candidate.is_absolute() or len(candidate.parts) == 1:
        return str(candidate)
    return str((_repo_root() / candidate).resolve())


def contains_rate_limit_signal(text: str) -> bool:
    lower = text.lower()
    return any(
        token in lower
        for token in (
            "api rate limit reached",
            "rate limit reached",
            "rate_limit",
            "too many requests",
            "接口请求并发超额",
        )
    )


class WildClawPortfolioAdapter:
    def __init__(
        self,
        *,
        wildclaw_root: Path,
        run_root: Path,
        task_filter: str,
        settings_factory: Callable[[Path], Any],
        runner: Any,
        config_hash: str,
        terminal_threshold: float = 1.0,
    ) -> None:
        self.wildclaw_root = wildclaw_root.resolve()
        self.run_root = run_root.resolve()
        self.task_filter = task_filter
        self.settings_factory = settings_factory
        self.runner = runner
        self.config_hash = config_hash
        self.terminal_threshold = terminal_threshold
        self.records: list[Any] = []
        self._runner_lock = threading.Lock()

    def public_task(self, task_id: str) -> PublicTask:
        task_path, task = self._task()
        if str(task["task_id"]) != task_id:
            raise ValueError(f"WildClaw task identity mismatch: {task['task_id']} != {task_id}")
        from skilllift_eval.benchmarks.wildclaw_public_view import wildclaw_public_task_view

        public = wildclaw_public_task_view(task)
        return PublicTask(task_id, json.dumps(public, ensure_ascii=False, indent=2, sort_keys=True), task_path.name)

    def seed_portfolio(self, task_id: str) -> PortfolioRef:
        _, task = self._task()
        if str(task["task_id"]) != task_id:
            raise ValueError(f"WildClaw task identity mismatch: {task['task_id']} != {task_id}")
        names = _native_skill_names(task.get("skills"))
        if not names:
            raise ValueError(
                "WildClaw task must declare at least one pre-provisioned skill"
            )
        seed_root = self.run_root / "seeds" / task_id / "portfolio"
        manifest_path = seed_root.parent / "source.json"
        source_hashes = {name: _native_skill_hash(self.wildclaw_root, name) for name in names}
        source_payload = {
            "task_id": task_id,
            "config_hash": self.config_hash,
            "skill_names": list(names),
            "source_hashes": source_hashes,
        }
        if seed_root.exists():
            if not manifest_path.is_file() or _read_json_file(manifest_path) != source_payload:
                raise ValueError("WildClaw normalized seed no longer matches native skills or configuration")
        else:
            seed_root.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".seed-", dir=seed_root.parent) as temporary:
                staged = Path(temporary) / "portfolio"
                staged.mkdir()
                for name in names:
                    _copy_native_skill(self.wildclaw_root, name, staged / name)
                os.replace(staged, seed_root)
            _write_json_file(manifest_path, source_payload)
        task_hash = hashlib.sha256(self.public_task(task_id).text.encode("utf-8")).hexdigest()
        return PortfolioRef.from_directory(
            task_id=task_id,
            root=seed_root,
            config_hash=_hash_json(
                {
                    "adapter_config": self.config_hash,
                    "task_hash": task_hash,
                    "source_hashes": source_hashes,
                }
            ),
        )

    def reward_spec(self) -> RewardSpec:
        return RewardSpec(terminal_threshold=self.terminal_threshold, minimum=0.0, maximum=1.0)

    def evaluate(
        self,
        task_id: str,
        portfolio: PortfolioRef,
        trial: TrialSpec,
    ) -> CandidateEvaluation:
        trial_root = self._trial_root(task_id, trial.trial_id)
        try:
            settings = self.settings_factory(trial_root)
            with self._runner_lock:
                result = self.runner.run_portfolio(
                    settings,
                    portfolio.root,
                    skill_hash=portfolio.tree_hash,
                )
            record = result.records[0]
            return self._commit_record(task_id, portfolio, trial, trial_root, record, settings)
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
        settings = self.settings_factory(trial_root)
        if marker.is_file():
            try:
                payload = _read_json_file(marker)
                if (
                    payload.get("trial_id") != trial.trial_id
                    or payload.get("task_id") != task_id
                    or payload.get("portfolio_hash") != portfolio.tree_hash
                ):
                    return None
                from skilllift_eval.schemas import TaskRunRecord

                record = TaskRunRecord.from_dict(
                    _read_json_file(Path(payload["task_run_record_path"]))
                )
                return self._commit_record(task_id, portfolio, trial, trial_root, record, settings)
            except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError):
                return None
        record_paths = list(trial_root.glob("tasks/*/task_run_record.json"))
        if len(record_paths) != 1:
            return None
        try:
            from skilllift_eval.schemas import TaskRunRecord

            record = TaskRunRecord.from_dict(_read_json_file(record_paths[0]))
            return self._commit_record(task_id, portfolio, trial, trial_root, record, settings)
        except (OSError, ValueError, json.JSONDecodeError, TypeError):
            return None

    def _commit_record(
        self,
        task_id: str,
        portfolio: PortfolioRef,
        trial: TrialSpec,
        trial_root: Path,
        record: Any,
        settings: Any,
    ) -> CandidateEvaluation:
        self._validate_record(task_id, portfolio, trial_root, record, settings)
        score_path = Path(record.score_path)
        score = _read_json_file(score_path)
        scores = score.get("scores") if isinstance(score.get("scores"), dict) else {}
        raw_reward = scores.get("overall_score", scores.get("score"))
        if not isinstance(raw_reward, (int, float)):
            raise ValueError(f"WildClaw run did not produce a benchmark reward: {score_path}")
        reward = float(raw_reward)
        result_hash = _file_sha256(score_path)
        record_path = Path(record.raw_output_path).parent / "task_run_record.json"
        marker = trial_root / "trial_result.json"
        payload = {
            "trial_id": trial.trial_id,
            "task_id": task_id,
            "portfolio_hash": portfolio.tree_hash,
            "score_path": str(score_path.resolve()),
            "result_hash": result_hash,
            "reward": reward,
            "task_run_record_path": str(record_path.resolve()),
        }
        if marker.exists() and _read_json_file(marker) != payload:
            raise ValueError(f"WildClaw trial marker conflict: {trial.trial_id}")
        _write_json_file(marker, payload)
        if all(existing.raw_output_path != record.raw_output_path for existing in self.records):
            self.records.append(record)
        return CandidateEvaluation.valid(
            reward,
            result_hash=result_hash,
            artifact_ref=str(marker.resolve()),
            usage_ref=str(record_path.resolve()),
        )

    @staticmethod
    def _validate_record(
        task_id: str,
        portfolio: PortfolioRef,
        trial_root: Path,
        record: Any,
        settings: Any,
    ) -> None:
        if record.task_id != task_id or record.skill_hash != portfolio.tree_hash:
            raise ValueError("WildClaw task record does not match the logical cell")
        if record.status != "succeeded":
            raise ValueError("WildClaw task record is not a successful benchmark result")
        expected_fields = {
            "endpoint_config_hash": settings.endpoint_config_hash,
            "algorithm_param_hash": settings.algorithm_param_hash,
            "benchmark_source_hash": settings.benchmark_source_hash,
            "task_set_hash": settings.task_set_hash,
            "run_config_hash": settings.run_config_hash,
            "provider_model_id": settings.model_endpoint.provider_model_id,
            "task_sample_policy_id": settings.task_sample_policy_id,
        }
        mismatches = [name for name, expected in expected_fields.items() if getattr(record, name) != expected]
        if mismatches:
            raise ValueError(f"WildClaw task record fingerprint mismatch: {mismatches}")
        root = trial_root.resolve()
        paths = [Path(record.raw_output_path), Path(record.score_path), Path(record.loader_manifest_path)]
        if any(not path.resolve().is_relative_to(root) for path in paths):
            raise ValueError("WildClaw task record points outside the logical cell")
        manifest = _read_json_file(Path(record.loader_manifest_path))
        if manifest.get("skill_hash") != portfolio.tree_hash:
            raise ValueError("WildClaw loader manifest does not match the candidate Portfolio")

    def _task(self) -> tuple[Path, dict[str, Any]]:
        from skilllift_eval.runners.wildclaw import resolve_task_path
        from skilllift_eval.runners.wildclaw_engine import run_batch, task_parser

        task_path = resolve_task_path(self.wildclaw_root, self.task_filter)
        task_parser.ROOT_DIR = self.wildclaw_root
        task = run_batch.parse_task_md(task_path)
        task["category"] = task_path.parent.name
        return task_path, task

    def _trial_root(self, task_id: str, trial_id: str) -> Path:
        if not trial_id or "/" in trial_id or "\\" in trial_id:
            raise ValueError(f"unsafe WildClaw trial id: {trial_id!r}")
        return self.run_root / "adapter_artifacts" / task_id / trial_id


def _native_skill_names(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    items = raw if isinstance(raw, list) else str(raw).replace(",", "\n").splitlines()
    names = tuple(str(item).strip() for item in items if str(item).strip())
    if len(set(names)) != len(names):
        raise ValueError("WildClaw task declares duplicate native skills")
    for name in names:
        if name in {".", ".."} or "/" in name or "\\" in name:
            raise ValueError(f"unsafe WildClaw native skill name: {name}")
    return names


def _native_skill_hash(wildclaw_root: Path, name: str) -> str:
    source = wildclaw_root / "skills" / name
    flat = wildclaw_root / "skills" / f"{name}.md"
    if source.is_dir():
        digest = hashlib.sha256()
        for path in sorted(item for item in source.rglob("*") if item.is_file()):
            digest.update(path.relative_to(source).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()
    if flat.is_file():
        return _file_sha256(flat)
    raise ValueError(f"WildClaw native skill does not exist: {name}")


def _copy_native_skill(wildclaw_root: Path, name: str, destination: Path) -> None:
    source = wildclaw_root / "skills" / name
    flat = wildclaw_root / "skills" / f"{name}.md"
    if source.is_dir():
        shutil.copytree(source, destination, copy_function=shutil.copy2)
        return
    if flat.is_file():
        destination.mkdir()
        shutil.copy2(flat, destination / "SKILL.md")
        return
    raise ValueError(f"WildClaw native skill does not exist: {name}")


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_json(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _read_json_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
