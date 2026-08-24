from __future__ import annotations

import json
from pathlib import Path

import pytest

from skilllift_eval.runners.skilllift_skillsbench import (
    SkillLiftSkillsBenchRunner,
    SkillLiftSkillsBenchSettings,
    aggregate_oracle_matrix,
    select_winner,
)
from skilllift_eval.skillsbench_cli import load_and_validate_split


TASKS = ("task-1", "task-2")
SKILLS = ("s000_v001", "s001_v001")


def _cell(task_id: str, skill_id: str, reward: float) -> dict:
    return {
        "task_id": task_id,
        "skill_id": skill_id,
        "oracle_result": {
            "rewards": {"reward": reward},
            "n_tool_calls": 1,
            "n_skill_invocations": 1,
        },
    }


def test_oracle_matrix_derives_aggregate_and_per_task_rank_groups() -> None:
    evidence = aggregate_oracle_matrix(
        [
            _cell("task-1", "s000_v001", 1.0),
            _cell("task-2", "s000_v001", 0.5),
            _cell("task-1", "s001_v001", 0.5),
            _cell("task-2", "s001_v001", 0.5),
        ],
        TASKS,
        SKILLS,
    )

    assert evidence["aggregate_by_skill"]["s000_v001"] == {"mean_reward": 0.75, "rank_group": 1}
    assert evidence["aggregate_by_skill"]["s001_v001"] == {"mean_reward": 0.5, "rank_group": 2}
    assert evidence["per_task_analysis"]["task-1"]["rank_groups"] == [["s000_v001"], ["s001_v001"]]
    assert evidence["per_task_analysis"]["task-2"]["no_signal"] is True


def test_oracle_matrix_rejects_missing_or_duplicate_cells() -> None:
    cells = [
        _cell("task-1", "s000_v001", 1.0),
        _cell("task-2", "s000_v001", 1.0),
        _cell("task-1", "s001_v001", 1.0),
    ]
    with pytest.raises(ValueError, match="coverage"):
        aggregate_oracle_matrix(cells, TASKS, SKILLS)

    cells.append(_cell("task-1", "s001_v001", 1.0))
    cells.append(_cell("task-1", "s001_v001", 1.0))
    with pytest.raises(ValueError, match="duplicate"):
        aggregate_oracle_matrix(cells, TASKS, SKILLS)


def test_winner_tie_breaks_by_minimum_then_verifier_then_hash() -> None:
    evidence = aggregate_oracle_matrix(
        [
            _cell("task-1", "s000_v001", 1.0),
            _cell("task-2", "s000_v001", 0.0),
            _cell("task-1", "s001_v001", 0.5),
            _cell("task-2", "s001_v001", 0.5),
        ],
        TASKS,
        SKILLS,
    )
    assert select_winner(evidence, {skill: 1.0 for skill in SKILLS}, {"s000_v001": "a", "s001_v001": "z"}) == "s001_v001"


class _FakeAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, command, *, task_id, skill_id, **kwargs):
        self.calls += 1
        reward = 1.0 if skill_id.startswith("s000_") else 0.5
        return _cell(task_id, skill_id, reward), {}, command.jobs_dir / "result.json"


class _FakeRunner(SkillLiftSkillsBenchRunner):
    def _create_llm_clients(self) -> None:
        self.sg_client = self.rubricator_client = self.verifier_client = None
        self.revisions = 0

    def _generate_initial_receipt(self, task):
        from skilllift.rubricator import build_fallback_receipt

        return build_fallback_receipt(task)

    def _initialize_skills(self, task):
        from skilllift.schemas import SkillLiftConfig
        from skilllift.baselines.sg import initialize_skill_group

        return initialize_skill_group(task, SkillLiftConfig(skill_count=4, skill_format="agent_skill"), "", None)

    def _score_skills(self, task, skills, receipt, *, mode):
        from skilllift.verifier import score_skills

        scores = score_skills(task, skills, receipt, None, mode=mode, skill_format="agent_skill")
        for key, score in scores.items():
            score.normalized_score = 0.0 if key.slot == 0 else 1.0
        return scores

    def _revise_receipt(self, revision_input, store_root):
        self.revisions += 1
        return revision_input.receipt


def test_media_batch_writes_complete_markers_and_resumes_without_rollouts(tmp_path, monkeypatch) -> None:
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("TEST_SKILLSBENCH_URL", "https://example.test/v1")
    monkeypatch.setenv("TEST_SKILLSBENCH_KEY", "secret")
    split = load_and_validate_split(
        root / "skilllift_eval" / "benchmarks" / "skillsbench_split_v1.json",
        root / "skillsbench" / "tasks",
    )
    settings = SkillLiftSkillsBenchSettings(
        project_root=root,
        run_root=tmp_path / "run",
        tasks_root=root / "skillsbench" / "tasks",
        python_executable=root / "skillsbench" / ".venv" / "bin" / "python",
        model="vllm/test",
        sandbox="docker",
        trials=3,
        base_url_env="TEST_SKILLSBENCH_URL",
        api_key_env="TEST_SKILLSBENCH_KEY",
        skilllift={
            "skill_count": 4,
            "skill_format": "agent_skill",
            "outer_rounds": 3,
            "mode_a_iters": 3,
            "mode_b_iters": 3,
            "oracle_threshold": 0.75,
            "mode_a_min_score_threshold": 0.85,
            "rank_alignment_threshold": 0.8,
        },
        resume=True,
    )
    adapter = _FakeAdapter()
    runner = _FakeRunner(settings, adapter=adapter)

    runner.train(split, ["media-content-production"])

    round_path = settings.run_root / "train" / "media-content-production" / "b00" / "round-0" / "round_result.json"
    round_payload = json.loads(round_path.read_text(encoding="utf-8"))
    assert len(round_payload["oracle_evidence"]["oracle_matrix"]) == 16
    assert round_payload["stop_reason"] == "oracle_threshold"
    assert adapter.calls == 16
    assert runner.revisions == 1
    assert (settings.run_root / "train" / "media-content-production" / "b00" / "batch_result.json").exists()
    assert (settings.run_root / "final_skills" / "media-content-production" / "skilllift-media-content-production" / "SKILL.md").exists()

    resumed_adapter = _FakeAdapter()
    _FakeRunner(settings, adapter=resumed_adapter).train(split, ["media-content-production"])
    assert resumed_adapter.calls == 0

    test_adapter = _FakeAdapter()
    _FakeRunner(settings, adapter=test_adapter).test(split, ["media-content-production"])
    score_path = settings.run_root / "test" / "media-content-production" / "threejs-to-obj" / "score.json"
    score = json.loads(score_path.read_text(encoding="utf-8"))
    assert score["mean_reward"] == 0.5
    assert len(score["trials"]) == 3
    assert test_adapter.calls == 3

    resumed_test_adapter = _FakeAdapter()
    _FakeRunner(settings, adapter=resumed_test_adapter).test(split, ["media-content-production"])
    assert resumed_test_adapter.calls == 0
