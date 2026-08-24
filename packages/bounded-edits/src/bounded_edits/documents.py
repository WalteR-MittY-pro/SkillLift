from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Iterable, Literal

from .errors import BoundedEditError, ErrorCode
from .models import TargetFile, WorkspaceSnapshot


def normalize_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def logical_to_bytes(logical_content: str, newline: str) -> bytes:
    physical = (
        logical_content if newline == "\n" else logical_content.replace("\n", "\r\n")
    )
    return physical.encode("utf-8")


def capture_snapshot(
    root: Path | str,
    allowed_paths: Iterable[Path | str],
    *,
    max_files: int = 20,
    max_file_bytes: int = 2_000_000,
) -> WorkspaceSnapshot:
    try:
        resolved_root = Path(root).resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise BoundedEditError(ErrorCode.FILE_NOT_FOUND) from exc
    if not resolved_root.is_dir():
        raise BoundedEditError(ErrorCode.FILE_NOT_FOUND)

    relative_paths = sorted({_normalize_path(path) for path in allowed_paths})
    if not relative_paths:
        raise BoundedEditError(ErrorCode.EMPTY_TARGET_SET)
    if len(relative_paths) > max_files:
        raise BoundedEditError(ErrorCode.TOO_MANY_TARGETS)

    targets: list[TargetFile] = []
    for index, relative_path in enumerate(relative_paths):
        candidate = resolved_root.joinpath(*PurePosixPath(relative_path).parts)
        _reject_symlinks(resolved_root, candidate)
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(resolved_root)
        except FileNotFoundError as exc:
            raise BoundedEditError(
                ErrorCode.FILE_NOT_FOUND, details={"path": relative_path}
            ) from exc
        except ValueError as exc:
            raise BoundedEditError(
                ErrorCode.PATH_OUTSIDE_ROOT, details={"path": relative_path}
            ) from exc
        if not resolved.is_file():
            raise BoundedEditError(
                ErrorCode.FILE_NOT_FOUND, details={"path": relative_path}
            )

        raw = resolved.read_bytes()
        if len(raw) > max_file_bytes:
            raise BoundedEditError(
                ErrorCode.FILE_TOO_LARGE, details={"path": relative_path}
            )
        logical, newline = _decode_text(raw, relative_path)
        targets.append(
            TargetFile(
                file_id=f"f{index}",
                relative_path=relative_path,
                logical_content=logical,
                newline=newline,
                final_newline=logical.endswith("\n"),
                content_sha256=hashlib.sha256(raw).hexdigest(),
            )
        )

    digest = hashlib.sha256()
    for target in targets:
        digest.update(target.relative_path.encode())
        digest.update(b"\0")
        digest.update(target.content_sha256.encode())
        digest.update(b"\0")
    return WorkspaceSnapshot(resolved_root, tuple(targets), digest.hexdigest())


def assert_snapshot_current(snapshot: WorkspaceSnapshot) -> None:
    for target in snapshot.targets:
        path = snapshot.root.joinpath(*PurePosixPath(target.relative_path).parts)
        try:
            _reject_symlinks(snapshot.root, path)
            raw = path.read_bytes()
        except (OSError, BoundedEditError) as exc:
            raise BoundedEditError(
                ErrorCode.SOURCE_CHANGED, details={"path": target.relative_path}
            ) from exc
        if hashlib.sha256(raw).hexdigest() != target.content_sha256:
            raise BoundedEditError(
                ErrorCode.SOURCE_CHANGED, details={"path": target.relative_path}
            )


def _normalize_path(path: Path | str) -> str:
    raw = str(path)
    if not raw or "\\" in raw:
        raise BoundedEditError(ErrorCode.INVALID_PATH, details={"path": raw})
    pure = PurePosixPath(raw)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise BoundedEditError(ErrorCode.INVALID_PATH, details={"path": raw})
    return pure.as_posix()


def _reject_symlinks(root: Path, candidate: Path) -> None:
    current = root
    for part in candidate.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise BoundedEditError(
                ErrorCode.SYMLINK_FORBIDDEN,
                details={"path": candidate.relative_to(root).as_posix()},
            )


def _decode_text(raw: bytes, relative_path: str) -> tuple[str, Literal["\n", "\r\n"]]:
    if b"\0" in raw:
        raise BoundedEditError(
            ErrorCode.BINARY_CONTENT, details={"path": relative_path}
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BoundedEditError(
            ErrorCode.INVALID_UTF8, details={"path": relative_path}
        ) from exc

    without_crlf = raw.replace(b"\r\n", b"")
    if b"\r" in without_crlf:
        raise BoundedEditError(
            ErrorCode.UNSUPPORTED_CR_LINE_ENDINGS, details={"path": relative_path}
        )
    has_crlf = b"\r\n" in raw
    if has_crlf and b"\n" in without_crlf:
        raise BoundedEditError(
            ErrorCode.MIXED_LINE_ENDINGS, details={"path": relative_path}
        )
    newline: Literal["\n", "\r\n"] = "\r\n" if has_crlf else "\n"
    return text.replace("\r\n", "\n"), newline
