from pathlib import Path

from experiments.revision2026.docker_agent import scorer_command, solver_command


def test_hidden_verifiers_are_absent_from_solver_command():
    command = solver_command("solver", "image", [])
    assert "--mount" not in command
    assert "-v" not in command
    assert not any("tests" in x or "API_KEY" in x for x in command)


def test_only_stopped_solver_snapshot_is_used_for_scoring():
    command = scorer_command("scorer", "snapshot:abc", Path("/tasks/x"), Path("/runs/x"))
    assert "snapshot:abc" in command
    assert "type=bind,source=/tasks/x/tests,target=/tests,readonly" in command
    assert command[-1] == "/tests/test.sh"
