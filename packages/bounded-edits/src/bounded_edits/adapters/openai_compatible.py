from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from ..errors import AuditEvent
from ..models import EditContract, ParsedResponse
from ..transport import parse_response


_SINGLE_JSON_FENCE = re.compile(
    r"\A\s*```json[ \t]*\r?\n(?P<body>.*?)\r?\n```\s*\Z", re.DOTALL
)


def response_format(contract: EditContract) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "bounded_edits",
            "strict": True,
            "schema": contract.json_schema,
        },
    }


def parse_openai_response(
    raw: str,
    *,
    contract: EditContract,
    unwrap_single_json_fence: bool = False,
) -> ParsedResponse:
    if not unwrap_single_json_fence:
        return parse_response(raw, contract=contract)
    match = _SINGLE_JSON_FENCE.fullmatch(raw)
    if match is None:
        return parse_response(raw, contract=contract)
    parsed = parse_response(match.group("body"), contract=contract)
    return replace(
        parsed,
        audit_events=(AuditEvent("single_json_fence_unwrapped"), *parsed.audit_events),
    )
