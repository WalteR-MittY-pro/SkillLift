#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
from collections import Counter
from pathlib import Path
from typing import Any

import gradio as gr


DEFAULT_CHAT_PATH = (
    Path(__file__).resolve().parent.parent
    / "skilllift_baseline"
    / "01_Productivity_Flow"
    / "01_Productivity_Flow_task_10_pdf_digest"
    / "gpt-5.4_20260514_2324_bf8dd3"
    / "chat.jsonl"
)


CSS = """
.chat-viewer {
  font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: #17202a;
}
.summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 8px 0 18px;
}
.pill {
  border: 1px solid #d9e1ea;
  border-radius: 999px;
  background: #f7fafc;
  padding: 5px 10px;
  font-size: 13px;
}
.event {
  border: 1px solid #d8dee8;
  border-radius: 14px;
  margin: 12px 0;
  overflow: hidden;
  background: white;
  box-shadow: 0 1px 8px rgba(20, 33, 61, 0.04);
}
.event.user { border-left: 6px solid #2563eb; }
.event.assistant { border-left: 6px solid #16a34a; }
.event.toolResult { border-left: 6px solid #f97316; }
.event.meta { border-left: 6px solid #64748b; }
.event-header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  border-bottom: 1px solid #eef2f7;
  background: #fbfcfe;
}
.role {
  font-weight: 750;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  font-size: 12px;
}
.timestamp, .line-number, .meta-chip {
  color: #64748b;
  font-size: 12px;
}
.event-body { padding: 12px 14px 14px; }
.part {
  border: 1px solid #edf1f5;
  border-radius: 10px;
  margin: 8px 0;
  background: #ffffff;
}
.part-title {
  padding: 7px 10px;
  border-bottom: 1px solid #edf1f5;
  background: #f8fafc;
  color: #334155;
  font-size: 12px;
  font-weight: 700;
}
.part-text {
  padding: 10px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.55;
  font-size: 14px;
}
.thinking .part-title { color: #7c2d12; background: #fff7ed; }
.tool-call .part-title { color: #075985; background: #f0f9ff; }
.tool-result .part-title { color: #9a3412; background: #fff7ed; }
.tool-result.error { border-color: #fecaca; }
.tool-result.error .part-title { color: #991b1b; background: #fef2f2; }
.raw-json {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  color: #334155;
}
"""


ROLE_LABELS = {
    "user": "User",
    "assistant": "Assistant",
    "toolResult": "Tool Result",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            event = json.loads(line)
            event["_line_number"] = line_number
            events.append(event)
    return events


def text_from_content_item(item: dict[str, Any]) -> str:
    item_type = item.get("type")
    if item_type == "text":
        return str(item.get("text", ""))
    if item_type == "thinking":
        return str(item.get("thinking", ""))
    if item_type == "toolCall":
        name = item.get("name", "tool")
        arguments = item.get("arguments", {})
        return f"{name}({json.dumps(arguments, ensure_ascii=False, indent=2)})"
    return json.dumps(item, ensure_ascii=False, indent=2)


def message_plain_text(event: dict[str, Any]) -> str:
    message = event.get("message") or {}
    parts = []
    for item in message.get("content") or []:
        parts.append(text_from_content_item(item))
    return "\n".join(parts)


def render_text_block(title: str, text: str, css_class: str) -> str:
    return (
        f'<div class="part {css_class}">'
        f'<div class="part-title">{html.escape(title)}</div>'
        f'<div class="part-text">{html.escape(text)}</div>'
        "</div>"
    )


def render_tool_call(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "tool")
    call_id = str(item.get("id") or "")
    arguments = json.dumps(item.get("arguments", {}), ensure_ascii=False, indent=2)
    title = f"Tool Call: {name}"
    if call_id:
        title += f" | {call_id}"
    return render_text_block(title, arguments, "tool-call raw-json")


def render_tool_result(message: dict[str, Any]) -> str:
    tool_name = str(message.get("toolName") or "tool")
    tool_call_id = str(message.get("toolCallId") or "")
    title = f"Tool Result: {tool_name}"
    if tool_call_id:
        title += f" | {tool_call_id}"
    text_parts = []
    for item in message.get("content") or []:
        text_parts.append(text_from_content_item(item))
    if not text_parts and message.get("details") is not None:
        text_parts.append(json.dumps(message.get("details"), ensure_ascii=False, indent=2))
    css_class = "tool-result"
    if message.get("details", {}).get("status") == "error" or message.get("isError"):
        css_class += " error"
    return render_text_block(title, "\n".join(text_parts), css_class)


def render_message_event(
    event: dict[str, Any],
    show_thinking: bool,
    show_tool_calls: bool,
    show_tool_results: bool,
) -> str:
    message = event.get("message") or {}
    role = str(message.get("role") or "message")
    if role == "toolResult" and not show_tool_results:
        return ""

    body_parts: list[str] = []
    if role == "toolResult":
        body_parts.append(render_tool_result(message))
    else:
        for item in message.get("content") or []:
            item_type = item.get("type")
            if item_type == "text":
                body_parts.append(render_text_block("Text", str(item.get("text", "")), "text"))
            elif item_type == "thinking":
                if show_thinking:
                    body_parts.append(render_text_block("Thinking", str(item.get("thinking", "")), "thinking"))
            elif item_type == "toolCall":
                if show_tool_calls:
                    body_parts.append(render_tool_call(item))
            else:
                body_parts.append(render_text_block(str(item_type or "Content"), text_from_content_item(item), "raw-json"))

    if not body_parts:
        return ""

    timestamp = str(event.get("timestamp") or message.get("timestamp") or "")
    line_number = event.get("_line_number", "?")
    provider = message.get("provider")
    model = message.get("model")
    extra = ""
    if provider or model:
        extra = f'<span class="meta-chip">{html.escape(str(provider or ""))} {html.escape(str(model or ""))}</span>'

    return (
        f'<article class="event {html.escape(role)}">'
        '<div class="event-header">'
        f'<span class="role">{html.escape(ROLE_LABELS.get(role, role))}</span>'
        f'<span class="line-number">line {html.escape(str(line_number))}</span>'
        f'<span class="timestamp">{html.escape(timestamp)}</span>'
        f"{extra}"
        "</div>"
        f'<div class="event-body">{"".join(body_parts)}</div>'
        "</article>"
    )


def render_meta_event(event: dict[str, Any]) -> str:
    event_type = str(event.get("type") or "event")
    line_number = event.get("_line_number", "?")
    timestamp = str(event.get("timestamp") or "")
    compact = {k: v for k, v in event.items() if not k.startswith("_")}
    text = json.dumps(compact, ensure_ascii=False, indent=2)
    return (
        '<article class="event meta">'
        '<div class="event-header">'
        f'<span class="role">{html.escape(event_type)}</span>'
        f'<span class="line-number">line {html.escape(str(line_number))}</span>'
        f'<span class="timestamp">{html.escape(timestamp)}</span>'
        "</div>"
        f'<div class="event-body">{render_text_block("Metadata", text, "raw-json")}</div>'
        "</article>"
    )


def render_events(
    events: list[dict[str, Any]],
    show_thinking: bool,
    show_tool_calls: bool,
    show_tool_results: bool,
    show_metadata: bool,
    query: str,
) -> str:
    role_counts: Counter[str] = Counter()
    rendered: list[str] = []
    query_norm = query.strip().lower()

    for event in events:
        event_type = event.get("type")
        message = event.get("message") or {}
        role = str(message.get("role") or event_type or "event")
        role_counts[role] += 1

        searchable = json.dumps(event, ensure_ascii=False).lower()
        if query_norm and query_norm not in searchable:
            continue

        if event_type == "message":
            rendered_event = render_message_event(event, show_thinking, show_tool_calls, show_tool_results)
        elif show_metadata:
            rendered_event = render_meta_event(event)
        else:
            rendered_event = ""

        if rendered_event:
            rendered.append(rendered_event)

    summary = [
        f'<span class="pill">events: {len(events)}</span>',
        *[
            f'<span class="pill">{html.escape(role)}: {count}</span>'
            for role, count in sorted(role_counts.items())
        ],
        f'<span class="pill">rendered: {len(rendered)}</span>',
    ]
    if query_norm:
        summary.append(f'<span class="pill">filter: {html.escape(query)}</span>')

    if not rendered:
        rendered.append('<div class="event"><div class="event-body">No events matched the current filters.</div></div>')

    return (
        '<section class="chat-viewer">'
        '<div class="summary">'
        + "".join(summary)
        + "</div>"
        + "".join(rendered)
        + "</section>"
    )


def resolve_input_path(uploaded_file: str | None, path_text: str) -> Path:
    if uploaded_file:
        return Path(uploaded_file)
    if path_text.strip():
        return Path(path_text).expanduser()
    return DEFAULT_CHAT_PATH


def load_and_render(
    uploaded_file: str | None,
    path_text: str,
    show_thinking: bool,
    show_tool_calls: bool,
    show_tool_results: bool,
    show_metadata: bool,
    query: str,
) -> str:
    path = resolve_input_path(uploaded_file, path_text)
    try:
        events = read_jsonl(path)
    except Exception as exc:
        return (
            '<section class="chat-viewer">'
            '<div class="event toolResult error"><div class="event-body">'
            f"Failed to load {html.escape(str(path))}: {html.escape(str(exc))}"
            "</div></div></section>"
        )
    return render_events(events, show_thinking, show_tool_calls, show_tool_results, show_metadata, query)


def build_app(default_path: Path = DEFAULT_CHAT_PATH) -> gr.Blocks:
    with gr.Blocks(title="Agent Chat JSONL Viewer") as app:
        gr.Markdown(
            "# Agent Chat JSONL Viewer\n"
            "Render agent chat histories as normal HTML, so browser page translation can read the dialogue text."
        )
        with gr.Row():
            path_text = gr.Textbox(
                value=str(default_path),
                label="chat.jsonl path",
                lines=2,
                scale=3,
            )
            upload = gr.File(
                label="or upload a chat.jsonl",
                file_types=[".jsonl"],
                type="filepath",
                scale=1,
            )
        with gr.Row():
            show_thinking = gr.Checkbox(value=True, label="Show thinking")
            show_tool_calls = gr.Checkbox(value=True, label="Show tool calls")
            show_tool_results = gr.Checkbox(value=True, label="Show tool results")
            show_metadata = gr.Checkbox(value=False, label="Show metadata")
        with gr.Row():
            query = gr.Textbox(value="", label="Filter text", placeholder="Optional substring filter")
            refresh = gr.Button("Render", variant="primary")

        output = gr.HTML(
            value=load_and_render(None, str(default_path), True, True, True, False, ""),
            label="Rendered chat",
            show_label=False,
            container=False,
        )

        inputs = [upload, path_text, show_thinking, show_tool_calls, show_tool_results, show_metadata, query]
        for component in inputs:
            component.change(load_and_render, inputs=inputs, outputs=output)
        refresh.click(load_and_render, inputs=inputs, outputs=output)
    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Visualize agent chat JSONL with Gradio.")
    parser.add_argument("--chat", default=str(DEFAULT_CHAT_PATH), help="Path to a chat.jsonl file.")
    parser.add_argument("--server-name", default="127.0.0.1", help="Gradio server host.")
    parser.add_argument("--server-port", type=int, default=7860, help="Gradio server port.")
    parser.add_argument("--share", action="store_true", help="Create a Gradio share link.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = build_app(Path(args.chat).expanduser())
    app.launch(server_name=args.server_name, server_port=args.server_port, share=args.share, css=CSS)


if __name__ == "__main__":
    main()
