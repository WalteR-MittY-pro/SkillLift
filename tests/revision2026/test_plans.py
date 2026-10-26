from collections import Counter

from experiments.revision2026 import legacy, oracles, runner
from experiments.revision2026.common import load_protocol, tree_hash, write_json
from experiments.revision2026.sources import Instance


def test_generalization_has_agreed_groups_one_run_and_no_seed_in_test(tmp_path, monkeypatch):
    tasks = [Instance(f"family{i}", n, tmp_path / f"family{i}-{n}") for i in range(20) for n in range(1, 6)]
    monkeypatch.setattr(runner, "instances", lambda: tasks)
    protocol = load_protocol()
    assert protocol["skilllearnbench"]["methods"] == ["no_skill", "llm_written", "skillopt", "skilllift"]
    jobs = runner.generalization_jobs(tmp_path, protocol, "cfg")
    counts = Counter(job["kind"] for job in jobs)
    # 2 models x 4 methods x 1 run x 20 families; 4 held-out instances per mock family.
    assert counts == {"train": 160, "evaluate": 640}
    assert all(job["instance"] > 1 for job in jobs if job["kind"] == "evaluate")
    assert {job["run"] for job in jobs} == {0}
    assert len({(job["model"], job["method"]) for job in jobs}) == 8


def test_skillsbench_ablation_arms_follow_the_published_reference_decision(tmp_path, monkeypatch):
    monkeypatch.setattr(oracles, "native_task_ids", lambda benchmark: [f"{benchmark}-{i}" for i in range(87)])
    protocol = load_protocol()
    assert protocol["ablation"]["benchmarks"] == ["skillsbench"]
    jobs = runner.ablation_jobs(tmp_path, protocol, "cfg")
    counts = Counter((job["model"], job["method"], job["kind"]) for job in jobs)
    # GLM: Direct Oracle only, compared with the published Table 2 SkillLift result (no new SkillLift, no finals).
    assert counts[("glm", "direct_oracle", "train")] == 87 and counts[("glm", "skilllift", "train")] == 0
    assert not any(job["model"] == "glm" and job["kind"] == "evaluate" for job in jobs)
    # DeepSeek-V4.1-Flash: both arms are new, each frozen skill re-run twice independently.
    for method in ("skilllift", "direct_oracle"):
        assert counts[("deepseek", method, "train")] == 87 and counts[("deepseek", method, "evaluate")] == 174
    assert {job["benchmark"] for job in jobs} == {"skillsbench"}
    assert protocol["ablation"]["published_reference"]["glm"]["skilllift"]["mean_percent"] == 75.1


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
