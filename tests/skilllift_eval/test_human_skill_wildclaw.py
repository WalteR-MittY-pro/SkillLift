from __future__ import annotations

import json
from pathlib import Path

from skilllift_eval.model_endpoints import ModelEndpointProfile, endpoint_config_hash
from skilllift_eval.runners.human_skill_wildclaw import (
    FULL_TASK_SAMPLE_POLICY_ID,
    HumanSkillWildClawRunner,
    build_human_skill_bundle,
    validate_human_skill_inventory,
)
from skilllift_eval.runners.wildclaw import WildClawRunSettings
from skilllift_eval.schemas import stable_hash


ROOT = Path(__file__).resolve().parents[2]


def _endpoint() -> ModelEndpointProfile:
    return ModelEndpointProfile(
        model_endpoint_id="gpt-5.4",
        model_label="gpt-5.4",
        provider="openai-completions",
        provider_model_id="gpt-5.4",
        base_url="https://example.test/v1",
        api_key="literal-secret-for-test",
    )


def _write_task(root: Path, category: str, task_num: int, slug: str) -> Path:
    task_id = f"{category}_task_{task_num}_{slug}"
    path = root / "tasks" / category / f"{task_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""---
id: {task_id}
category: {category}
timeout_seconds: 30
---
## Prompt

Complete {slug}.

## Workspace Path

workspace/{task_id}

## Automated Checks

```python
def grade(**kwargs):
    return {{"overall_score": 1.0}}
```

## Skills

benchmark-native-skill
""",
        encoding="utf-8",
    )
    return path


def _write_human_skill(root: Path, task_path: Path, marker: str) -> Path:
    path = root / task_path.name.lower()
    name = task_path.stem.lower().replace("_", "-")
    path.write_text(
        f"---\nname: {name}\ndescription: Human-authored guidance.\n---\n\n# Guidance\n\n{marker}\n",
        encoding="utf-8",
    )
    return path


def _settings(tmp_path: Path, wildclaw_root: Path) -> WildClawRunSettings:
    endpoint = _endpoint()
    return WildClawRunSettings(
        run_root=tmp_path / "run",
        wildclaw_root=wildclaw_root,
        model_endpoint=endpoint,
        matrix_cell_id="native_end_to_end__human_skill__wildclawbench__gpt-5_4",
        model_label="gpt-5.4",
        endpoint_config_hash=endpoint_config_hash(endpoint),
        algorithm_param_hash=stable_hash({"baseline": "human_skill"}),
        benchmark_source_hash="benchmark",
        task_set_hash="task-set",
        run_config_hash="run-config",
        evaluation_mode="native_end_to_end",
        context_budget_profile="native_default",
        visible_feedback_policy_id="public_sanitized_v1",
        oracle_call_budget="native_default",
        task_sample_policy_id=FULL_TASK_SAMPLE_POLICY_ID,
        round_budget="paper_default",
        token_budget="native_default",
        tasks_mode="full",
        baseline="human_skill",
    )


def test_repo_human_skill_inventory_matches_all_60_wildclaw_tasks() -> None:
    inventory = validate_human_skill_inventory(
        ROOT / "WildClawBench",
        ROOT / "skilllift_eval" / "human_skills" / "wildclawbench",
    )

    assert len(inventory) == 60
    assert len({task.name.lower() for task, _ in inventory}) == 60
    assert all(task.name.lower() == skill.name.lower() for task, skill in inventory)


def test_human_skill_runner_loads_only_the_matching_skill_for_each_task(tmp_path: Path) -> None:
    wildclaw_root = tmp_path / "WildClawBench"
    (wildclaw_root / "skills").mkdir(parents=True)
    first = _write_task(wildclaw_root, "01_Productivity_Flow", 1, "alpha")
    second = _write_task(wildclaw_root, "02_Code_Intelligence", 2, "beta")
    human_skills_root = tmp_path / "human-skills"
    human_skills_root.mkdir()
    _write_human_skill(human_skills_root, first, "HUMAN_ALPHA_MARKER")
    _write_human_skill(human_skills_root, second, "HUMAN_BETA_MARKER")
    calls: list[dict] = []

    def fake_task_runner(**kwargs):
        task = kwargs["task"]
        skills_root = Path(task["skills_path"])
        skill_docs = list(skills_root.glob("*/SKILL.md"))
        calls.append(
            {
                "task_id": task["task_id"],
                "skills": task["skills"].splitlines(),
                "documents": [path.read_text(encoding="utf-8") for path in skill_docs],
            }
        )
        output = tmp_path / "output" / task["task_id"]
        output.mkdir(parents=True)
        (output / "agent.log").write_text("human skill loaded\n", encoding="utf-8")
        return {
            "task_id": task["task_id"],
            "scores": {"overall_score": 1.0},
            "usage": {"total_tokens": 1},
            "error": None,
            "output_dir": str(output),
        }

    result = HumanSkillWildClawRunner(task_runner=fake_task_runner).run(
        _settings(tmp_path, wildclaw_root),
        human_skills_root,
    )

    assert result.summary["status"] == "SUCCEEDED"
    assert result.summary["expected_count"] == 2
    assert len(calls) == 2
    by_task = {call["task_id"]: call for call in calls}
    assert "HUMAN_ALPHA_MARKER" in by_task[first.stem]["documents"][0]
    assert "HUMAN_BETA_MARKER" not in by_task[first.stem]["documents"][0]
    assert "HUMAN_BETA_MARKER" in by_task[second.stem]["documents"][0]
    assert "HUMAN_ALPHA_MARKER" not in by_task[second.stem]["documents"][0]
    assert all(len(call["skills"]) == 1 for call in calls)
    assert all(record.baseline == "human_skill" for record in result.records)
    assert all(record.task_sample_policy_id == FULL_TASK_SAMPLE_POLICY_ID for record in result.records)

    metrics = [
        json.loads(line)
        for line in (tmp_path / "run" / "round_metrics.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert {metric["round_id"] for metric in metrics} == {first.stem, second.stem}
    assert {metric["round_type"] for metric in metrics} == {"human_skill_evaluation"}

    manifest = json.loads(
        (tmp_path / "run" / "human_skill_source_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["inventory_count"] == 2
    assert manifest["selected_count"] == 2


def test_human_skill_bundle_preserves_supplied_skill_document(tmp_path: Path) -> None:
    task = _write_task(tmp_path / "WildClawBench", "03_Social_Interaction", 1, "gamma")
    source = tmp_path / task.name.lower()
    source.write_text(
        "---\nname: supplied-human-skill\ndescription: Supplied by a human.\n---\n\nExact body.\n",
        encoding="utf-8",
    )

    bundle = build_human_skill_bundle(task, source)

    assert bundle.baseline == "human_skill"
    assert bundle.skills[0]["id"] == "supplied-human-skill"
    assert bundle.skills[0]["files"]["SKILL.md"] == source.read_text(encoding="utf-8")
