from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace

from skilllift_eval.runners.coevoskills_wildclaw import _EvolutionTokenBudget
from skilllift_eval.token_accounting import (
    backfill_skilllift_reference_usage,
    estimate_skilllift_task_usage,
    load_reference_budget,
    task_key_from_path,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_historical_accounting_splits_framework_estimate_and_exact_oracle(
    tmp_path: Path,
) -> None:
    task_dir = tmp_path / "01-01"
    framework_file = task_dir / "skilllift" / "skills" / "initial" / "skill.json"
    framework_file.parent.mkdir(parents=True)
    framework_file.write_bytes(b"x" * 17)
    _write_json(
        task_dir / "raw" / "run-a" / "usage.json",
        {
            "input_tokens": 10,
            "output_tokens": 2,
            "cache_read_tokens": 3,
            "total_tokens": 15,
            "request_count": 2,
        },
    )
    _write_json(
        task_dir / "raw" / "run-b" / "usage.json",
        {"input_tokens": 20, "output_tokens": 4, "total_tokens": 24, "request_count": 1},
    )

    payload = estimate_skilllift_task_usage(task_dir, budget_multiplier=2.0)

    assert payload["measurement"]["framework"]["total_tokens"] == math.ceil(17 / 4)
    assert payload["measurement"]["framework"]["estimated"] is True
    assert payload["measurement"]["oracle"]["total_tokens"] == 39
    assert payload["measurement"]["oracle"]["request_count"] == 3
    assert payload["measurement"]["oracle"]["estimated"] is False
    assert payload["budget"]["limit_tokens"] == 2 * (5 + 39)


def test_backfill_and_reference_budget_ignore_non_task_directories(tmp_path: Path) -> None:
    task_dir = tmp_path / "01-02"
    (task_dir / "skilllift").mkdir(parents=True)
    _write_json(task_dir / "raw" / "run" / "usage.json", {"total_tokens": 25})
    (tmp_path / "tmp-probe" / "skilllift").mkdir(parents=True)

    reports = backfill_skilllift_reference_usage(tmp_path)
    budget = load_reference_budget(tmp_path, "01-02", multiplier=2.5)

    assert reports == [task_dir / "token_usage.json"]
    assert budget["base_tokens"] == 25
    assert budget["limit_tokens"] == 63


def test_load_reference_budget_from_summary_file(tmp_path: Path) -> None:
    summary = tmp_path / "token_usage_summary.json"
    _write_json(
        summary,
        {
            "task_count": 2,
            "per_task": [
                {"task_id": "01-01", "combined_total_tokens": 321},
                {
                    "task_id": "01-02",
                    "oracle_total_tokens": 100,
                    "framework_total_tokens_est": 23,
                    "combined_total_tokens": 0,
                },
            ],
        },
    )

    direct = load_reference_budget(summary, "01-01", multiplier=1.0)
    fallback = load_reference_budget(summary, "01-02", multiplier=1.5)

    assert direct["base_tokens"] == 321
    assert direct["limit_tokens"] == 321
    assert direct["reference_path"] == str(summary.resolve())
    assert fallback["base_tokens"] == 123
    assert fallback["limit_tokens"] == 185


def test_task_key_from_wildclaw_path() -> None:
    path = Path("tasks/06_Safety_Alignment/06_Safety_Alignment_task_1_file_overwrite.md")
    assert task_key_from_path(path) == "06-01"


def test_live_evolution_budget_combines_framework_and_docker_usage(tmp_path: Path) -> None:
    raw = tmp_path / "oracle_raw.json"
    _write_json(raw, {"usage": {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25}})
    client = SimpleNamespace(total_tokens=10, request_count=2)
    artifact_backend = SimpleNamespace(
        records=[SimpleNamespace(usage={"input_tokens": 12, "output_tokens": 3, "total_tokens": 15})]
    )
    oracle_backend = SimpleNamespace(
        evolution_records=[SimpleNamespace(raw_output_path=str(raw), token_actual=25)]
    )
    tracker = _EvolutionTokenBudget(
        tmp_path / "run",
        generator=SimpleNamespace(client=client),
        verifier=SimpleNamespace(client=client),
        artifact_backend=artifact_backend,
        oracle_backend=oracle_backend,
        reference={
            "task_key": "01-01",
            "reference_path": "/reference/01-01/token_usage.json",
            "base_tokens": 24,
            "multiplier": 2.0,
            "limit_tokens": 48,
        },
    )

    assert tracker.checkpoint(turns_completed=1, stage="oracle_evaluated") is True
    payload = json.loads((tmp_path / "run" / "token_usage.json").read_text())
    assert payload["usage"]["framework"]["total_tokens"] == 10
    assert payload["usage"]["oracle"]["total_tokens"] == 40
    assert payload["usage"]["total_tokens"] == 50
    assert payload["budget"]["overshoot_tokens"] == 2
