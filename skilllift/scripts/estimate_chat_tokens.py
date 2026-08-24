#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import tiktoken


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def read_chat_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            event = json.loads(line)
            event["_line_number"] = line_number
            events.append(event)
    return events


def find_chat_paths(target: Path) -> list[Path]:
    target = target.expanduser()
    if target.is_file():
        return [target]
    direct = target / "chat.jsonl"
    if direct.exists():
        return [direct]
    return sorted(target.rglob("chat.jsonl"))


def choose_model(events: list[dict[str, Any]], fallback: str) -> str:
    for event in events:
        if event.get("type") == "model_change" and event.get("modelId"):
            return str(event["modelId"])
    for event in events:
        message = event.get("message") or {}
        if message.get("model"):
            return str(message["model"])
    return fallback


def get_encoding(model: str):
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding("o200k_base")


def token_count(encoding: Any, text: str) -> int:
    return len(encoding.encode(text))


def normalize_usage(usage: dict[str, Any] | None) -> dict[str, float]:
    if not usage:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
        }

    cost = usage.get("cost", {})
    input_tokens = usage.get("input_tokens", usage.get("input", 0)) or 0
    output_tokens = usage.get("output_tokens", usage.get("output", 0)) or 0
    cache_read = usage.get("cache_read_tokens", usage.get("cacheRead", 0)) or 0
    cache_write = usage.get("cache_write_tokens", usage.get("cacheWrite", 0)) or 0
    total = usage.get("total_tokens", usage.get("totalTokens", 0)) or 0
    if not total:
        total = input_tokens + output_tokens + cache_read + cache_write
    cost_usd = usage.get("cost_usd", 0.0) or 0.0
    if isinstance(cost, dict):
        cost_usd = cost.get("total", cost_usd) or cost_usd

    return {
        "input_tokens": float(input_tokens),
        "output_tokens": float(output_tokens),
        "cache_read_tokens": float(cache_read),
        "cache_write_tokens": float(cache_write),
        "total_tokens": float(total),
        "cost_usd": float(cost_usd),
    }


def add_usage(left: dict[str, float], right: dict[str, float]) -> dict[str, float]:
    return {key: left.get(key, 0.0) + right.get(key, 0.0) for key in set(left) | set(right)}


def usage_is_nonzero(usage: dict[str, float]) -> bool:
    return any(usage.get(key, 0) for key in ("input_tokens", "output_tokens", "total_tokens", "cost_usd"))


def message_to_visible_text(message: dict[str, Any]) -> tuple[str, Counter[str], list[dict[str, Any]], list[dict[str, Any]]]:
    role = str(message.get("role") or "")
    parts = [f"role: {role}"]
    content_counts: Counter[str] = Counter()
    image_attachments: list[dict[str, Any]] = []
    image_descriptions: list[dict[str, Any]] = []

    for item in message.get("content") or []:
        item_type = str(item.get("type") or "unknown")
        content_counts[item_type] += 1

        if item_type == "text":
            text = str(item.get("text", ""))
            parts.append(f"text:\n{text}")
            image_descriptions.extend(find_image_descriptions(text))
        elif item_type == "thinking":
            parts.append(f"thinking:\n{item.get('thinking', '')}")
        elif item_type == "toolCall":
            parts.append(
                "toolCall:\n"
                + json.dumps(
                    {
                        "id": item.get("id"),
                        "name": item.get("name"),
                        "arguments": item.get("arguments", {}),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        elif item_type == "image":
            data = str(item.get("data", ""))
            mime_type = str(item.get("mimeType") or "")
            image_attachments.append({"mime_type": mime_type, "base64_chars": len(data)})
            parts.append(f"image {mime_type}: [base64 omitted, chars={len(data)}]")
        else:
            parts.append(f"{item_type}:\n{json.dumps(item, ensure_ascii=False, sort_keys=True)}")

    return "\n".join(parts), content_counts, image_attachments, image_descriptions


def find_image_descriptions(text: str) -> list[dict[str, Any]]:
    images = []
    for match in re.finditer(r"original\s+(\d+)x(\d+)", text):
        images.append({"width": int(match.group(1)), "height": int(match.group(2))})
    return images


def estimate_chat(chat_path: Path, fallback_model: str) -> dict[str, Any]:
    events = read_chat_events(chat_path)
    model = choose_model(events, fallback_model)
    encoding = get_encoding(model)
    usage_json = normalize_usage(read_json(chat_path.with_name("usage.json")))

    chat_usage = normalize_usage(None)
    per_call: list[dict[str, Any]] = []
    history: list[str] = []
    message_events = 0
    role_counts: Counter[str] = Counter()
    content_counts: Counter[str] = Counter()
    image_attachments: list[dict[str, Any]] = []
    image_descriptions: list[dict[str, Any]] = []
    transcript_once_parts: list[str] = []

    for event in events:
        if event.get("type") != "message":
            continue

        message = event.get("message") or {}
        message_events += 1
        role = str(message.get("role") or "unknown")
        role_counts[role] += 1
        chat_usage = add_usage(chat_usage, normalize_usage(message.get("usage")))

        message_text, counts, message_images, message_image_descriptions = message_to_visible_text(message)
        content_counts.update(counts)
        image_attachments.extend(message_images)
        image_descriptions.extend(message_image_descriptions)

        if role == "assistant":
            input_text = "\n\n".join(history)
            input_tokens = token_count(encoding, input_text)
            output_tokens = token_count(encoding, message_text)
            per_call.append(
                {
                    "line": event.get("_line_number"),
                    "stop_reason": message.get("stopReason"),
                    "input_tokens_est": input_tokens,
                    "output_tokens_est": output_tokens,
                }
            )

        history.append(message_text)
        transcript_once_parts.append(message_text)

    cumulative_input = sum(call["input_tokens_est"] for call in per_call)
    assistant_output = sum(call["output_tokens_est"] for call in per_call)
    visible_once = token_count(encoding, "\n\n".join(transcript_once_parts))

    return {
        "chat_path": str(chat_path),
        "run_dir": str(chat_path.parent),
        "model": model,
        "encoding": encoding.name,
        "event_count": len(events),
        "message_event_count": message_events,
        "request_count_est": len(per_call),
        "role_counts": dict(sorted(role_counts.items())),
        "content_counts": dict(sorted(content_counts.items())),
        "usage_json": usage_json,
        "chat_usage_sum": chat_usage,
        "visible_text_once_tokens_est": visible_once,
        "cumulative_input_tokens_est": cumulative_input,
        "assistant_output_tokens_est": assistant_output,
        "total_text_tokens_est": cumulative_input + assistant_output,
        "image_attachment_count": len(image_attachments),
        "image_description_count": len(image_descriptions),
        "image_attachments": image_attachments,
        "image_descriptions": image_descriptions,
        "per_call": per_call,
    }


def format_int(value: float | int) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return f"{value:,}"


def print_usage_block(label: str, usage: dict[str, float]) -> None:
    print(f"{label}:")
    print(f"  input_tokens:       {format_int(usage['input_tokens'])}")
    print(f"  output_tokens:      {format_int(usage['output_tokens'])}")
    print(f"  cache_read_tokens:  {format_int(usage['cache_read_tokens'])}")
    print(f"  cache_write_tokens: {format_int(usage['cache_write_tokens'])}")
    print(f"  total_tokens:       {format_int(usage['total_tokens'])}")
    print(f"  cost_usd:           {usage['cost_usd']:.6f}")


def print_report(result: dict[str, Any], show_per_call: bool) -> None:
    print("=" * 88)
    print(result["chat_path"])
    print(f"model: {result['model']}  encoding: {result['encoding']}")
    print(f"events: {result['event_count']}  messages: {result['message_event_count']}  requests_est: {result['request_count_est']}")
    print(f"roles: {result['role_counts']}")
    print(f"content: {result['content_counts']}")
    print()
    print_usage_block("usage.json", result["usage_json"])
    print_usage_block("chat.jsonl usage sum", result["chat_usage_sum"])
    print()
    print("text-only estimate:")
    print(f"  visible_text_once_tokens: {format_int(result['visible_text_once_tokens_est'])}")
    print(f"  cumulative_input_tokens:  {format_int(result['cumulative_input_tokens_est'])}")
    print(f"  assistant_output_tokens:  {format_int(result['assistant_output_tokens_est'])}")
    print(f"  total_text_tokens:        {format_int(result['total_text_tokens_est'])}")
    print()
    print(f"image_attachments_not_priced: {result['image_attachment_count']}")
    if result["image_attachments"]:
        print(f"image_attachments: {result['image_attachments']}")
    if result["image_descriptions"]:
        print(f"image_descriptions_seen: {result['image_descriptions']}")
    if not usage_is_nonzero(result["usage_json"]) and not usage_is_nonzero(result["chat_usage_sum"]):
        print("note: real usage fields are zero, so use the text-only estimate as a rough upper-bound-ish transcript estimate.")
    print("note: hidden system prompts, tool schemas, cache discounts, and true image tokens are not recoverable from this JSONL.")

    if show_per_call:
        print()
        print("per assistant call:")
        print("  #  line  stop_reason  input_est  output_est")
        for index, call in enumerate(result["per_call"], 1):
            print(
                f"  {index:<2} {call['line']:<5} {str(call['stop_reason']):<11} "
                f"{format_int(call['input_tokens_est']):>9} {format_int(call['output_tokens_est']):>10}"
            )


def summarize_many(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "run_count": len(results),
        "total_text_tokens_est": sum(r["total_text_tokens_est"] for r in results),
        "cumulative_input_tokens_est": sum(r["cumulative_input_tokens_est"] for r in results),
        "assistant_output_tokens_est": sum(r["assistant_output_tokens_est"] for r in results),
        "request_count_est": sum(r["request_count_est"] for r in results),
        "image_attachment_count": sum(r["image_attachment_count"] for r in results),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Estimate token usage for OpenClaw/agent chat.jsonl runs."
    )
    parser.add_argument("target", help="Path to a run directory, a parent directory, or a chat.jsonl file.")
    parser.add_argument(
        "--model",
        default="gpt-4o",
        help="Fallback tokenizer model when the chat log does not record a model. Unknown models use o200k_base.",
    )
    parser.add_argument("--per-call", action="store_true", help="Print per assistant-call estimates.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    chat_paths = find_chat_paths(Path(args.target))
    if not chat_paths:
        raise SystemExit(f"No chat.jsonl found under {args.target}")

    results = [estimate_chat(path, args.model) for path in chat_paths]
    if args.json:
        print(json.dumps({"summary": summarize_many(results), "runs": results}, ensure_ascii=False, indent=2))
        return

    for result in results:
        print_report(result, args.per_call)

    if len(results) > 1:
        summary = summarize_many(results)
        print("=" * 88)
        print("TOTAL")
        for key, value in summary.items():
            print(f"{key}: {format_int(value)}")


if __name__ == "__main__":
    main()
