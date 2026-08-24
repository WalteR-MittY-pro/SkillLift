from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "skilllift_eval" / "config.yaml"
RUN_ROOT = ROOT / "runs" / "task_0_1_dry_run"


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "skilllift_eval.cli", *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_native_matrix_dry_run_writes_16_cells_without_secrets() -> None:
    result = run_cli(
        "matrix",
        "--dry-run",
        "--evaluation-mode",
        "native_end_to_end",
        "--config",
        str(CONFIG),
        "--run-root",
        str(RUN_ROOT),
    )

    assert result.returncode == 0, result.stderr
    assert "16 native_end_to_end matrix cells" in result.stdout

    cells = json.loads((RUN_ROOT / "matrix_cells.json").read_text())
    assert len(cells) == 16
    assert {cell["baseline"] for cell in cells} == {
        "no_skill",
        "autoskill",
        "textgrad",
        "skilllift",
    }
    assert {cell["benchmark"] for cell in cells} == {"wildclawbench", "tau2"}
    assert {cell["model_label"] for cell in cells} == {"gpt-5.4", "opus-4.7"}
    assert {cell["evaluation_mode"] for cell in cells} == {"native_end_to_end"}
    assert {cell["context_budget_profile"] for cell in cells} == {"native_default"}
    assert {cell["algorithm_param_profile"] for cell in cells} == {"paper_default"}
    assert all(cell["algorithm_override_path"] is None for cell in cells)
    assert all(cell["algorithm_override_diff_path"] is None for cell in cells)

    wildclaw = [cell for cell in cells if cell["benchmark"] == "wildclawbench"]
    assert {cell["task_count"] for cell in wildclaw} == {60}

    tau2 = [cell for cell in cells if cell["benchmark"] == "tau2"]
    assert all(cell["task_count"] is None for cell in tau2)
    assert all(cell["tau2_domains"] == ["airline", "retail", "telecom"] for cell in tau2)
    assert {cell["tau2_split"] for cell in tau2} == {"base"}
    assert all("banking_knowledge" not in cell["task_set_id"] for cell in tau2)

    redacted = json.loads((RUN_ROOT / "model_endpoints.redacted.json").read_text())
    assert set(redacted) == {"gpt-5.4", "opus-4.7"}
    serialized = json.dumps(redacted)
    assert all("api_key" not in endpoint for endpoint in redacted.values())
    assert redacted["gpt-5.4"]["provider"] == "openai-completions"
    assert redacted["gpt-5.4"]["provider_model_id"] == "gpt-5.4"
    assert redacted["gpt-5.4"]["api_key_env"] == "GPT_API_KEY"
    assert redacted["opus-4.7"]["provider"] == "openai-completions"
    assert redacted["opus-4.7"]["provider_model_id"] == "claude-opus-4-7"
    assert redacted["opus-4.7"]["api_key_env"] == "CLAUDE_API_KEY"
    assert "GLM_API_KEY" not in serialized
    assert "OPENROUTER_API_KEY" not in serialized


def test_budget_matched_interface_is_accepted_without_generating_cells() -> None:
    result = run_cli(
        "matrix",
        "--dry-run",
        "--evaluation-mode",
        "budget_matched",
        "--context-budget-profile",
        "matched_v1",
        "--config",
        str(CONFIG),
        "--run-root",
        str(RUN_ROOT / "budget"),
    )

    assert result.returncode == 0, result.stderr
    assert "budget_matched interface accepted" in result.stdout
    assert not (RUN_ROOT / "budget" / "matrix_cells.json").exists()


def test_literal_api_key_is_redacted_from_endpoint_artifact(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "model_endpoints:\n"
        "  gpt-5.4:\n"
        "    provider: openai-compatible\n"
        "    provider_model_id: glm-5.2\n"
        "    base_url: https://open.bigmodel.cn/api/coding/paas/v4\n"
        "    api_key: literal-secret-for-test\n"
        "  opus-4.7:\n"
        "    provider: openai-compatible\n"
        "    provider_model_id: glm-5.2\n"
        "    base_url: https://open.bigmodel.cn/api/coding/paas/v4\n"
        "    api_key_env: GLM_API_KEY\n"
        "context_budgets:\n"
        "  native_default:\n"
        "    context_budget_profile: native_default\n"
        "    visible_feedback_policy_id: public_sanitized_v1\n"
        "    oracle_call_budget: native_default\n"
        "    task_sample_policy_id: full_native_v1\n"
        "    round_budget: paper_default\n"
        "    token_budget: native_default\n"
    )
    run_root = tmp_path / "run"

    result = run_cli(
        "matrix",
        "--dry-run",
        "--evaluation-mode",
        "native_end_to_end",
        "--config",
        str(config),
        "--run-root",
        str(run_root),
    )

    assert result.returncode == 0, result.stderr
    redacted = (run_root / "model_endpoints.redacted.json").read_text()
    assert "literal-secret-for-test" not in redacted
    assert '"api_key"' not in redacted


def test_missing_endpoint_required_fields_fail_fast(tmp_path: Path) -> None:
    bad_config = tmp_path / "bad_config.yaml"
    bad_config.write_text(
        "model_endpoints:\n"
        "  gpt-5.4:\n"
        "    provider: openai-compatible\n"
        "    api_key_env: GLM_API_KEY\n"
        "context_budgets:\n"
        "  native_default:\n"
        "    context_budget_profile: native_default\n"
    )

    result = run_cli(
        "matrix",
        "--dry-run",
        "--evaluation-mode",
        "native_end_to_end",
        "--config",
        str(bad_config),
        "--run-root",
        str(tmp_path / "run"),
    )

    assert result.returncode != 0
    assert "missing required field" in result.stderr
