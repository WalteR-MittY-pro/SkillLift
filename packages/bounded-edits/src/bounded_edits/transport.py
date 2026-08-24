from __future__ import annotations

import json
from typing import Any

from .errors import AuditEvent, BoundedEditError, ErrorCode
from .models import EditBatch, EditContract, EditOp, ParsedResponse, WireEdit


_TOP_LEVEL_KEYS = {"schema_version", "edits"}
_EDIT_KEYS = {"file_id", "op", "start_anchor", "end_anchor", "content"}


def parse_response(raw: str, *, contract: EditContract) -> ParsedResponse:
    if not isinstance(raw, str):
        raise BoundedEditError(ErrorCode.INVALID_JSON_TRANSPORT)
    if len(raw.encode("utf-8")) > contract.max_response_bytes:
        raise BoundedEditError(ErrorCode.RESPONSE_TOO_LARGE)

    values = _decode_top_level_values(raw)
    audit_events: tuple[AuditEvent, ...] = ()
    if len(values) == 2:
        if _canonical_json(values[0]) != _canonical_json(values[1]):
            raise BoundedEditError(ErrorCode.DUPLICATE_TOP_LEVEL_VALUES_DIFFER)
        audit_events = (AuditEvent("duplicate_identical_response"),)
    payload = values[0]
    batch = _validate_payload(payload, contract)
    return ParsedResponse(contract.snapshot_id, batch, audit_events)


def _decode_top_level_values(raw: str) -> list[Any]:
    decoder = json.JSONDecoder(
        object_pairs_hook=_object_without_duplicate_keys,
        parse_constant=_reject_non_finite,
    )
    values: list[Any] = []
    position = 0
    while True:
        while position < len(raw) and raw[position].isspace():
            position += 1
        if position == len(raw):
            break
        try:
            value, position = decoder.raw_decode(raw, position)
        except json.JSONDecodeError as exc:
            raise BoundedEditError(ErrorCode.INVALID_JSON_TRANSPORT) from exc
        values.append(value)
        if len(values) > 2:
            raise BoundedEditError(ErrorCode.MORE_THAN_TWO_TOP_LEVEL_VALUES)
    if not values:
        raise BoundedEditError(ErrorCode.INVALID_JSON_TRANSPORT)
    return values


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BoundedEditError(ErrorCode.DUPLICATE_JSON_KEY)
        result[key] = value
    return result


def _reject_non_finite(_value: str) -> None:
    raise BoundedEditError(ErrorCode.NON_FINITE_JSON_VALUE)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validate_payload(payload: Any, contract: EditContract) -> EditBatch:
    if not isinstance(payload, dict) or set(payload) != _TOP_LEVEL_KEYS:
        raise BoundedEditError(ErrorCode.SCHEMA_MISMATCH)
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise BoundedEditError(ErrorCode.SCHEMA_MISMATCH)
    edits = payload["edits"]
    if not isinstance(edits, list) or not 1 <= len(edits) <= contract.max_edits:
        raise BoundedEditError(ErrorCode.SCHEMA_MISMATCH)
    return EditBatch(
        1,
        tuple(
            _validate_edit(value, contract, index) for index, value in enumerate(edits)
        ),
    )


def _validate_edit(value: Any, contract: EditContract, index: int) -> WireEdit:
    if not isinstance(value, dict) or set(value) != _EDIT_KEYS:
        raise BoundedEditError(ErrorCode.SCHEMA_MISMATCH, edit_index=index)
    file_id = value["file_id"]
    if not isinstance(file_id, str):
        raise BoundedEditError(ErrorCode.SCHEMA_MISMATCH, edit_index=index)
    if file_id not in contract.allowed_file_ids:
        raise BoundedEditError(
            ErrorCode.UNKNOWN_FILE_ID, edit_index=index, file_id=file_id
        )
    try:
        operation = EditOp(value["op"])
    except (TypeError, ValueError) as exc:
        raise BoundedEditError(
            ErrorCode.INVALID_OPERATION, edit_index=index, file_id=file_id
        ) from exc
    if operation not in contract.allowed_ops:
        raise BoundedEditError(
            ErrorCode.INVALID_OPERATION, edit_index=index, file_id=file_id
        )

    start_anchor = value["start_anchor"]
    end_anchor = value["end_anchor"]
    content = value["content"]
    if not _optional_string(start_anchor, contract.max_anchor_chars):
        raise BoundedEditError(
            ErrorCode.SCHEMA_MISMATCH, edit_index=index, file_id=file_id
        )
    if not _optional_string(end_anchor, contract.max_anchor_chars):
        raise BoundedEditError(
            ErrorCode.SCHEMA_MISMATCH, edit_index=index, file_id=file_id
        )
    if not isinstance(content, str) or len(content) > contract.max_content_chars:
        raise BoundedEditError(
            ErrorCode.SCHEMA_MISMATCH, edit_index=index, file_id=file_id
        )
    _validate_operation_fields(
        operation, start_anchor, end_anchor, content, index, file_id
    )
    return WireEdit(file_id, operation, start_anchor, end_anchor, content)


def _optional_string(value: Any, maximum: int) -> bool:
    return value is None or isinstance(value, str) and len(value) <= maximum


def _validate_operation_fields(
    operation: EditOp,
    start_anchor: str | None,
    end_anchor: str | None,
    content: str,
    index: int,
    file_id: str,
) -> None:
    valid = False
    if operation is EditOp.APPEND:
        valid = start_anchor is None and end_anchor is None and bool(content)
    elif operation in {EditOp.INSERT_BEFORE, EditOp.INSERT_AFTER}:
        valid = bool(start_anchor) and end_anchor is None and bool(content)
    elif operation is EditOp.REPLACE_RANGE:
        valid = bool(start_anchor) and bool(end_anchor) and bool(content)
    elif operation is EditOp.DELETE_RANGE:
        valid = bool(start_anchor) and bool(end_anchor) and content == ""
    if not valid:
        raise BoundedEditError(
            ErrorCode.INVALID_OPERATION_FIELDS, edit_index=index, file_id=file_id
        )
