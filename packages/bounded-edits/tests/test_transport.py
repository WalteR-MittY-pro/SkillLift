import json

import pytest

from bounded_edits import (
    BoundedEditError,
    ErrorCode,
    EditOp,
    build_contract,
    capture_snapshot,
    parse_response,
)
from bounded_edits.adapters.openai_compatible import parse_openai_response


def _contract(tmp_path):
    (tmp_path / "skill.md").write_text("# Skill\n", encoding="utf-8")
    return build_contract(capture_snapshot(tmp_path, ["skill.md"]))


def _payload(content="new text"):
    return {
        "schema_version": 1,
        "edits": [
            {
                "file_id": "f0",
                "op": "append",
                "start_anchor": None,
                "end_anchor": None,
                "content": content,
            }
        ],
    }


def test_single_object_is_accepted(tmp_path):
    parsed = parse_response(json.dumps(_payload()), contract=_contract(tmp_path))

    assert parsed.batch.edits[0].file_id == "f0"
    assert parsed.audit_events == ()


def test_two_identical_objects_are_deduplicated_and_audited(tmp_path):
    first = json.dumps(_payload(), separators=(",", ":"))
    second = json.dumps(_payload(), indent=2)

    parsed = parse_response(first + second, contract=_contract(tmp_path))

    assert len(parsed.batch.edits) == 1
    assert [event.code for event in parsed.audit_events] == [
        "duplicate_identical_response"
    ]


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (
            json.dumps(_payload()) + json.dumps(_payload("different")),
            ErrorCode.DUPLICATE_TOP_LEVEL_VALUES_DIFFER,
        ),
        (json.dumps(_payload()) * 3, ErrorCode.MORE_THAN_TWO_TOP_LEVEL_VALUES),
        (json.dumps(_payload()) + " prose", ErrorCode.INVALID_JSON_TRANSPORT),
        (
            "```json\n" + json.dumps(_payload()) + "\n```",
            ErrorCode.INVALID_JSON_TRANSPORT,
        ),
        (
            '{"schema_version":1,"schema_version":1,"edits":[]}',
            ErrorCode.DUPLICATE_JSON_KEY,
        ),
        ('{"schema_version":NaN,"edits":[]}', ErrorCode.NON_FINITE_JSON_VALUE),
    ],
)
def test_invalid_transport_is_rejected(tmp_path, raw, code):
    with pytest.raises(BoundedEditError) as raised:
        parse_response(raw, contract=_contract(tmp_path))

    assert raised.value.code is code


def test_adapter_optionally_unwraps_exactly_one_json_fence(tmp_path):
    raw = "  \n```json\n" + json.dumps(_payload()) + "\n```\t"

    parsed = parse_openai_response(
        raw,
        contract=_contract(tmp_path),
        unwrap_single_json_fence=True,
    )

    assert [event.code for event in parsed.audit_events] == [
        "single_json_fence_unwrapped"
    ]


@pytest.mark.parametrize(
    "raw",
    [
        "prose\n```json\n{}\n```",
        "```json\n{}\n```\nprose",
        "```json\n{}\n```\n```json\n{}\n```",
        "```JSON\n{}\n```",
    ],
)
def test_adapter_never_extracts_json_from_prose_or_multiple_fences(tmp_path, raw):
    with pytest.raises(BoundedEditError) as raised:
        parse_openai_response(
            raw,
            contract=_contract(tmp_path),
            unwrap_single_json_fence=True,
        )

    assert raised.value.code is ErrorCode.INVALID_JSON_TRANSPORT


def test_unknown_file_and_extra_fields_are_rejected(tmp_path):
    payload = _payload()
    payload["edits"][0]["file_id"] = "f9"
    with pytest.raises(BoundedEditError) as raised:
        parse_response(json.dumps(payload), contract=_contract(tmp_path))
    assert raised.value.code is ErrorCode.UNKNOWN_FILE_ID

    payload = _payload()
    payload["edits"][0]["path"] = "skill.md"
    with pytest.raises(BoundedEditError) as raised:
        parse_response(json.dumps(payload), contract=_contract(tmp_path))
    assert raised.value.code is ErrorCode.SCHEMA_MISMATCH


def test_operation_outside_contract_policy_is_rejected(tmp_path):
    path = tmp_path / "skill.md"
    path.write_text("# Skill\n", encoding="utf-8")
    contract = build_contract(
        capture_snapshot(tmp_path, ["skill.md"]),
        allowed_ops=(EditOp.REPLACE_RANGE,),
    )

    with pytest.raises(BoundedEditError) as raised:
        parse_response(json.dumps(_payload()), contract=contract)

    assert raised.value.code is ErrorCode.INVALID_OPERATION
