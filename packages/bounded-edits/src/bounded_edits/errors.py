from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping


class ErrorCode(StrEnum):
    EMPTY_TARGET_SET = "empty_target_set"
    TOO_MANY_TARGETS = "too_many_targets"
    INVALID_PATH = "invalid_path"
    PATH_OUTSIDE_ROOT = "path_outside_root"
    SYMLINK_FORBIDDEN = "symlink_forbidden"
    FILE_NOT_FOUND = "file_not_found"
    FILE_TOO_LARGE = "file_too_large"
    INVALID_UTF8 = "invalid_utf8"
    BINARY_CONTENT = "binary_content"
    MIXED_LINE_ENDINGS = "mixed_line_endings"
    UNSUPPORTED_CR_LINE_ENDINGS = "unsupported_cr_line_endings"
    SOURCE_CHANGED = "source_changed"
    CONTRACT_MISMATCH = "contract_mismatch"
    RESPONSE_TOO_LARGE = "response_too_large"
    INVALID_JSON_TRANSPORT = "invalid_json_transport"
    DUPLICATE_JSON_KEY = "duplicate_json_key"
    NON_FINITE_JSON_VALUE = "non_finite_json_value"
    MORE_THAN_TWO_TOP_LEVEL_VALUES = "more_than_two_top_level_values"
    DUPLICATE_TOP_LEVEL_VALUES_DIFFER = "duplicate_top_level_values_differ"
    SCHEMA_MISMATCH = "schema_mismatch"
    UNKNOWN_FILE_ID = "unknown_file_id"
    INVALID_OPERATION = "invalid_operation"
    INVALID_OPERATION_FIELDS = "invalid_operation_fields"
    ANCHOR_WEAK = "anchor_weak"
    ANCHOR_NOT_FOUND = "anchor_not_found"
    ANCHOR_AMBIGUOUS = "anchor_ambiguous"
    INVALID_RANGE = "invalid_range"
    OVERLAPPING_EDITS = "overlapping_edits"
    EMPTY_CHANGE = "empty_change"
    GIT_NOT_AVAILABLE = "git_not_available"
    GIT_DIFF_FAILED = "git_diff_failed"
    GIT_APPLY_CHECK_FAILED = "git_apply_check_failed"


@dataclass(frozen=True)
class AuditEvent:
    code: str
    edit_index: int | None = None
    file_id: str | None = None
    details: Mapping[str, object] = field(default_factory=dict)


class BoundedEditError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        *,
        message: str | None = None,
        edit_index: int | None = None,
        file_id: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        self.code = code
        self.edit_index = edit_index
        self.file_id = file_id
        self.details = dict(details or {})
        super().__init__(message or code.value)
