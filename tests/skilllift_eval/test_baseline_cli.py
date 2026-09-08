from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "skilllift_eval" / "config.yaml"
RUN_ROOT = ROOT / "runs" / "task_1_x" / "cli_tests"


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "skilllift_eval.cli", *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_run_dry_run_normalizes_baseline_alias_and_writes_standalone_config() -> None:
    out_dir = RUN_ROOT / "alias"
    result = run_cli(
        "run",
        "autoskills",
        "--dry-run",
        "--benchmark",
        "wildclawbench",
        "--model",
        "gpt-5.4",
        "--config",
        str(CONFIG),
        "--run-root",
        str(out_dir),
        "--param-profile",
        "paper_default",
        "--evaluation-mode",
        "native_end_to_end",
        "--tasks.mode",
        "single",
        "--tasks.filter",
        "task-1",
    )

    assert result.returncode == 0, result.stderr
    config = json.loads((out_dir / "run_config.json").read_text())
    assert config["baseline"] == "autoskill"
    assert config["baseline_input"] == "autoskills"
    assert config["tasks"]["mode"] == "single"
    assert config["tasks"]["filter"] == "task-1"
    encoded = json.dumps(config)
    assert '"api_key"' not in encoded
    assert "literal-secret-for-test" not in encoded


def test_run_dry_run_without_overrides_has_empty_override_diff_for_all_profiles() -> None:
    for baseline in ["human_skill", "textgrad", "coevoskills"]:
        out_dir = RUN_ROOT / f"zero_override_{baseline}"
        result = run_cli(
            "run",
            baseline,
            "--dry-run",
            "--benchmark",
            "wildclawbench",
            "--model",
            "gpt-5.4",
            "--config",
            str(CONFIG),
            "--run-root",
            str(out_dir),
            "--param-profile",
            "paper_default",
            "--tasks.mode",
            "full",
        )

        assert result.returncode == 0, result.stderr
        config = json.loads((out_dir / "run_config.json").read_text())
        assert config["override_diff"] == {}
        assert set(config["resolved_params"]) == set(config["paper_default_params"])


def test_human_skill_dry_run_records_supplemental_baseline_contract() -> None:
    out_dir = RUN_ROOT / "human_skill"
    result = run_cli(
        "run",
        "human_skill",
        "--dry-run",
        "--benchmark",
        "wildclawbench",
        "--model",
        "gpt-5.4",
        "--config",
        str(CONFIG),
        "--run-root",
        str(out_dir),
        "--param-profile",
        "paper_default",
        "--tasks.mode",
        "single",
        "--tasks.filter",
        "06-01",
    )

    assert result.returncode == 0, result.stderr
    config = json.loads((out_dir / "run_config.json").read_text())
    assert config["baseline"] == "human_skill"
    assert config["task_sample_policy_id"] == "human_skill_single_task_v1"
    assert config["resolved_params"]["skill_selection"] == "exact_task_id_match"
    assert config["human_skill_source"]["inventory_count"] == 60
    assert config["human_skill_source"]["source_hash"].startswith("sha256:")


def test_run_dry_run_applies_inline_and_file_overrides(tmp_path: Path) -> None:
    override = tmp_path / "override.yaml"
    override.write_text("resolved_params:\n  epochs: 9\n")
    out_dir = RUN_ROOT / "override"

    result = run_cli(
        "run",
        "autoskill",
        "--dry-run",
        "--benchmark",
        "wildclawbench",
        "--model",
        "gpt-5.4",
        "--config",
        str(CONFIG),
        "--run-root",
        str(out_dir),
        "--param-profile",
        "paper_default",
        "--algorithm-override",
        str(override),
        "--algo-param",
        "rollout_batch_size=41",
        "--tasks.mode",
        "full",
    )

    assert result.returncode == 0, result.stderr
    config = json.loads((out_dir / "run_config.json").read_text())
    assert config["resolved_params"]["epochs"] == 9
    assert config["resolved_params"]["rollout_batch_size"] == 41
    assert config["override_diff"]
    assert config["algorithm_param_hash"].startswith("sha256:")


def test_task_mode_validation_and_tau2_domain_rejection() -> None:
    missing_filter = run_cli(
        "run",
        "autoskill",
        "--dry-run",
        "--benchmark",
        "wildclawbench",
        "--model",
        "gpt-5.4",
        "--config",
        str(CONFIG),
        "--param-profile",
        "paper_default",
        "--tasks.mode",
        "single",
    )
    assert missing_filter.returncode != 0
    assert "--tasks.filter" in missing_filter.stderr

    banking = run_cli(
        "run",
        "autoskill",
        "--dry-run",
        "--benchmark",
        "tau2",
        "--model",
        "gpt-5.4",
        "--config",
        str(CONFIG),
        "--param-profile",
        "paper_default",
        "--tasks.mode",
        "full",
        "--tau2.domains",
        "airline,banking_knowledge",
        "--tau2.split",
        "base",
    )
    assert banking.returncode != 0
    assert "banking_knowledge" in banking.stderr


def test_budget_matched_run_dry_run_is_accepted_without_supplemental_matrix() -> None:
    out_dir = RUN_ROOT / "budget"
    result = run_cli(
        "run",
        "skilllift",
        "--dry-run",
        "--benchmark",
        "tau2",
        "--model",
        "opus-4.7",
        "--config",
        str(CONFIG),
        "--run-root",
        str(out_dir),
        "--param-profile",
        "paper_default",
        "--evaluation-mode",
        "budget_matched",
        "--context-budget-profile",
        "matched_v1",
        "--tasks.mode",
        "full",
        "--tau2.domains",
        "airline,retail,telecom",
        "--tau2.split",
        "base",
    )
    assert result.returncode == 0, result.stderr
    assert (out_dir / "run_config.json").exists()
    assert not (out_dir / "matrix_cells.json").exists()


def test_tau2_eval_split_is_rejected_for_phase3_contract() -> None:
    result = run_cli(
        "run",
        "no_skill",
        "--dry-run",
        "--benchmark",
        "tau2",
        "--model",
        "gpt-5.4",
        "--config",
        str(CONFIG),
        "--tasks.mode",
        "full",
        "--tau2.domains",
        "airline,retail,telecom",
        "--tau2.split",
        "eval",
    )

    assert result.returncode != 0
    assert "tau2 split must be base" in result.stderr
