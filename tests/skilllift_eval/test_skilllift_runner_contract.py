from __future__ import annotations

import ast
from dataclasses import asdict, fields
from pathlib import Path

import yaml

import skilllift
from skilllift import SkillLiftPortfolioCoordinator
from skilllift.coordinator import CoordinatorConfig
from skilllift import SkillLiftPortfolioCoordinator as CanonicalSkillLiftCoordinator


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATHS = {
    "wildclawbench": ROOT / "skilllift_eval" / "runners" / "skilllift_wildclaw.py",
    "skillsbench": ROOT / "skilllift_eval" / "runners" / "skilllift_skillsbench_tasks.py",
}
CORE_CONFIG_FIELDS = tuple(field.name for field in fields(CoordinatorConfig))
ALLOWED_OUTER_DIFFERENCES = {
    "adapter",
    "planner.max_input_chars",
    "planner.core_evidence_on_overflow",
    "terminal_threshold",
}
MAPPED_CORE_CONFIG_FIELDS = {
    "candidate_concurrency",
    "mode_a_iters",
    "mode_b_iters",
    "mode_a_min_score_threshold",
    "rank_alignment_threshold",
    "max_context_chars",
}


def _constructed_names(path: Path, suffix: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id.endswith(suffix)
    ]


def _constructor_keywords(path: Path, name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == name
    ]
    assert len(calls) == 1
    return {keyword.arg for keyword in calls[0].keywords if keyword.arg is not None}


def _core_config(params: dict) -> CoordinatorConfig:
    return CoordinatorConfig(
        candidate_concurrency=int(params.get("candidate_concurrency", 2)),
        mode_a_iters=int(params.get("mode_a_iters", 2)),
        mode_b_iters=int(params.get("mode_b_iters", 2)),
        mode_a_min_score_threshold=float(params.get("mode_a_min_score_threshold", 0.85)),
        rank_alignment_threshold=float(params.get("rank_alignment_threshold", 0.9)),
        max_context_chars=int(params.get("max_context_chars", 120_000)),
    )


def test_benchmark_runners_construct_the_public_skilllift_coordinator_once() -> None:
    assert SkillLiftPortfolioCoordinator is CanonicalSkillLiftCoordinator
    assert "coordinator" in skilllift.__all__
    assert "SkillLiftPortfolioCoordinator" in skilllift.__all__
    assert {
        benchmark: _constructed_names(path, "PortfolioCoordinator")
        for benchmark, path in RUNNER_PATHS.items()
    } == {
        "wildclawbench": ["SkillLiftPortfolioCoordinator"],
        "skillsbench": ["SkillLiftPortfolioCoordinator"],
    }


def test_benchmark_config_mappings_share_core_and_limit_outer_differences() -> None:
    wildclaw_profile = yaml.safe_load(
        (ROOT / "skilllift_eval" / "algorithm_params" / "skilllift.paper_default.yaml").read_text(encoding="utf-8")
    )
    skillsbench_profile = yaml.safe_load(
        (ROOT / "configs" / "skilllift_skillsbench_tasks.yaml").read_text(encoding="utf-8")
    )
    wildclaw_params = wildclaw_profile["resolved_params"]
    skillsbench_params = skillsbench_profile["skilllift"]

    wildclaw_core = _core_config(wildclaw_params)
    skillsbench_core = _core_config(skillsbench_params)
    assert {
        benchmark: _constructor_keywords(path, "CoordinatorConfig")
        for benchmark, path in RUNNER_PATHS.items()
    } == {
        "wildclawbench": MAPPED_CORE_CONFIG_FIELDS,
        "skillsbench": MAPPED_CORE_CONFIG_FIELDS,
    }
    assert {
        field: asdict(wildclaw_core)[field]
        for field in CORE_CONFIG_FIELDS
    } == {
        field: asdict(skillsbench_core)[field]
        for field in CORE_CONFIG_FIELDS
    }

    assert {
        benchmark: _constructor_keywords(path, "LLMRubricatorPlanner")
        for benchmark, path in RUNNER_PATHS.items()
    } == {
        "wildclawbench": {"max_input_chars"},
        "skillsbench": {"max_input_chars", "core_evidence_on_overflow"},
    }

    outer_wiring = {
        "wildclawbench": {
            "adapter": _constructed_names(RUNNER_PATHS["wildclawbench"], "PortfolioAdapter")[0],
            "planner": {
                "max_input_chars": wildclaw_params.get("max_context_chars"),
                "core_evidence_on_overflow": False,
            },
            "terminal_threshold": wildclaw_params["terminal_reward"],
        },
        "skillsbench": {
            "adapter": _constructed_names(RUNNER_PATHS["skillsbench"], "PortfolioAdapter")[0],
            "planner": {
                "max_input_chars": skillsbench_params["max_context_chars"],
                "core_evidence_on_overflow": True,
            },
            "terminal_threshold": skillsbench_params["terminal_threshold"],
        },
    }
    differences = set()
    for field in ("adapter", "terminal_threshold"):
        if outer_wiring["wildclawbench"][field] != outer_wiring["skillsbench"][field]:
            differences.add(field)
    for field in ("max_input_chars", "core_evidence_on_overflow"):
        if outer_wiring["wildclawbench"]["planner"][field] != outer_wiring["skillsbench"]["planner"][field]:
            differences.add(f"planner.{field}")
    assert differences <= ALLOWED_OUTER_DIFFERENCES
