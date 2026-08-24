from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def append_usage_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def summarize_usage(path: Path) -> dict[str, Any]:
    records: dict[str, dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            records[str(record["usage_record_id"])] = record
    return {
        "attempts": len(records),
        "total_tokens": sum(record.get("total_tokens") or 0 for record in records.values()),
        "total_cost_usd": sum(record.get("cost_usd") or 0.0 for record in records.values()),
        "by_role": {
            role: {
                "attempts": sum(1 for record in records.values() if record.get("role") == role),
                "total_tokens": sum(
                    record.get("total_tokens") or 0
                    for record in records.values()
                    if record.get("role") == role
                ),
            }
            for role in sorted({str(record.get("role")) for record in records.values()})
        },
    }
