from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from skilllift_eval.runners.skillsbench_adapter import (
    SkillsBenchCommand,
    SkillsBenchSubprocessAdapter,
    directory_hash,
)
from skilllift_eval.runners.skillsbench_report import append_usage_record, summarize_usage


def aggregate_oracle_matrix(
    cells: list[dict[str, Any]],
    task_ids: tuple[str, ...] | list[str],
    skill_ids: tuple[str, ...] | list[str],
) -> dict[str, Any]:
    expected = {(task_id, skill_id) for task_id in task_ids for skill_id in skill_ids}
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for cell in cells:
        key = (str(cell.get("task_id")), str(cell.get("skill_id")))
        if key in indexed:
            raise ValueError(f"duplicate oracle matrix cell: {key}")
        indexed[key] = cell
    if set(indexed) != expected:
        raise ValueError(
            f"oracle matrix coverage mismatch; missing={sorted(expected - set(indexed))}, extra={sorted(set(indexed) - expected)}"
        )

    rewards = {
        key: _cell_reward(cell)
        for key, cell in indexed.items()
    }
    means = {
        skill_id: sum(rewards[(task_id, skill_id)] for task_id in task_ids) / len(task_ids)
        for skill_id in skill_ids
    }
    aggregate_groups = _rank_groups(means)
    aggregate_by_skill = {
        skill_id: {
            "mean_reward": means[skill_id],
            "rank_group": next(index for index, group in enumerate(aggregate_groups, start=1) if skill_id in group),
        }
        for skill_id in skill_ids
    }
    per_task_analysis = {}
    for task_id in task_ids:
        task_scores = {skill_id: rewards[(task_id, skill_id)] for skill_id in skill_ids}
        groups = _rank_groups(task_scores)
        per_task_analysis[task_id] = {
            "rank_groups": groups,
            "no_signal": len(set(task_scores.values())) == 1,
        }
    ordered_cells = [indexed[(task_id, skill_id)] for task_id in task_ids for skill_id in skill_ids]
    return {
        "oracle_matrix": ordered_cells,
        "aggregate_by_skill": aggregate_by_skill,
        "per_task_analysis": per_task_analysis,
    }


def select_winner(
    oracle_evidence: dict[str, Any],
    verifier_scores: dict[str, float],
    skill_hashes: dict[str, str],
) -> str:
    matrix = oracle_evidence["oracle_matrix"]
    aggregate = oracle_evidence["aggregate_by_skill"]
    minimums = {
        skill_id: min(
            _cell_reward(cell) for cell in matrix if cell["skill_id"] == skill_id
        )
        for skill_id in aggregate
    }
    missing = set(aggregate) - set(verifier_scores) | (set(aggregate) - set(skill_hashes))
    if missing:
        raise ValueError(f"winner inputs missing skills: {sorted(missing)}")
    return min(
        aggregate,
        key=lambda skill_id: (
            -float(aggregate[skill_id]["mean_reward"]),
            -minimums[skill_id],
            -float(verifier_scores[skill_id]),
            skill_hashes[skill_id],
        ),
    )


def _rank_groups(scores: dict[str, float]) -> list[list[str]]:
    groups: list[list[str]] = []
    for skill_id in sorted(scores, key=lambda item: (-scores[item], item)):
        if groups and scores[groups[-1][0]] == scores[skill_id]:
            groups[-1].append(skill_id)
        else:
            groups.append([skill_id])
    return groups


def _cell_reward(cell: dict[str, Any]) -> float:
    try:
        reward = float(cell["oracle_result"]["rewards"]["reward"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("oracle matrix cell has invalid reward") from exc
    if not 0.0 <= reward <= 1.0:
        raise ValueError(f"oracle reward outside [0,1]: {reward}")
    return reward


def skill_payload_hash(skill_payload: dict[str, Any]) -> str:
    canonical = json.dumps(skill_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_valid_marker(path: Path, config_hash: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("config_hash") != config_hash:
        return None
    skills = payload.get("skills")
    hashes = payload.get("skill_hashes")
    if not isinstance(skills, dict) or not isinstance(hashes, dict) or set(skills) != set(hashes):
        return None
    if any(skill_payload_hash(skills[key]) != hashes[key] for key in skills):
        return None
    return payload


@dataclass(frozen=True)
class SkillLiftSkillsBenchSettings:
    project_root: Path
    run_root: Path
    tasks_root: Path
    python_executable: Path
    model: str
    sandbox: str
    trials: int
    base_url_env: str
    api_key_env: str
    skilllift: dict[str, Any]
    resume: bool = False


class SkillLiftSkillsBenchRunner:
    def __init__(
        self,
        settings: SkillLiftSkillsBenchSettings,
        *,
        adapter: SkillsBenchSubprocessAdapter | None = None,
    ) -> None:
        self.settings = settings
        self.adapter = adapter or SkillsBenchSubprocessAdapter()
        self.ledger_path = settings.run_root / "usage" / "usage.jsonl"
        self._usage_context: dict[str, Any] = {}
        self._load_skilllift()
        self.base_url = _required_env(settings.base_url_env)
        self.api_key = _required_env(settings.api_key_env)
        self._create_llm_clients()

    def train(self, split: Any, categories: list[str]) -> dict[str, Any]:
        batch_results: dict[str, dict[str, Any]] = {}
        for category in categories:
            domain = split.domains[category]
            category_results = []
            for batch in domain.batches:
                result = self._train_batch(category, batch)
                category_results.append(result)
                batch_results[f"{category}/{batch.batch_id}"] = result
            winner = (
                self._fusion_skill(category, category_results, domain)
                if len(category_results) > 1
                else self._skill_from_dict(category_results[0]["winner_skill"])
            )
            self._write_final_skill(category, winner)
        summary = {
            "status": "completed",
            "categories": categories,
            "batches": batch_results,
            "usage": summarize_usage(self.ledger_path),
        }
        write_json_atomic(self.settings.run_root / "train_summary.json", summary)
        return summary

    def test(self, split: Any, categories: list[str]) -> dict[str, Any]:
        results = []
        for category in categories:
            final_root = self.settings.run_root / "final_skills" / category
            skill_hash = directory_hash(final_root)
            for task in split.domains[category].test:
                score_path = self.settings.run_root / "test" / category / task.task_id / "score.json"
                config_hash = self._fingerprint({"phase": "test", "category": category, "task_id": task.task_id})
                existing = _load_score_marker(score_path, config_hash, skill_hash) if self.settings.resume else None
                if existing is not None:
                    results.append(existing)
                    continue
                trial_rows = []
                for trial in range(self.settings.trials):
                    trial_root = score_path.parent / "jobs" / f"trial-{trial}"
                    reused = None
                    if self.settings.resume and trial_root.exists():
                        command = self._command((task.task_id,), final_root, _next_attempt_dir(trial_root))
                        reused = self.adapter.reuse(
                            trial_root,
                            command,
                            project_root=self.settings.project_root,
                            task_id=task.task_id,
                            skill_id=f"final-{category}",
                            ledger_path=self.ledger_path,
                            usage_context={
                                "usage_record_id": f"{config_hash}-trial-{trial}-reuse",
                                "phase": "test",
                                "category": category,
                                "task_id": task.task_id,
                                "trial": trial,
                                "selected_result": True,
                            },
                        )
                    if reused is not None:
                        cell, _, result_path = reused
                    else:
                        attempt_dir = _next_attempt_dir(trial_root)
                        command = self._command((task.task_id,), final_root, attempt_dir)
                        cell, _, result_path = self.adapter.execute(
                            command,
                            project_root=self.settings.project_root,
                            task_id=task.task_id,
                            skill_id=f"final-{category}",
                            base_url=self.base_url,
                            api_key=self.api_key,
                            ledger_path=self.ledger_path,
                            usage_context={
                                "usage_record_id": f"{config_hash}-trial-{trial}-{attempt_dir.name}",
                                "phase": "test",
                                "category": category,
                                "task_id": task.task_id,
                                "trial": trial,
                                "selected_result": True,
                            },
                        )
                    trial_rows.append(
                        {"trial": trial, "result_path": str(result_path), "oracle_result": cell["oracle_result"]}
                    )
                score = sum(float(row["oracle_result"]["rewards"]["reward"]) for row in trial_rows) / len(trial_rows)
                payload = {
                    "config_hash": config_hash,
                    "skill_hash": skill_hash,
                    "category": category,
                    "task_id": task.task_id,
                    "trials": trial_rows,
                    "mean_reward": score,
                }
                write_json_atomic(score_path, payload)
                results.append(payload)
        summary = {"status": "completed", "tasks": results, "usage": summarize_usage(self.ledger_path)}
        write_json_atomic(self.settings.run_root / "test_summary.json", summary)
        return summary

    def _train_batch(self, category: str, batch: Any) -> dict[str, Any]:
        batch_root = self.settings.run_root / "train" / category / batch.batch_id
        config_hash = self._fingerprint(
            {"phase": "train", "category": category, "batch_id": batch.batch_id, "tasks": list(batch.task_ids)}
        )
        batch_marker = batch_root / "batch_result.json"
        if self.settings.resume:
            existing = load_valid_marker(batch_marker, config_hash)
            if existing is not None:
                return existing

        task = self._batch_task(category, batch.task_ids)
        skills = None
        receipt = None
        completed_rounds: list[dict[str, Any]] = []
        for outer_round in range(int(self.settings.skilllift["outer_rounds"])):
            round_path = batch_root / f"round-{outer_round}" / "round_result.json"
            marker = load_valid_marker(round_path, config_hash) if self.settings.resume else None
            if marker is not None:
                completed_rounds.append(marker)
                skills = {self._skill_key(token): self._skill_from_dict(data) for token, data in marker["skills"].items()}
                receipt = self._receipt_from_dict(marker["receipt"])
                if marker.get("stop_reason") == "oracle_threshold":
                    break
                continue

            if skills is None or receipt is None:
                self._usage_context = {"phase": "train", "category": category, "batch": batch.batch_id, "outer_round": outer_round}
                receipt = self._generate_initial_receipt(task)
                skills = self._initialize_skills(task)
            elif outer_round > 0:
                skills = self._run_mode_a(task, skills, receipt)

            verifier_scores = self._score_skills(task, skills, receipt, mode="mode_b")
            oracle_evidence, result_paths = self._run_mode_b(
                category, batch.batch_id, outer_round, batch.task_ids, skills, batch_root
            )
            oracle_scores, oracle_rank, per_task_rankings = self._oracle_schema_views(
                oracle_evidence, skills
            )
            verifier_rank = self._rank_verifier(verifier_scores)
            alignment = _pairwise_rank_alignment(
                verifier_scores, oracle_evidence["aggregate_by_skill"]
            )
            revision_input = self._revision_input(
                task,
                receipt,
                skills,
                verifier_scores,
                oracle_scores,
                verifier_rank,
                oracle_rank,
                alignment,
                per_task_rankings,
                oracle_evidence,
            )
            if alignment < float(self.settings.skilllift["rank_alignment_threshold"]):
                self._usage_context = {
                    "phase": "mode_b",
                    "category": category,
                    "batch": batch.batch_id,
                    "outer_round": outer_round,
                }
                receipt = self._revise_receipt(
                    revision_input,
                    batch_root / f"round-{outer_round}" / "rubricator",
                )
            skill_payloads = {key.token(): skill.to_dict() for key, skill in skills.items()}
            hashes = {token: skill_payload_hash(payload) for token, payload in skill_payloads.items()}
            verifier_values = {key.token(): score.normalized_score for key, score in verifier_scores.items()}
            winner_id = select_winner(oracle_evidence, verifier_values, hashes)
            winner_score = oracle_evidence["aggregate_by_skill"][winner_id]["mean_reward"]
            stop_reason = "oracle_threshold" if winner_score >= float(self.settings.skilllift["oracle_threshold"]) else None
            marker = {
                "config_hash": config_hash,
                "category": category,
                "batch_id": batch.batch_id,
                "outer_round": outer_round,
                "skills": skill_payloads,
                "skill_hashes": hashes,
                "receipt": receipt.to_dict(),
                "verifier_scores": {key.token(): value.to_dict() for key, value in verifier_scores.items()},
                "oracle_evidence": oracle_evidence,
                "rank_alignment": alignment,
                "winner_skill_id": winner_id,
                "winner_oracle_score": winner_score,
                "result_paths": result_paths,
                "stop_reason": stop_reason,
            }
            write_json_atomic(round_path, marker)
            completed_rounds.append(marker)
            if stop_reason:
                break

        best_round = min(completed_rounds, key=_round_winner_sort_key)
        winner_id = best_round["winner_skill_id"]
        final = {
            "config_hash": config_hash,
            "category": category,
            "batch_id": batch.batch_id,
            "skills": best_round["skills"],
            "skill_hashes": best_round["skill_hashes"],
            "winner_skill_id": winner_id,
            "winner_skill": best_round["skills"][winner_id],
            "winner_oracle_score": best_round["winner_oracle_score"],
            "completed_rounds": len(completed_rounds),
            "stop_reason": best_round.get("stop_reason") or "outer_rounds_exhausted",
        }
        write_json_atomic(batch_marker, final)
        return final

    def _run_mode_a(self, task: Any, skills: dict[Any, Any], receipt: Any) -> dict[Any, Any]:
        current = skills
        scores = self._score_skills(task, current, receipt, mode="mode_a")
        threshold = float(self.settings.skilllift["mode_a_min_score_threshold"])
        for _ in range(int(self.settings.skilllift["mode_a_iters"])):
            if all(score.normalized_score >= threshold for score in scores.values()):
                break
            accepted: dict[Any, Any] = {}
            accepted_scores: dict[Any, Any] = {}
            changed = False
            for key, skill in sorted(current.items()):
                old_score = scores[key]
                if old_score.normalized_score >= threshold:
                    accepted[key] = skill
                    accepted_scores[key] = old_score
                    continue
                candidate = self._update_skill(task, skill, receipt, old_score)
                if candidate.key == skill.key:
                    accepted[key] = skill
                    accepted_scores[key] = old_score
                    continue
                candidate_score = self._score_one(task, candidate, receipt, mode="mode_a")
                if candidate_score.normalized_score >= old_score.normalized_score:
                    accepted[candidate.key] = candidate
                    accepted_scores[candidate.key] = candidate_score
                    changed = True
                else:
                    accepted[key] = skill
                    accepted_scores[key] = old_score
            current = accepted
            scores = accepted_scores
            if not changed:
                break
        return current

    def _run_mode_b(
        self,
        category: str,
        batch_id: str,
        outer_round: int,
        task_ids: tuple[str, ...],
        skills: dict[Any, Any],
        batch_root: Path,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        cells = []
        result_paths = {}
        round_root = batch_root / f"round-{outer_round}"
        for key, skill in sorted(skills.items()):
            candidate_root = round_root / "candidates" / key.token()
            if candidate_root.exists():
                shutil.rmtree(candidate_root)
            candidate_root.mkdir(parents=True)
            self._materialize(skill, candidate_root)
            for task_id in task_ids:
                attempt_dir = _next_attempt_dir(round_root / "jobs" / key.token() / task_id)
                command = self._command((task_id,), candidate_root, attempt_dir)
                execution_id = f"{category}-{batch_id}-r{outer_round}-{key.token()}-{task_id}-{attempt_dir.name}"
                cell, _, result_path = self.adapter.execute(
                    command,
                    project_root=self.settings.project_root,
                    task_id=task_id,
                    skill_id=key.token(),
                    base_url=self.base_url,
                    api_key=self.api_key,
                    ledger_path=self.ledger_path,
                    usage_context={
                        "usage_record_id": execution_id,
                        "phase": "mode_b",
                        "category": category,
                        "batch": batch_id,
                        "outer_round": outer_round,
                        "skill_hash": directory_hash(candidate_root),
                        "task_id": task_id,
                        "attempt": int(attempt_dir.name.split("-")[-1]),
                        "selected_result": True,
                    },
                )
                cells.append(cell)
                result_paths[f"{task_id}/{key.token()}"] = str(result_path)
        return aggregate_oracle_matrix(cells, task_ids, [key.token() for key in sorted(skills)]), result_paths

    def _batch_task(self, category: str, task_ids: tuple[str, ...]) -> Any:
        from skilllift.schemas import TaskSpec

        sections = []
        paths = []
        for task_id in task_ids:
            path = self.settings.tasks_root / task_id / "task.md"
            text = path.read_text(encoding="utf-8")
            body = text.split("---", 2)[2].lstrip() if text.startswith("---") else text
            sections.append(f"# Task: {task_id}\n{body.rstrip()}")
            paths.append(str(path))
        return TaskSpec(category, "\n\n".join(sections) + "\n", ",".join(paths))

    def _command(self, task_ids: tuple[str, ...], skills_dir: Path, jobs_dir: Path) -> SkillsBenchCommand:
        return SkillsBenchCommand(
            bench_executable=self.settings.python_executable,
            tasks_dir=self.settings.tasks_root,
            task_ids=task_ids,
            agent="openhands-sse",
            model=self.settings.model,
            sandbox=self.settings.sandbox,
            skill_mode="with-skill",
            skills_dir=skills_dir,
            jobs_dir=jobs_dir,
        )

    def _fingerprint(self, context: dict[str, Any]) -> str:
        payload = {"skilllift": self.settings.skilllift, "model": self.settings.model, "sandbox": self.settings.sandbox, **context}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def _load_skilllift(self) -> None:
        path = str(self.settings.project_root / "skilllift")
        if path not in sys.path:
            sys.path.insert(0, path)

    def _create_llm_clients(self) -> None:
        from skilllift.llm_client import LLMClient

        def usage_callback(event: dict[str, Any]) -> None:
            usage = event["usage"]
            prompt = usage.get("prompt_tokens")
            completion = usage.get("completion_tokens")
            total = usage.get("total_tokens")
            record = {
                "usage_record_id": _next_usage_record_id(
                    self.ledger_path,
                    f"framework-{event['role']}-{event['request_id']}-{self._fingerprint(self._usage_context)}",
                ),
                "source": "framework",
                "role": event["role"],
                **self._usage_context,
                "input_tokens": prompt if isinstance(prompt, (int, float)) else None,
                "output_tokens": completion if isinstance(completion, (int, float)) else None,
                "cache_read_tokens": usage.get("cached_tokens"),
                "cache_creation_tokens": None,
                "total_tokens": total if isinstance(total, (int, float)) else None,
                "cost_usd": usage.get("cost_usd"),
                "usage_source": "provider_response",
                "breakdown_available": isinstance(prompt, (int, float)) and isinstance(completion, (int, float)),
                "selected_result": True,
            }
            append_usage_record(self.ledger_path, record)

        framework_model = self.settings.model.split("/", 1)[-1]
        self.sg_client = LLMClient(self.base_url, self.api_key, framework_model, role="skill_generator", usage_callback=usage_callback)
        self.rubricator_client = LLMClient(self.base_url, self.api_key, framework_model, role="rubricator", usage_callback=usage_callback)
        self.verifier_client = LLMClient(self.base_url, self.api_key, framework_model, role="verifier", usage_callback=usage_callback)

    def _initialize_skills(self, task: Any) -> dict[Any, Any]:
        from skilllift.schemas import SkillLiftConfig
        from skilllift.baselines.sg import initialize_skill_group

        config = SkillLiftConfig(**self.settings.skilllift, use_llm=True)
        skills = initialize_skill_group(task, config, task.task_description, self.sg_client)
        if any(skill.metadata.get("fallback") for skill in skills.values()):
            raise RuntimeError("agent_skill generation fell back instead of producing a valid package")
        return skills

    def _generate_initial_receipt(self, task: Any) -> Any:
        from skilllift.rubricator import generate_initial_receipt

        receipt = generate_initial_receipt(task, task.task_description, self.rubricator_client)
        if receipt.metadata.get("llm_initial_failed"):
            raise RuntimeError("initial Rubricator response was invalid")
        return receipt

    def _score_skills(self, task: Any, skills: dict[Any, Any], receipt: Any, *, mode: str) -> dict[Any, Any]:
        from skilllift.verifier import score_skills

        return score_skills(task, skills, receipt, self.verifier_client, mode=mode, skill_format="agent_skill")

    def _score_one(self, task: Any, skill: Any, receipt: Any, *, mode: str) -> Any:
        from skilllift.verifier import score_skill

        return score_skill(task, skill, receipt, self.verifier_client, mode=mode, skill_format="agent_skill")

    def _update_skill(self, task: Any, skill: Any, receipt: Any, score: Any) -> Any:
        from skilllift.baselines.sg import update_skill

        return update_skill(task, skill, receipt, score, self.sg_client, skill_format="agent_skill")

    def _materialize(self, skill: Any, target: Path) -> Path:
        from skilllift.baselines.skill_package import evo_skill_to_runtime_dir

        return evo_skill_to_runtime_dir(skill, target, skill_format="agent_skill")

    def _rank_verifier(self, scores: dict[Any, Any]) -> list[Any]:
        from skilllift.ranking import rank_verifier_scores

        return rank_verifier_scores(scores)

    def _oracle_schema_views(self, evidence: dict[str, Any], skills: dict[Any, Any]) -> tuple[dict[Any, Any], list[Any], list[Any]]:
        from skilllift.schemas import OracleScore, TaskRanking

        by_token = {key.token(): key for key in skills}
        oracle_scores = {}
        for token, aggregate in evidence["aggregate_by_skill"].items():
            key = by_token[token]
            oracle_scores[key] = OracleScore(
                key,
                float(aggregate["mean_reward"]),
                int(float(aggregate["mean_reward"]) >= float(self.settings.skilllift["oracle_threshold"])),
                rank=int(aggregate["rank_group"]),
            )
        oracle_rank = sorted(oracle_scores, key=lambda key: (oracle_scores[key].rank, key))
        rankings = []
        for task_id, analysis in evidence["per_task_analysis"].items():
            scores = {}
            for cell in evidence["oracle_matrix"]:
                if cell["task_id"] != task_id:
                    continue
                key = by_token[cell["skill_id"]]
                reward = _cell_reward(cell)
                scores[key] = OracleScore(key, reward, int(reward >= float(self.settings.skilllift["oracle_threshold"])))
            rank = [by_token[token] for group in analysis["rank_groups"] for token in group]
            rankings.append(TaskRanking(task_id, "skillsbench", rank, scores, not analysis["no_signal"], "all candidates tied" if analysis["no_signal"] else None))
        return oracle_scores, oracle_rank, rankings

    def _revision_input(self, task: Any, receipt: Any, skills: dict[Any, Any], verifier_scores: dict[Any, Any], oracle_scores: dict[Any, Any], verifier_rank: list[Any], oracle_rank: list[Any], alignment: float, rankings: list[Any], evidence: dict[str, Any]) -> Any:
        from skilllift.schemas import ReceiptRevisionInput

        return ReceiptRevisionInput(
            task,
            receipt,
            skills,
            verifier_scores,
            oracle_scores,
            verifier_rank,
            oracle_rank,
            alignment,
            per_task_rankings=rankings,
            oracle_evidence=evidence,
        )

    def _revise_receipt(self, revision_input: Any, store_root: Path) -> Any:
        from skilllift.persistence import ExperimentStore
        from skilllift.baselines.prompts import build_rubricator_revision_prompt
        from skilllift.rubricator import (
            build_skill_evidence_package,
            normalize_receipt_candidate,
            record_revision_attempt,
            validate_receipt,
        )

        evidence = build_skill_evidence_package(
            revision_input.task,
            revision_input.skills,
            revision_input.verifier_scores,
            revision_input.oracle_scores,
        )
        system, user = build_rubricator_revision_prompt(revision_input, evidence)
        payload = self.rubricator_client.call_json(system, user, temperature=0.0)
        candidate = normalize_receipt_candidate(payload, revision_input.receipt)
        report = validate_receipt(candidate)
        record_revision_attempt(
            ExperimentStore(store_root),
            revision_input.receipt,
            candidate,
            report,
            0,
            {"prompt.txt": f"{system}\n\n{user}", "parsed_candidate.json": json.dumps(payload, indent=2)},
            {"decision": "accepted" if report.ok else "round_failed", "temperature": 0.0},
        )
        if not report.ok:
            raise RuntimeError(f"Rubricator response failed validation: {report.error_codes}")
        return candidate

    def _skill_key(self, token: str) -> Any:
        from skilllift.schemas import SkillKey

        return SkillKey.from_token(token)

    def _skill_from_dict(self, payload: dict[str, Any]) -> Any:
        from skilllift.schemas import EvoSkill

        return EvoSkill.from_dict(payload)

    def _receipt_from_dict(self, payload: dict[str, Any]) -> Any:
        from skilllift.schemas import Receipt

        return Receipt.from_dict(payload)

    def _write_final_skill(self, category: str, skill: Any) -> None:
        root = self.settings.run_root / "final_skills" / category
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True)
        self._materialize(skill, root)

    def _fusion_skill(self, category: str, batch_results: list[dict[str, Any]], domain: Any) -> Any:
        from skilllift.schemas import EvoSkill, SkillKey
        from skilllift.baselines.skill_package import parse_skill_package, validate_agent_skill_name

        winners = [result["winner_skill"] for result in batch_results]
        task_ids = [task_id for batch in domain.batches for task_id in batch.task_ids]
        task = self._batch_task(category, tuple(task_ids))
        runtime_name = f"skilllift-{category}"
        system = "Fuse two agent skills into one complete multi-file agent_skill package. Return one JSON object only."
        user = json.dumps(
            {
                "runtime_name": runtime_name,
                "task": task.to_dict(),
                "winners": winners,
                "requirements": ["preserve complementary workflows", "do not include test tasks", "return package_mode full"],
            },
            ensure_ascii=False,
        )
        self._usage_context = {"phase": "fusion", "category": category}
        payload = self.sg_client.call_json(system, user, temperature=0.0)
        package = parse_skill_package(payload, skill_format="agent_skill")
        validate_agent_skill_name(package, runtime_name)
        return EvoSkill(SkillKey(0, 1), runtime_name, package.files, package.entrypoint, {**package.metadata, "strategy": "fusion"})


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"missing required environment variable: {name}")
    return value


def _next_attempt_dir(base: Path) -> Path:
    attempt = 1
    while (base / f"attempt-{attempt}").exists():
        attempt += 1
    return base / f"attempt-{attempt}"


def _load_score_marker(path: Path, config_hash: str, skill_hash: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if payload.get("config_hash") == config_hash and payload.get("skill_hash") == skill_hash else None


def _round_winner_sort_key(payload: dict[str, Any]) -> tuple[float, float, str]:
    winner = payload["winner_skill_id"]
    cells = [cell for cell in payload["oracle_evidence"]["oracle_matrix"] if cell["skill_id"] == winner]
    return (
        -float(payload["winner_oracle_score"]),
        -min(_cell_reward(cell) for cell in cells),
        payload["skill_hashes"][winner],
    )


def _pairwise_rank_alignment(verifier_scores: dict[Any, Any], aggregate: dict[str, Any]) -> float:
    keys = list(verifier_scores)
    agreed = 0
    compared = 0
    for left_index, left in enumerate(keys):
        for right in keys[left_index + 1 :]:
            local_delta = verifier_scores[left].normalized_score - verifier_scores[right].normalized_score
            oracle_delta = aggregate[left.token()]["mean_reward"] - aggregate[right.token()]["mean_reward"]
            if local_delta == 0 or oracle_delta == 0:
                continue
            compared += 1
            agreed += int((local_delta > 0) == (oracle_delta > 0))
    return 1.0 if compared == 0 else agreed / compared


def _next_usage_record_id(path: Path, base: str) -> str:
    existing = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                existing.add(str(json.loads(line)["usage_record_id"]))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    attempt = 1
    while f"{base}-attempt-{attempt}" in existing:
        attempt += 1
    return f"{base}-attempt-{attempt}"
