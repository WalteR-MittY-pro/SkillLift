from skilllift.persistence import ExperimentStore, write_json
from skilllift.report import build_report
from skilllift.schemas import SkillLiftConfig, EvoSkill, OracleScore, Receipt, RoundState, RubricCriterion, SkillKey, TaskSpec


def test_report_generates_markdown_and_html_with_alignment(tmp_path) -> None:
    store = ExperimentStore(tmp_path / "exp")
    receipt = Receipt(1, [RubricCriterion("r1", "Planning", "Has plan", 1)], 1, 0)
    store.save_config(SkillLiftConfig(exp_name="x"))
    store.save_task_spec(TaskSpec("task", "body"))
    store.save_receipt(receipt, "final")
    store.save_round_state(RoundState(0, 0, "mode_b", 0, {}, receipt, rank_alignment=0.5))
    store.save_revision_attempt(
        attempt=__import__("skilllift.schemas", fromlist=["ReceiptRevisionAttempt"]).ReceiptRevisionAttempt(
            "attempt_0", 1, False, ["bad"]
        ),
        files={"prompt.txt": "prompt"},
    )
    store.save_summary({"best_skill": "s000_v001", "best_oracle_score": 1.0}, "# Summary")
    paths = build_report(tmp_path / "exp")
    md = paths["markdown"].read_text(encoding="utf-8")
    html = paths["html"].read_text(encoding="utf-8")
    assert "Rank Alignment" in md
    assert "0.5" in md
    assert "bad" in md
    assert "<html" in html


def test_report_html_surfaces_oracle_runs_and_skill_snapshots(tmp_path) -> None:
    store = ExperimentStore(tmp_path / "exp")
    receipt = Receipt(1, [RubricCriterion("r1", "Planning", "Has plan", 1)], 1, 0)
    skill = EvoSkill(SkillKey(0, 2), "demo_skill", {"SKILL.md": "Use Slack carefully.", "executor.py": "def main():\n    return 0\n"}, "executor.py", {"llm_update": True, "summary": "better"})
    output_dir = tmp_path / "out" / "run"
    (output_dir / "task_output" / "workspace" / "results").mkdir(parents=True)
    write_json(output_dir / "score.json", {"overall_score": 0.91})
    write_json(output_dir / "usage.json", {"request_count": 3, "elapsed_time": 12.5, "total_tokens": 1234})
    (output_dir / "task_output" / "workspace" / "results" / "results.md").write_text("# Result\nDone\n", encoding="utf-8")
    (output_dir / "chat.jsonl").write_text(
        '{"message":{"role":"assistant","content":[{"type":"toolCall","name":"read","arguments":{"file_path":"/root/.agents/skills/demo-skill/SKILL.md"}}]}}\n',
        encoding="utf-8",
    )
    store.save_config(SkillLiftConfig(exp_name="x"))
    store.save_task_spec(TaskSpec("task", "body"))
    store.save_receipt(receipt, "final")
    store.save_skill(skill, "outer_000_mode_a_000")
    store.save_oracle_scores({skill.key: OracleScore(skill.key, 0.91, 1, output_dir=str(output_dir))}, "outer_000_mode_b_batch")
    store.save_round_state(RoundState(0, 0, "mode_b", 0, {skill.key: skill}, receipt, oracle_scores={skill.key: OracleScore(skill.key, 0.91, 1)}))
    write_json(
        tmp_path / "exp" / "oracle_manifests" / "outer_000" / "s000_v002_oracle_run_manifest.json",
        {
            "skill_key": "s000_v002",
            "output_dir": str(output_dir),
            "skill_dir": "runtime/skill",
            "failure_stage": "success",
            "returncode": 0,
            "command": ["python3", "eval/run_batch.py"],
        },
    )
    store.save_summary({"best_skill": "s000_v002", "best_oracle_score": 0.91}, "# Summary")

    html = build_report(tmp_path / "exp")["html"].read_text(encoding="utf-8")

    assert "Oracle Runs" in html
    assert "Oracle Resource Summary" in html
    assert "12.50s" in html
    assert "1,234" in html
    assert "0.91" in html
    assert "Skill Snapshots" in html
    assert "Use Slack carefully." in html
    assert "# Result" in html
    assert "chat.jsonl" in html
    assert "Skill read verified" in html
    assert "/root/.agents/skills/demo-skill/SKILL.md" in html


def test_report_accepts_skill_read_tool_call_path_argument(tmp_path) -> None:
    store = ExperimentStore(tmp_path / "exp")
    receipt = Receipt(1, [RubricCriterion("r1", "Planning", "Has plan", 1)], 1, 0)
    skill = EvoSkill(SkillKey(0, 1), "demo_skill", {"SKILL.md": "Use it."})
    output_dir = tmp_path / "out" / "run"
    output_dir.mkdir(parents=True)
    write_json(output_dir / "score.json", {"overall_score": 0.5})
    (output_dir / "chat.jsonl").write_text(
        '{"message":{"role":"assistant","content":[{"type":"toolCall","name":"read","arguments":{"path":"/root/.agents/skills/demo-skill/SKILL.md"}}]}}\n',
        encoding="utf-8",
    )
    store.save_config(SkillLiftConfig(exp_name="x"))
    store.save_task_spec(TaskSpec("task", "body"))
    store.save_receipt(receipt, "final")
    store.save_oracle_scores({skill.key: OracleScore(skill.key, 0.5, 0, output_dir=str(output_dir))}, "outer_000_mode_b_batch")
    write_json(
        tmp_path / "exp" / "oracle_manifests" / "outer_000" / "s000_v001_oracle_run_manifest.json",
        {
            "skill_key": "s000_v001",
            "output_dir": str(output_dir),
            "skill_dir": "runtime/skill",
            "failure_stage": "task_low_score",
            "returncode": 0,
            "command": ["python3", "eval/run_batch.py"],
        },
    )
    store.save_summary({"best_skill": "s000_v001", "best_oracle_score": 0.5}, "# Summary")

    md = build_report(tmp_path / "exp")["markdown"].read_text(encoding="utf-8")

    assert "skill_read=True" in md
