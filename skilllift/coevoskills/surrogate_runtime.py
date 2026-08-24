from __future__ import annotations

import ast
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .errors import SurrogateSuiteError
from .schemas import ArtifactSnapshot, AssertionResult, SurrogateResult, TestSuiteVersion

ALLOWED_IMPORTS = {"pathlib", "json", "csv", "re", "math", "statistics", "datetime"}
DENIED_CALLS = {"eval", "exec", "compile", "__import__", "input", "breakpoint"}
DENIED_ATTRIBUTES = {
    "system", "popen", "spawn", "fork", "connect", "urlopen", "request",
    "getenv", "environ", "walk", "rmtree", "unlink", "remove", "rename", "replace",
}

RUNNER_SOURCE = """from __future__ import annotations
import importlib.util
import json
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("generated_suite", sys.argv[1])
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)
result = module.run(Path(sys.argv[2]))
print(json.dumps(result, ensure_ascii=False))
"""


class PythonSurrogateRuntime:
    def __init__(
        self,
        *,
        timeout_seconds: int = 30,
        docker_image: str | None = None,
        allow_local: bool = False,
    ) -> None:
        if docker_image is None and not allow_local:
            raise ValueError("production surrogate runtime requires docker_image")
        self.timeout_seconds = timeout_seconds
        self.docker_image = docker_image
        self.allow_local = allow_local

    def run(
        self, suite: TestSuiteVersion, artifacts: ArtifactSnapshot
    ) -> SurrogateResult:
        validate_suite_code(suite.code)
        artifact_root = Path(artifacts.root_path).resolve()
        if not artifact_root.is_dir():
            raise SurrogateSuiteError(f"artifact root does not exist: {artifact_root}")
        with tempfile.TemporaryDirectory(prefix="coevoskills-surrogate-") as raw_dir:
            suite_root = Path(raw_dir)
            suite_path = suite_root / "test_surrogate.py"
            runner_path = suite_root / "runner.py"
            suite_path.write_text(_ensure_newline(suite.code), encoding="utf-8")
            runner_path.write_text(RUNNER_SOURCE, encoding="utf-8")
            completed = self._execute(suite_root, artifact_root)
        if completed.returncode != 0:
            raise SurrogateSuiteError(
                f"surrogate suite failed with exit {completed.returncode}: {completed.stderr[-1000:]}"
            )
        payload = _last_json_line(completed.stdout)
        results = _parse_results(payload, suite)
        return SurrogateResult(
            suite_version=suite.version,
            results=results,
            stdout=completed.stdout[-4000:],
            stderr=completed.stderr[-4000:],
        )

    def _execute(self, suite_root: Path, artifact_root: Path) -> subprocess.CompletedProcess[str]:
        if self.docker_image:
            _ensure_docker_image_available(self.docker_image)
            command = [
                "docker", "run", "--rm", "--pull", "never", "--network", "none", "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--pids-limit", "64", "--memory", "256m", "--cpus", "0.5",
                "-e", "PYTHONDONTWRITEBYTECODE=1",
                "-v", f"{suite_root.resolve()}:/suite:ro",
                "-v", f"{artifact_root}:/artifacts:ro",
                self.docker_image,
                "python3", "/suite/runner.py", "/suite/test_surrogate.py", "/artifacts",
            ]
        else:
            command = [
                os.environ.get("PYTHON", "python3"),
                str(suite_root / "runner.py"),
                str(suite_root / "test_surrogate.py"),
                str(artifact_root),
            ]
        try:
            return subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
                env={"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"},
            )
        except subprocess.TimeoutExpired as exc:
            raise SurrogateSuiteError(
                f"surrogate suite timed out after {self.timeout_seconds}s"
            ) from exc


def _ensure_docker_image_available(image: str) -> None:
    try:
        completed = subprocess.run(
            ["docker", "image", "inspect", image],
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise SurrogateSuiteError(
            "Docker is unavailable; CoEvoSkills surrogate verification requires a local Docker image"
        ) from exc
    if completed.returncode == 0:
        return
    listed = subprocess.run(
        ["docker", "image", "ls", "-q", image],
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    if listed.returncode == 0 and listed.stdout.strip():
        return
    load_hint = (
        "docker load -i WildClawBench/Images/wildclawbench-ubuntu_v1.3.tar"
        if image == "wildclawbench-ubuntu:v1.3"
        else f"docker image inspect {image}"
    )
    raise SurrogateSuiteError(
        f"surrogate Docker image is not loaded locally: {image}. Run: {load_hint}"
    )


def validate_suite_code(code: str) -> None:
    try:
        tree = ast.parse(code, filename="test_surrogate.py")
    except SyntaxError as exc:
        raise SurrogateSuiteError(f"invalid surrogate Python: {exc}") from exc
    run_defs = [
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run"
    ]
    if len(run_defs) != 1 or isinstance(run_defs[0], ast.AsyncFunctionDef):
        raise SurrogateSuiteError("surrogate code must define exactly one synchronous run function")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] not in ALLOWED_IMPORTS:
                    raise SurrogateSuiteError(f"forbidden import: {alias.name}")
        if isinstance(node, ast.ImportFrom):
            if not node.module or node.module.split(".", 1)[0] not in ALLOWED_IMPORTS:
                raise SurrogateSuiteError(f"forbidden import: {node.module}")
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in DENIED_CALLS:
                raise SurrogateSuiteError(f"forbidden call: {node.func.id}")
            if isinstance(node.func, ast.Attribute) and node.func.attr in DENIED_ATTRIBUTES:
                raise SurrogateSuiteError(f"forbidden call: {node.func.attr}")


def _last_json_line(stdout: str) -> Any:
    for line in reversed(stdout.splitlines()):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    raise SurrogateSuiteError("surrogate suite did not emit JSON results")


def _parse_results(payload: Any, suite: TestSuiteVersion) -> list[AssertionResult]:
    if not isinstance(payload, list):
        raise SurrogateSuiteError("surrogate result must be a JSON array")
    expected = [item.assertion_id for item in suite.assertions]
    results: list[AssertionResult] = []
    for raw in payload:
        if not isinstance(raw, dict):
            raise SurrogateSuiteError("each surrogate result must be an object")
        assertion_id = str(raw.get("assertion_id") or "")
        passed = raw.get("passed")
        if not isinstance(passed, bool):
            raise SurrogateSuiteError(f"{assertion_id}: passed must be boolean")
        results.append(AssertionResult(assertion_id, passed, str(raw.get("message") or "")))
    actual = [item.assertion_id for item in results]
    if actual != expected:
        raise SurrogateSuiteError(f"assertion result ids/order mismatch: expected={expected}, actual={actual}")
    return results


def _ensure_newline(value: str) -> str:
    return value if value.endswith("\n") else value + "\n"
