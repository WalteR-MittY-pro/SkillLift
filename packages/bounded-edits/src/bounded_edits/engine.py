from __future__ import annotations

from dataclasses import dataclass
import re
from .diff import canonical_diff
from .documents import assert_snapshot_current, logical_to_bytes, normalize_newlines
from .errors import BoundedEditError, ErrorCode
from .models import EditOp, ParsedResponse, PreparedPatch, WireEdit, WorkspaceSnapshot


@dataclass(frozen=True)
class _ResolvedEdit:
    index: int
    file_id: str
    start: int
    end: int
    replacement: str

    @property
    def is_insertion(self) -> bool:
        return self.start == self.end


def prepare_patch(
    *, snapshot: WorkspaceSnapshot, parsed: ParsedResponse
) -> PreparedPatch:
    if parsed.snapshot_id != snapshot.snapshot_id:
        raise BoundedEditError(ErrorCode.CONTRACT_MISMATCH)
    assert_snapshot_current(snapshot)

    by_file: dict[str, list[_ResolvedEdit]] = {}
    for index, edit in enumerate(parsed.batch.edits):
        target = snapshot.target_by_id.get(edit.file_id)
        if target is None:
            raise BoundedEditError(ErrorCode.CONTRACT_MISMATCH)
        by_file.setdefault(edit.file_id, []).append(
            _resolve_edit(target.logical_content, edit, index)
        )

    changed: dict[str, bytes] = {}
    for file_id, edits in by_file.items():
        _validate_no_conflicts(edits)
        target = snapshot.target_by_id[file_id]
        logical = _apply_resolved(target.logical_content, edits)
        content = logical_to_bytes(logical, target.newline)
        original = logical_to_bytes(target.logical_content, target.newline)
        if content != original:
            changed[target.relative_path] = content
    if not changed:
        raise BoundedEditError(ErrorCode.EMPTY_CHANGE)

    patch = canonical_diff(snapshot, changed)
    assert_snapshot_current(snapshot)
    changed_paths = tuple(sorted(changed))
    return PreparedPatch(
        unified_diff=patch,
        changed_paths=changed_paths,
        source_hashes=tuple(
            (target.relative_path, target.content_sha256) for target in snapshot.targets
        ),
        audit_events=parsed.audit_events,
    )


def _resolve_edit(text: str, edit: WireEdit, index: int) -> _ResolvedEdit:
    content = normalize_newlines(edit.content)
    if edit.op == EditOp.APPEND:
        return _ResolvedEdit(index, edit.file_id, len(text), len(text), content)

    start_anchor = normalize_newlines(edit.start_anchor or "")
    start_offset = _resolve_position(text, start_anchor, index, edit.file_id)
    if edit.op == EditOp.INSERT_BEFORE:
        return _ResolvedEdit(index, edit.file_id, start_offset, start_offset, content)
    if edit.op == EditOp.INSERT_AFTER:
        offset = _line_end(text, start_anchor, start_offset)
        return _ResolvedEdit(index, edit.file_id, offset, offset, content)

    end_anchor = normalize_newlines(edit.end_anchor or "")
    end_offset = _resolve_position(text, end_anchor, index, edit.file_id)
    minimum_end = (
        start_offset + 1
        if _LINE_ID_RE.fullmatch(start_anchor)
        else start_offset + len(start_anchor)
    )
    if end_offset < minimum_end:
        raise BoundedEditError(
            ErrorCode.INVALID_RANGE, edit_index=index, file_id=edit.file_id
        )
    end = end_offset
    if edit.op == EditOp.REPLACE_RANGE:
        replacement = content
    elif edit.op == EditOp.DELETE_RANGE:
        replacement = ""
    else:
        # WireEdits constructed directly (bypassing parse_response) may carry
        # a plain-string op. Fail closed on anything unrecognized instead of
        # silently degrading a replace into a delete.
        raise BoundedEditError(
            ErrorCode.INVALID_OPERATION, edit_index=index, file_id=edit.file_id
        )
    return _ResolvedEdit(index, edit.file_id, start_offset, end, replacement)


def _validate_anchor(anchor: str, index: int, file_id: str) -> None:
    if not any(character.isalnum() for character in anchor):
        raise BoundedEditError(ErrorCode.ANCHOR_WEAK, edit_index=index, file_id=file_id)


_LINE_ID_RE = re.compile(r"^L(\d{4,})$")


def _resolve_position(text: str, anchor: str, index: int, file_id: str) -> int:
    match = _LINE_ID_RE.fullmatch(anchor)
    if match:
        line_number = int(match.group(1))
        lines = text.splitlines(keepends=True)
        if not 1 <= line_number <= len(lines) + 1:
            raise BoundedEditError(
                ErrorCode.ANCHOR_NOT_FOUND,
                edit_index=index,
                file_id=file_id,
                details={"line_id": anchor},
            )
        return sum(len(line) for line in lines[: line_number - 1])
    # Keep old persisted responses readable; new prompts and schemas use IDs.
    _validate_anchor(anchor, index, file_id)
    return _unique_offset(text, anchor, index, file_id)


def _line_end(text: str, anchor: str, start_offset: int) -> int:
    if _LINE_ID_RE.fullmatch(anchor):
        lines = text.splitlines(keepends=True)
        line_number = int(anchor[1:])
        return start_offset + (len(lines[line_number - 1]) if line_number <= len(lines) else 0)
    return start_offset + len(anchor)


def _unique_offset(text: str, anchor: str, index: int, file_id: str) -> int:
    first = text.find(anchor)
    if first < 0:
        raise BoundedEditError(
            ErrorCode.ANCHOR_NOT_FOUND, edit_index=index, file_id=file_id
        )
    if text.find(anchor, first + 1) >= 0:
        raise BoundedEditError(
            ErrorCode.ANCHOR_AMBIGUOUS, edit_index=index, file_id=file_id
        )
    return first


def _validate_no_conflicts(edits: list[_ResolvedEdit]) -> None:
    for left_index, left in enumerate(edits):
        for right in edits[left_index + 1 :]:
            if _conflicts(left, right):
                raise BoundedEditError(
                    ErrorCode.OVERLAPPING_EDITS,
                    edit_index=right.index,
                    file_id=right.file_id,
                )


def _conflicts(left: _ResolvedEdit, right: _ResolvedEdit) -> bool:
    if left.is_insertion and right.is_insertion:
        return left.start == right.start
    if left.is_insertion:
        return right.start <= left.start <= right.end
    if right.is_insertion:
        return left.start <= right.start <= left.end
    return max(left.start, right.start) <= min(left.end, right.end)


def _apply_resolved(text: str, edits: list[_ResolvedEdit]) -> str:
    result = text
    for edit in sorted(
        edits, key=lambda value: (value.start, value.end, value.index), reverse=True
    ):
        result = result[: edit.start] + edit.replacement + result[edit.end :]
    return result
