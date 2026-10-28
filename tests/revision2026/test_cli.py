import subprocess
import sys

from experiments.revision2026.common import ROOT


def test_prepare_skilllearnbench_ignores_unrelated_benchmarks(monkeypatch):
    from experiments.revision2026 import __main__ as cli
    calls = []
    monkeypatch.setattr(cli, "prepare_sources", lambda names: calls.append(names))
    cli.main(["prepare", "--benchmarks", "skilllearnbench"])
    assert calls == [["skilllearnbench", "skillopt"]]
    cli.main(["prepare"])
    assert calls[-1] == list(cli.PINS)


def test_preflight_can_limit_checks_to_skilllearnbench(monkeypatch):
    from experiments.revision2026 import __main__ as cli
    calls = []
    monkeypatch.setattr(cli, "preflight", lambda models, benchmarks, **kwargs: calls.append((models, benchmarks)))
    cli.main(["preflight", "--models", "glm", "deepseek", "--benchmarks", "skilllearnbench"])
    assert calls == [(["glm", "deepseek"], ["skilllearnbench"])]


def test_status_counts_a_resumed_logical_job_once(tmp_path, capsys):
    import json
    from experiments.revision2026 import __main__ as cli
    from experiments.revision2026.common import write_json
    job = dict(kind="train", benchmark="skilllearnbench", model="glm", method="skillopt", run=0,
               family="python-scala-translation", root=str(tmp_path), config_hash="cfg")
    write_json(tmp_path / "jobs/old.json", {**job, "protocol": {"timeout": 300}})
    write_json(tmp_path / "jobs/old.status.json", {"status": "running"})
    write_json(tmp_path / "jobs/new.json", {**job, "protocol": {"timeout": 900}})
    write_json(tmp_path / "jobs/new.status.json", {"status": "error"})
    cli.main(["status", "--run-dir", str(tmp_path)])
    assert json.loads(capsys.readouterr().out) == [{"kind": "train", "recorded_status": "error", "count": 1}]


def test_legacy_audit_module_does_not_shadow_stdlib_statistics():
    result = subprocess.run([sys.executable, "-m", "experiments.revision2026.legacy_audit", "--help"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "--archive" in result.stdout


def test_cli_lists_all_three_workflows_and_recovery():
    result = subprocess.run([sys.executable, "-m", "experiments.revision2026", "--help"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0
    for name in ["generalization", "ablation", "repair-plan", "repair-run", "table2-rebuild", "report"]:
        assert name in result.stdout
