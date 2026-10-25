import subprocess
import sys

from experiments.revision2026.common import ROOT


def test_legacy_audit_module_does_not_shadow_stdlib_statistics():
    result = subprocess.run([sys.executable, "-m", "experiments.revision2026.legacy_audit", "--help"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "--archive" in result.stdout


def test_cli_lists_all_three_workflows_and_recovery():
    result = subprocess.run([sys.executable, "-m", "experiments.revision2026", "--help"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0
    for name in ["generalization", "ablation", "repair-plan", "repair-run", "table2-rebuild", "report"]:
        assert name in result.stdout
