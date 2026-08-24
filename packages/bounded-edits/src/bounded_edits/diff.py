from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Mapping

from .documents import logical_to_bytes
from .errors import BoundedEditError, ErrorCode
from .models import WorkspaceSnapshot


_GIT_CONFIG = [
    "-c",
    "core.autocrlf=false",
    "-c",
    "core.safecrlf=false",
    "-c",
    "commit.gpgsign=false",
]


def canonical_diff(snapshot: WorkspaceSnapshot, changed: Mapping[str, bytes]) -> str:
    with tempfile.TemporaryDirectory(prefix="bounded-edits-diff-") as temporary:
        root = Path(temporary)
        for target in snapshot.targets:
            path = root.joinpath(*PurePosixPath(target.relative_path).parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(logical_to_bytes(target.logical_content, target.newline))
        _run_git(["init", "-q"], root, ErrorCode.GIT_DIFF_FAILED)
        _run_git(["add", "--all"], root, ErrorCode.GIT_DIFF_FAILED)
        _run_git(
            [
                "-c",
                "user.name=bounded-edits",
                "-c",
                "user.email=bounded-edits@invalid",
                "commit",
                "-q",
                "-m",
                "snapshot",
            ],
            root,
            ErrorCode.GIT_DIFF_FAILED,
        )
        for relative_path, content in changed.items():
            root.joinpath(*PurePosixPath(relative_path).parts).write_bytes(content)
        completed = _run_git(
            [
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--src-prefix=a/",
                "--dst-prefix=b/",
                "--",
            ],
            root,
            ErrorCode.GIT_DIFF_FAILED,
        )
        patch = completed.stdout.decode("utf-8")
        if not patch:
            raise BoundedEditError(ErrorCode.EMPTY_CHANGE)
        _run_git(["checkout", "-q", "--", "."], root, ErrorCode.GIT_DIFF_FAILED)
        validate_patch_applies(root, patch)
        return patch


def validate_patch_applies(root: Path, patch: str) -> None:
    completed = _run_git(
        ["apply", "--no-index", "--recount", "--check", "-"],
        root,
        ErrorCode.GIT_APPLY_CHECK_FAILED,
        input_bytes=patch.encode("utf-8"),
    )
    if completed.returncode != 0:
        raise BoundedEditError(
            ErrorCode.GIT_APPLY_CHECK_FAILED,
            details={"stderr": completed.stderr.decode("utf-8", errors="replace")},
        )


def _run_git(
    arguments: list[str],
    root: Path,
    error_code: ErrorCode,
    *,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    if shutil.which("git") is None:
        raise BoundedEditError(ErrorCode.GIT_NOT_AVAILABLE)
    env = os.environ.copy()
    for key in tuple(env):
        if key.startswith("GIT_"):
            env.pop(key)
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_DIR=str(root / ".git"),
        GIT_WORK_TREE=str(root),
    )
    completed = subprocess.run(
        ["git", *_GIT_CONFIG, *arguments],
        cwd=root,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=env,
    )
    if completed.returncode != 0 and error_code is not ErrorCode.GIT_APPLY_CHECK_FAILED:
        raise BoundedEditError(
            error_code,
            details={"stderr": completed.stderr.decode("utf-8", errors="replace")},
        )
    return completed
