from __future__ import annotations

from typing import Any

from skilllift_eval.tokens import normalize_usage


SCORE_PARSER_VERSION = "tau2_scores_v1"


def parse_tau2_simulation(simulation: Any) -> dict[str, Any]:
    reward_info = _model_dump(getattr(simulation, "reward_info", None))
    if not reward_info or reward_info.get("reward") is None:
        raise ValueError("tau2 simulation missing reward_info.reward")

    usage = _aggregate_message_usage(_messages(simulation))
    token_usage = normalize_usage(usage)
    cost = _sum_costs(
        getattr(simulation, "agent_cost", None),
        getattr(simulation, "user_cost", None),
    )
    return {
        "benchmark": "tau2",
        "score_parser_version": SCORE_PARSER_VERSION,
        "task_id": str(getattr(simulation, "task_id", "")),
        "score": float(reward_info["reward"]),
        "reward_info": reward_info,
        "usage": token_usage.to_dict(),
        "cost_actual": cost if cost is not None else token_usage.cost_actual,
    }


def simulation_to_jsonable(simulation: Any) -> dict[str, Any]:
    dumped = _model_dump(simulation)
    if dumped:
        return dumped
    return {
        "id": getattr(simulation, "id", None),
        "task_id": getattr(simulation, "task_id", None),
        "reward_info": _model_dump(getattr(simulation, "reward_info", None)),
        "messages": [_model_dump(message) for message in _messages(simulation)],
        "agent_cost": getattr(simulation, "agent_cost", None),
        "user_cost": getattr(simulation, "user_cost", None),
    }


def messages_for_feedback(simulation: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in _messages(simulation):
        data = _model_dump(message)
        result.append(
            {
                "role": data.get("role", getattr(message, "role", "")),
                "content": data.get("content", getattr(message, "content", "")),
            }
        )
    return result


def _messages(simulation: Any) -> list[Any]:
    if hasattr(simulation, "get_messages"):
        return list(simulation.get_messages())
    return list(getattr(simulation, "messages", None) or [])


def _aggregate_message_usage(messages: list[Any]) -> dict[str, Any] | None:
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    found = False
    for message in messages:
        usage = _usage(message)
        if not usage:
            continue
        found = True
        totals["prompt_tokens"] += int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        totals["completion_tokens"] += int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        totals["total_tokens"] += int(usage.get("total_tokens") or 0)
    if not found:
        return None
    if totals["total_tokens"] == 0:
        totals["total_tokens"] = totals["prompt_tokens"] + totals["completion_tokens"]
    return totals


def _usage(message: Any) -> dict[str, Any] | None:
    if isinstance(message, dict):
        return message.get("usage")
    return getattr(message, "usage", None)


def _model_dump(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return {}


def _sum_costs(*costs: Any) -> float | None:
    total = 0.0
    found = False
    for cost in costs:
        if cost is None:
            continue
        total += float(cost)
        found = True
    return total if found else None
