from collections import Counter

from experiments.revision2026 import legacy, oracles, runner
from experiments.revision2026.common import load_protocol, tree_hash, write_json
from experiments.revision2026.sources import Instance


def test_generalization_has_all_five_groups_and_no_seed_in_test(tmp_path, monkeypatch):
    tasks = [Instance(f"family{i}", n, tmp_path / f"family{i}-{n}") for i in range(20) for n in range(1, 6)]
    monkeypatch.setattr(runner, "instances", lambda: tasks)
    jobs = runner.generalization_jobs(tmp_path, load_protocol(), "cfg")
    counts = Counter(job["kind"] for job in jobs)
    assert counts == {"train": 600, "evaluate": 2400}
    assert all(job["instance"] > 1 for job in jobs if job["kind"] == "evaluate")
    assert len({(job["model"], job["method"]) for job in jobs}) == 10


def test_fresh_table2_plan_contains_all_36_overall_conditions(tmp_path, monkeypatch):
    monkeypatch.setattr(oracles, "native_task_ids", lambda _: ["one", "two"])
    jobs = legacy.rebuild_jobs(tmp_path, load_protocol(), "cfg")
    groups = {(j["benchmark"], j["model"], j["method"]) for j in jobs if j["kind"] == "evaluate"}
    assert len(groups) == 36
    assert len([j for j in jobs if j["kind"] == "write_seed"]) == 4  # shared across models/runs


def test_repair_keeps_missing_assets_in_the_evaluation_denominator(tmp_path):
    skill = tmp_path / "source"
    skill.mkdir()
    (skill / "SKILL.md").write_text("frozen")
    common = {"benchmark": "skillsbench", "model": "glm", "method": "skilllift", "evolution_run": 0}
    plan = {"assets": [{**common, "task_id": "one", "family": "one", "source_dir": str(skill), "content_hash": tree_hash(skill)}],
            "unresolved": [{**common, "task_id": "two", "family": "two", "reason": "missing_skill"}]}
    path = tmp_path / "plan.json"
    write_json(path, plan)
    jobs = legacy.repair_jobs(path, tmp_path / "run", load_protocol(), "cfg")
    assert len(jobs) == 6
    assert len([j for j in jobs if j.get("blocked_reason")]) == 3
