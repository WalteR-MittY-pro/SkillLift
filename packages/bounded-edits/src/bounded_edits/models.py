from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Mapping

from .errors import AuditEvent


class EditOp(StrEnum):
    APPEND = "append"
    INSERT_BEFORE = "insert_before"
    INSERT_AFTER = "insert_after"
    REPLACE_RANGE = "replace_range"
    DELETE_RANGE = "delete_range"


@dataclass(frozen=True)
class TargetFile:
    file_id: str
    relative_path: str
    logical_content: str
    newline: Literal["\n", "\r\n"]
    final_newline: bool
    content_sha256: str


@dataclass(frozen=True)
class WorkspaceSnapshot:
    root: Path
    targets: tuple[TargetFile, ...]
    snapshot_id: str

    @property
    def target_by_id(self) -> Mapping[str, TargetFile]:
        return {target.file_id: target for target in self.targets}


@dataclass(frozen=True)
class EditContract:
    protocol_version: int
    snapshot_id: str
    targets: tuple[TargetFile, ...]
    allowed_ops: frozenset[EditOp]
    json_schema: dict[str, Any]
    protocol_instructions: str
    target_context: str
    max_edits: int
    max_response_bytes: int
    max_anchor_chars: int
    max_content_chars: int

    @property
    def allowed_file_ids(self) -> frozenset[str]:
        return frozenset(target.file_id for target in self.targets)


@dataclass(frozen=True)
class WireEdit:
    file_id: str
    op: EditOp
    start_anchor: str | None
    end_anchor: str | None
    content: str


@dataclass(frozen=True)
class EditBatch:
    schema_version: int
    edits: tuple[WireEdit, ...]


@dataclass(frozen=True)
class ParsedResponse:
    snapshot_id: str
    batch: EditBatch
    audit_events: tuple[AuditEvent, ...] = ()


@dataclass(frozen=True)
class PreparedPatch:
    unified_diff: str
    changed_paths: tuple[str, ...]
    source_hashes: tuple[tuple[str, str], ...]
    audit_events: tuple[AuditEvent, ...] = ()
