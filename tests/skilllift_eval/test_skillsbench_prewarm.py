from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "prewarm.sh"


def _fake_docker(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "docker-calls.log"
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$DOCKER_CALLS\"\n"
        "if [ \"$1\" = info ]; then exit 0; fi\n"
        "if [ \"$1 $2\" = 'image inspect' ]; then exit \"${IMAGE_EXISTS:-1}\"; fi\n"
        "if [ \"$1\" = build ]; then exit 0; fi\n"
        "if [ \"$1\" = pull ]; then exit 9; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    return bin_dir, calls


def _run_prewarm(tmp_path: Path, *, image_exists: bool) -> tuple[subprocess.CompletedProcess, str]:
    tasks = tmp_path / "tasks"
    environment = tasks / "task-a" / "environment"
    environment.mkdir(parents=True)
    (environment / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    bin_dir, calls = _fake_docker(tmp_path)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "DOCKER_CALLS": str(calls),
        "IMAGE_EXISTS": "0" if image_exists else "1",
        "SKILLSBENCH_TASKS_DIR": str(tasks),
        "FAILURES_FILE": str(tmp_path / "failures.txt"),
        "PARALLEL": "1",
        "MAX_RETRY": "1",
    }
    completed = subprocess.run(
        ["bash", str(SCRIPT), "task-a"],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    return completed, calls.read_text(encoding="utf-8")


def test_prewarm_skips_existing_image_without_build_or_pull(tmp_path: Path) -> None:
    completed, calls = _run_prewarm(tmp_path, image_exists=True)

    assert completed.returncode == 0, completed.stderr
    assert "task-a|SKIPPED_EXISTING" in completed.stdout
    assert "build" not in calls
    assert "pull" not in calls


def test_prewarm_builds_only_missing_image_without_registry_preflight(tmp_path: Path) -> None:
    completed, calls = _run_prewarm(tmp_path, image_exists=False)

    assert completed.returncode == 0, completed.stderr
    assert "task-a|SUCCESS" in completed.stdout
    assert "build -t prewarm/task-a:prewarm ." in calls
    assert "pull" not in calls
