from pathlib import Path


def test_baseline_defaults_to_single_task_concurrency() -> None:
    root = Path(__file__).resolve().parent.parent
    script = (root / "scripts" / "run_skilllift_baseline.sh").read_text(encoding="utf-8")

    assert "--parallel 1" in script
    assert "--parallel 2" not in script
    assert '${WILDCLAWBENCH_ROOT:-${root_dir}/../WildClawBench}' in script
    assert 'find "${wildclaw_root}/tasks"' in script
