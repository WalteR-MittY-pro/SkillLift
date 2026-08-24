import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("skilllift_run_batch_shim", ROOT / "eval" / "run_batch.py")
assert SPEC and SPEC.loader
SHIM = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SHIM)


def test_shim_resolves_task_to_external_and_config_to_skilllift(tmp_path) -> None:
    args = SHIM.resolve_forwarded_args(
        ["--task", "tasks/category/task.md", "--models-config", "models.json"],
        tmp_path / "WildClawBench",
    )

    assert args[1] == str((tmp_path / "WildClawBench" / "tasks/category/task.md").resolve())
    assert args[3] == str((ROOT / "models.json").resolve())


def test_shim_keeps_output_under_skilllift(monkeypatch) -> None:
    monkeypatch.setenv("OUTPUT_SUBDIR", "skilllift_baseline")

    env = SHIM.resolve_forwarded_env()

    assert env["OUTPUT_SUBDIR"] == str((ROOT / "skilllift_baseline").resolve())


def test_shim_keeps_default_output_under_skilllift(monkeypatch) -> None:
    monkeypatch.delenv("OUTPUT_SUBDIR", raising=False)

    env = SHIM.resolve_forwarded_env()

    assert env["OUTPUT_SUBDIR"] == str((ROOT / "output").resolve())
