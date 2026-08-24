from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from .persistence import read_json, read_jsonl

SKILL_READ_MARKER = "/root/.agents/skills/"


def build_report(exp_dir: Path | str) -> dict[str, Path]:
    root = Path(exp_dir)
    data = load_report_data(root)
    markdown = render_markdown(data)
    html_text = render_html(data)
    md_path = root / "report.md"
    html_path = root / "report.html"
    md_path.write_text(markdown, encoding="utf-8")
    html_path.write_text(html_text, encoding="utf-8")
    return {"markdown": md_path, "html": html_path}


def load_report_data(exp_dir: Path) -> dict[str, Any]:
    round_states = _load_many(exp_dir / "round_states")
    oracle_manifests = _load_nested_json(exp_dir / "oracle_manifests")
    return {
        "exp_dir": str(exp_dir),
        "summary": _safe_json(exp_dir / "summary.json"),
        "task": _safe_json(exp_dir / "task_spec.json"),
        "config": _safe_json(exp_dir / "config.json"),
        "receipts": _load_many(exp_dir / "receipts"),
        "verifier_scores": _load_many(exp_dir / "verifier_scores"),
        "oracle_scores": _load_many(exp_dir / "oracle_scores"),
        "round_states": round_states,
        "skills": _load_skill_snapshots(exp_dir),
        "oracle_manifests": oracle_manifests,
        "oracle_outputs": _load_oracle_outputs(oracle_manifests),
        "revision_attempts": _load_attempts(exp_dir / "receipt_revision_attempts"),
        "events": read_jsonl(exp_dir / "events.jsonl"),
    }


def render_markdown(data: dict[str, Any]) -> str:
    summary = data.get("summary", {})
    lines = [
        "# Rubric-CoEvoSkills Report",
        "",
        f"- Task: {data.get('task', {}).get('task_name', '')}",
        f"- Best skill: {summary.get('best_skill', '')}",
        f"- Best oracle score: {summary.get('best_oracle_score', '')}",
        f"- Oracle pass ratio: {summary.get('oracle_pass_ratio', '')}",
        f"- Stop reason: {summary.get('stop_reason', '')}",
        "",
        "## Timeline",
    ]
    for item in _timeline(data):
        lines.append(
            f"- Step {item['step']} outer={item['outer_round']} {item['mode']}: "
            f"verifier={item['verifier_summary']} oracle={item['oracle_summary']} "
            f"alignment={item['rank_alignment']}"
        )
    lines.extend([
        "",
        "## Oracle Runs",
    ])
    for item in _oracle_run_rows(data):
        lines.append(
            f"- {item['skill_key']} {_outer_display(item['outer_round'])} "
            f"score={item['overall_score']} pass={item['oracle_pass']} "
            f"stage={item['failure_stage']} output={item['output_dir']} "
            f"chat={item['chat_jsonl_path']} skill_read={item['skill_read_verified']}"
        )
    lines.extend([
        "",
        "## Skill Snapshots",
    ])
    for item in _skill_rows(data):
        lines.append(
            f"- {item['label']} {item['skill_key']} files={', '.join(item['files'])} "
            f"metadata={item['metadata_summary']}"
        )
    lines.extend([
        "",
        "## Rank Alignment",
    ])
    for state in data.get("round_states", []):
        payload = state.get("payload", {})
        if payload.get("rank_alignment") is not None:
            lines.append(f"- {state['name']}: {payload['rank_alignment']}")
    lines.extend(["", "## Final Receipt"])
    receipts = data.get("receipts", [])
    if receipts:
        final = receipts[-1]["payload"]
        for rubric in final.get("rubrics", []):
            lines.append(f"- `{rubric['rubric_id']}` {rubric['points']}: {rubric['criterion']}")
    lines.extend(["", "## Revision Attempts"])
    for attempt in data.get("revision_attempts", []):
        meta = attempt.get("metadata", {})
        lines.append(f"- {attempt['name']}: accepted={meta.get('accepted')} errors={meta.get('error_codes')}")
    return "\n".join(lines).strip() + "\n"


def render_html(data: dict[str, Any]) -> str:
    title = "CoEvo Experiment Report"
    parts = [
        "<!doctype html>",
        "<html>",
        "<head>",
        "<meta charset='utf-8'>",
        f"<title>{_e(title)}</title>",
        "<style>",
        _css(),
        "</style>",
        "</head>",
        "<body>",
        "<main>",
        _summary_html(data),
        _timeline_html(data),
        _oracle_runs_html(data),
        _skills_html(data),
        _events_html(data),
        _receipt_html(data),
        _raw_data_html(data),
        "</main>",
        "</body>",
        "</html>",
    ]
    return "\n".join(parts) + "\n"


def _summary_html(data: dict[str, Any]) -> str:
    summary = data.get("summary", {})
    config = data.get("config", {})
    task = data.get("task", {})
    rows = [
        ("Experiment", data.get("exp_dir", "")),
        ("Task", task.get("task_name", summary.get("task", ""))),
        ("Model", config.get("model", "")),
        ("Best skill", summary.get("best_skill", "")),
        ("Best oracle score", _fmt(summary.get("best_oracle_score", ""))),
        ("Oracle pass ratio", _fmt(summary.get("oracle_pass_ratio", ""))),
        ("Stop reason", summary.get("stop_reason", "")),
        ("Elapsed seconds", summary.get("elapsed_seconds", "")),
    ]
    return (
        "<section class='hero'>"
        "<h1>CoEvo Experiment Report</h1>"
        f"<p>{_e(task.get('task_name', summary.get('task', '')))}</p>"
        "<div class='metrics'>"
        f"{''.join(_metric(label, value) for label, value in rows[3:7])}"
        "</div>"
        f"{_kv_table(rows)}"
        "</section>"
    )


def _timeline_html(data: dict[str, Any]) -> str:
    rows = _timeline(data)
    if not rows:
        return _section("Timeline", "<p class='muted'>No round state files found.</p>")
    explainer = (
        "<p class='muted'>Read each outer round as: Mode A scores the current skills with the verifier "
        "and may create the next skill version through the Skill Generator; Mode B sends that updated "
        "skill version to the Oracle and may revise the receipt/rubric if ranks disagree.</p>"
    )
    body = [
        explainer,
        _oracle_resource_summary_html(data),
        "<table>",
        "<thead><tr><th>Step</th><th>Outer</th><th>Mode</th><th>Skills</th><th>Verifier</th><th>Oracle</th><th>Ranks</th></tr></thead>",
        "<tbody>",
    ]
    for row in rows:
        body.append(
            "<tr>"
            f"<td>{_e(row['step'])}</td>"
            f"<td>{_e(row['outer_round'])}</td>"
            f"<td><span class='pill'>{_e(row['mode'])}</span></td>"
            f"<td>{_e(', '.join(row['skills']))}</td>"
            f"<td>{_e(row['verifier_summary'])}</td>"
            f"<td>{_e(row['oracle_summary'])}</td>"
            f"<td>{_e(row['rank_summary'])}</td>"
            "</tr>"
        )
    body.extend(["</tbody>", "</table>"])
    return _section("Timeline", "".join(body))


def _oracle_runs_html(data: dict[str, Any]) -> str:
    rows = _oracle_run_rows(data)
    if not rows:
        return _section("Oracle Runs", "<p class='muted'>No oracle manifests found.</p>")
    body = []
    for row in rows:
        score = row.get("score_payload") or {}
        usage = row.get("usage_payload") or {}
        result = row.get("result_excerpt", "")
        evidence = row.get("skill_read_excerpt", "")
        body.append(
            "<article class='panel'>"
            "<div class='panel-title'>"
            f"<h3>{_e(row['skill_key'])} · {_e(_outer_display(row['outer_round']))}</h3>"
            f"<span class='score'>{_e(_fmt(row['overall_score']))}</span>"
            "</div>"
            f"{_kv_table([('Oracle pass', row['oracle_pass']), ('Failure stage', row['failure_stage']), ('Return code', row['returncode']), ('Output dir', row['output_dir']), ('Skill dir', row['skill_dir']), ('Chat JSONL', row['chat_jsonl_path']), ('Chat JSONL exists', row['chat_jsonl_exists']), ('Skill read verified', row['skill_read_verified']), ('Skill read line', row['skill_read_line'])])}"
            "<details open><summary>Score JSON</summary>"
            f"<pre>{_e(_pretty(score))}</pre>"
            "</details>"
            "<details><summary>Usage JSON</summary>"
            f"<pre>{_e(_pretty(usage))}</pre>"
            "</details>"
            "<details><summary>Skill read evidence</summary>"
            f"<pre>{_e(evidence)}</pre>"
            "</details>"
            "<details><summary>Final results.md excerpt</summary>"
            f"<pre>{_e(result)}</pre>"
            "</details>"
            "<details><summary>Run command</summary>"
            f"<pre>{_e(' '.join(str(item) for item in row.get('command', [])))}</pre>"
            "</details>"
            "</article>"
        )
    return _section("Oracle Runs", "".join(body))


def _skills_html(data: dict[str, Any]) -> str:
    rows = _skill_rows(data)
    if not rows:
        return _section("Skill Snapshots", "<p class='muted'>No skill snapshots found.</p>")
    body = []
    for row in rows:
        files_html = []
        for path, content in row["file_items"]:
            open_attr = " open" if path == "SKILL.md" else ""
            files_html.append(
                f"<details{open_attr}><summary>{_e(path)}</summary><pre>{_e(content)}</pre></details>"
            )
        body.append(
            "<article class='panel'>"
            "<div class='panel-title'>"
            f"<h3>{_e(row['label'])} · {_e(row['skill_key'])}</h3>"
            f"<span class='pill'>{_e(row['sg_status'])}</span>"
            "</div>"
            f"{_kv_table([('Skill name', row['skill_name']), ('Entrypoint', row['entrypoint']), ('Metadata', row['metadata_summary'])])}"
            f"{''.join(files_html)}"
            "</article>"
        )
    return _section("Skill Snapshots", "".join(body))


def _events_html(data: dict[str, Any]) -> str:
    events = data.get("events", [])
    if not events:
        return _section("SG Update Events", "<p class='muted'>No events.jsonl entries found for this run.</p>")
    rows = []
    for event in events:
        if str(event.get("event", "")).startswith("sg_"):
            rows.append(event)
    if not rows:
        return _section("SG Update Events", "<p class='muted'>No SG update events found.</p>")
    body = "<pre>" + _e(_pretty(rows)) + "</pre>"
    return _section("SG Update Events", body)


def _receipt_html(data: dict[str, Any]) -> str:
    receipts = data.get("receipts", [])
    if not receipts:
        return _section("Receipt", "<p class='muted'>No receipt files found.</p>")
    final = receipts[-1]["payload"]
    rows = []
    for rubric in final.get("rubrics", []):
        rows.append(
            "<tr>"
            f"<td>{_e(rubric.get('rubric_id', ''))}</td>"
            f"<td>{_e(rubric.get('points', ''))}</td>"
            f"<td>{_e(rubric.get('category', ''))}</td>"
            f"<td>{_e(rubric.get('criterion', ''))}</td>"
            "</tr>"
        )
    table = (
        "<table><thead><tr><th>ID</th><th>Points</th><th>Category</th><th>Criterion</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )
    return _section("Receipt", table)


def _raw_data_html(data: dict[str, Any]) -> str:
    return _section(
        "Raw Summary",
        "<details><summary>summary.json</summary>"
        f"<pre>{_e(_pretty(data.get('summary', {})))}</pre>"
        "</details>"
        "<details><summary>config.json</summary>"
        f"<pre>{_e(_pretty(data.get('config', {})))}</pre>"
        "</details>",
    )


def _timeline(data: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in data.get("round_states", []):
        payload = item.get("payload", {})
        verifier = payload.get("verifier_scores", {}) or {}
        oracle = payload.get("oracle_scores", {}) or {}
        rows.append(
            {
                "step": payload.get("step", ""),
                "outer_round": payload.get("outer_round", ""),
                "mode": payload.get("mode", ""),
                "skills": sorted((payload.get("skills") or {}).keys()),
                "verifier_summary": _score_summary(verifier, "normalized_score"),
                "oracle_summary": _score_summary(oracle, "oracle_score"),
                "rank_alignment": payload.get("rank_alignment", ""),
                "rank_summary": _rank_summary(payload),
            }
        )
    return rows


def _oracle_run_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    score_index = _oracle_score_index(data.get("oracle_scores", []))
    output_index = {item["output_dir"]: item for item in data.get("oracle_outputs", []) if item.get("output_dir")}
    rows = []
    for manifest in data.get("oracle_manifests", []):
        payload = manifest.get("payload", {})
        skill_key = str(payload.get("skill_key", ""))
        outer_round = _outer_from_name(manifest.get("name", ""))
        output_dir = str(payload.get("output_dir", ""))
        score_payload = score_index.get((outer_round, skill_key), score_index.get(("", skill_key), {}))
        output = output_index.get(output_dir, {})
        public_score = output.get("score_payload") or {}
        rows.append(
            {
                "outer_round": outer_round,
                "skill_key": skill_key,
                "overall_score": score_payload.get("oracle_score", public_score.get("overall_score", "")),
                "oracle_pass": score_payload.get("oracle_pass", ""),
                "failure_stage": payload.get("failure_stage", ""),
                "returncode": payload.get("returncode", ""),
                "output_dir": output_dir,
                "skill_dir": payload.get("skill_dir", ""),
                "command": payload.get("command", []),
                "score_payload": public_score,
                "usage_payload": output.get("usage_payload") or {},
                "result_excerpt": output.get("result_excerpt", ""),
                "chat_jsonl_path": output.get("chat_jsonl_path", str(Path(output_dir) / "chat.jsonl") if output_dir else ""),
                "chat_jsonl_exists": output.get("chat_jsonl_exists", False),
                "skill_read_verified": output.get("skill_read_verified", False),
                "skill_read_line": output.get("skill_read_line", ""),
                "skill_read_excerpt": output.get("skill_read_excerpt", ""),
            }
        )
    return rows


def _oracle_resource_summary_html(data: dict[str, Any]) -> str:
    rows = _oracle_resource_rows(data)
    if not rows:
        return "<p class='muted'>No oracle usage.json files found for resource summary.</p>"
    body = [
        "<h3>Oracle Resource Summary</h3>",
        "<table>",
        "<thead><tr><th>Outer round</th><th>Oracle skills</th><th>Scores</th><th>Elapsed</th><th>Requests</th><th>Input tokens</th><th>Output tokens</th><th>Cache read</th><th>Total tokens</th><th>Cost</th></tr></thead>",
        "<tbody>",
    ]
    for row in rows:
        body.append(
            "<tr>"
            f"<td>{_e(_outer_display(row['outer_round']))}</td>"
            f"<td>{_e(', '.join(row['skills']))}</td>"
            f"<td>{_e(', '.join(row['scores']))}</td>"
            f"<td>{_e(_duration(row['elapsed_time']))}</td>"
            f"<td>{_e(row['request_count'])}</td>"
            f"<td>{_e(_int_fmt(row['input_tokens']))}</td>"
            f"<td>{_e(_int_fmt(row['output_tokens']))}</td>"
            f"<td>{_e(_int_fmt(row['cache_read_tokens']))}</td>"
            f"<td>{_e(_int_fmt(row['total_tokens']))}</td>"
            f"<td>{_e(_money(row['cost_usd']))}</td>"
            "</tr>"
        )
    body.extend(["</tbody>", "</table>"])
    return "".join(body)


def _oracle_resource_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for run in _oracle_run_rows(data):
        outer = str(run.get("outer_round", ""))
        usage = run.get("usage_payload") or {}
        row = grouped.setdefault(
            outer,
            {
                "outer_round": outer,
                "skills": [],
                "scores": [],
                "elapsed_time": 0.0,
                "request_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0,
            },
        )
        row["skills"].append(str(run.get("skill_key", "")))
        row["scores"].append(str(_fmt(run.get("overall_score", ""))))
        row["elapsed_time"] += _number(usage.get("elapsed_time"))
        row["request_count"] += int(_number(usage.get("request_count")))
        row["input_tokens"] += int(_number(usage.get("input_tokens")))
        row["output_tokens"] += int(_number(usage.get("output_tokens")))
        row["cache_read_tokens"] += int(_number(usage.get("cache_read_tokens")))
        row["total_tokens"] += int(_number(usage.get("total_tokens")))
        row["cost_usd"] += _number(usage.get("cost_usd"))
    return [grouped[key] for key in sorted(grouped)]


def _skill_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in data.get("skills", []):
        payload = item.get("payload", {})
        metadata = payload.get("metadata", {}) or {}
        file_items = sorted((payload.get("files", {}) or {}).items())
        rows.append(
            {
                "label": item.get("name", ""),
                "skill_key": _skill_key(payload.get("key", {})),
                "skill_name": payload.get("skill_name", ""),
                "entrypoint": payload.get("entrypoint", ""),
                "metadata_summary": _metadata_summary(metadata),
                "sg_status": _sg_status(metadata),
                "files": [path for path, _ in file_items],
                "file_items": file_items,
            }
        )
    return rows


def _load_skill_snapshots(exp_dir: Path) -> list[dict[str, Any]]:
    root = exp_dir / "skills"
    if not root.exists():
        return []
    snapshots = []
    for path in sorted(root.glob("*/*.json")):
        label = path.parent.name
        snapshots.append({"name": label, "path": str(path), "payload": read_json(path)})
    return snapshots


def _load_nested_json(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    items = []
    for path in sorted(root.rglob("*.json")):
        items.append({"name": str(path.relative_to(root).with_suffix("")), "path": str(path), "payload": read_json(path)})
    return items


def _load_oracle_outputs(manifests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    outputs = []
    for item in manifests:
        output_dir = Path(str(item.get("payload", {}).get("output_dir", "")))
        if not output_dir.exists():
            continue
        chat_path = output_dir / "chat.jsonl"
        skill_read = _find_skill_read_evidence(chat_path)
        outputs.append(
            {
                "output_dir": str(output_dir),
                "score_payload": _safe_json(output_dir / "score.json"),
                "usage_payload": _safe_json(output_dir / "usage.json"),
                "result_excerpt": _read_text(output_dir / "task_output" / "workspace" / "results" / "results.md", 16000),
                "chat_jsonl_path": str(chat_path),
                "chat_jsonl_exists": chat_path.exists(),
                **skill_read,
            }
        )
    return outputs


def _find_skill_read_evidence(chat_path: Path) -> dict[str, Any]:
    if not chat_path.exists():
        return {
            "skill_read_verified": False,
            "skill_read_line": "",
            "skill_read_excerpt": "",
        }
    with chat_path.open(encoding="utf-8", errors="replace") as file:
        for line_number, line in enumerate(file, start=1):
            if _line_reads_skill_file(line):
                return {
                    "skill_read_verified": True,
                    "skill_read_line": line_number,
                    "skill_read_excerpt": line[:2000],
                }
    return {
        "skill_read_verified": False,
        "skill_read_line": "",
        "skill_read_excerpt": "",
    }


def _line_reads_skill_file(line: str) -> bool:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return False
    message = payload.get("message")
    if not isinstance(message, dict):
        return False
    if message.get("role") != "assistant":
        return False
    for item in message.get("content", []):
        if not isinstance(item, dict) or item.get("type") != "toolCall":
            continue
        arguments = item.get("arguments")
        file_path = ""
        if isinstance(arguments, dict):
            file_path = arguments.get("file_path") or arguments.get("path") or ""
        if item.get("name") == "read" and SKILL_READ_MARKER in file_path and file_path.endswith("SKILL.md"):
            return True
    return False


def _oracle_score_index(items: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    index = {}
    for item in items:
        outer = _outer_from_name(item.get("name", ""))
        for skill_key, score in (item.get("payload", {}) or {}).items():
            index[(outer, skill_key)] = score
            index[("", skill_key)] = score
    return index


def _score_summary(scores: dict[str, Any], field: str) -> str:
    if not scores:
        return ""
    parts = []
    for key, score in sorted(scores.items()):
        value = score.get(field, "")
        rank = score.get("rank", "")
        parts.append(f"{key}: {field}={_fmt(value)} rank={rank}")
    return "; ".join(parts)


def _rank_summary(payload: dict[str, Any]) -> str:
    verifier = [_skill_key(item) for item in payload.get("verifier_rank", [])]
    oracle = [_skill_key(item) for item in payload.get("oracle_rank", [])]
    alignment = payload.get("rank_alignment", "")
    return f"verifier=[{', '.join(verifier)}] oracle=[{', '.join(oracle)}] alignment={_fmt(alignment)}"


def _metadata_summary(metadata: dict[str, Any]) -> str:
    if not metadata:
        return ""
    keys = ["strategy", "summary", "llm_update", "fallback_update", "fallback_update_reason"]
    picked = {key: metadata[key] for key in keys if key in metadata}
    return _pretty(picked or metadata)


def _sg_status(metadata: dict[str, Any]) -> str:
    if metadata.get("fallback_update"):
        return f"fallback: {metadata.get('fallback_update_reason', '')}"
    if metadata.get("llm_update"):
        return "llm update"
    if metadata.get("fallback"):
        return "fallback seed"
    return "seed"


def _skill_key(value: Any) -> str:
    if isinstance(value, dict):
        return f"s{int(value.get('slot', 0)):03d}_v{int(value.get('version', 0)):03d}"
    return str(value)


def _outer_from_name(name: str) -> str:
    for part in str(name).split("/"):
        if part.startswith("outer_"):
            return part
    if str(name).startswith("outer_"):
        return str(name).split("_mode_", 1)[0]
    return ""


def _outer_display(value: Any) -> str:
    text = str(value)
    if text.startswith("outer_"):
        suffix = text.removeprefix("outer_")
        if suffix.isdigit():
            return f"outer_round={int(suffix)}"
    if text != "":
        return f"outer_round={text}"
    return "outer_round="


def _metric(label: str, value: Any) -> str:
    return f"<div class='metric'><div>{_e(label)}</div><strong>{_e(_fmt(value))}</strong></div>"


def _kv_table(rows: list[tuple[str, Any]]) -> str:
    body = "".join(f"<tr><th>{_e(label)}</th><td>{_e(_fmt(value))}</td></tr>" for label, value in rows)
    return f"<table class='kv'><tbody>{body}</tbody></table>"


def _section(title: str, body: str) -> str:
    return f"<section><h2>{_e(title)}</h2>{body}</section>"


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _int_fmt(value: Any) -> str:
    return f"{int(_number(value)):,}"


def _duration(seconds: Any) -> str:
    value = _number(seconds)
    return f"{value:.2f}s" if value else ""


def _money(value: Any) -> str:
    amount = _number(value)
    return f"${amount:.4f}"


def _pretty(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True)


def _read_text(path: Path, limit: int) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n... truncated ..."


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _css() -> str:
    return """
:root { color-scheme: light; --bg: #f7f8fb; --ink: #172033; --muted: #647086; --line: #d8deea; --panel: #ffffff; --accent: #136f63; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 14px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
main { max-width: 1180px; margin: 0 auto; padding: 28px; }
section, .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; margin: 0 0 18px; padding: 18px; }
.hero { border-top: 4px solid var(--accent); }
h1, h2, h3 { margin: 0 0 10px; line-height: 1.2; }
h1 { font-size: 28px; }
h2 { font-size: 20px; }
h3 { font-size: 16px; }
p { margin: 0 0 12px; }
.muted { color: var(--muted); }
.metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; margin: 16px 0; }
.metric { border: 1px solid var(--line); border-radius: 8px; padding: 12px; background: #fbfcfe; }
.metric div { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
.metric strong { font-size: 22px; }
table { width: 100%; border-collapse: collapse; margin: 8px 0; }
th, td { border-bottom: 1px solid var(--line); padding: 8px; text-align: left; vertical-align: top; }
th { color: var(--muted); font-weight: 650; width: 170px; }
thead th { width: auto; background: #f1f4f8; color: var(--ink); }
.kv th { width: 170px; }
.panel-title { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; margin-bottom: 10px; }
.score { font-size: 22px; font-weight: 750; color: var(--accent); }
.pill { display: inline-block; border: 1px solid var(--line); border-radius: 999px; padding: 2px 8px; background: #f8fafc; color: #334155; font-size: 12px; white-space: nowrap; }
details { border: 1px solid var(--line); border-radius: 8px; margin: 10px 0; background: #fbfcfe; }
summary { cursor: pointer; padding: 8px 10px; font-weight: 650; }
pre { margin: 0; padding: 12px; overflow: auto; white-space: pre-wrap; word-break: break-word; font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; border-top: 1px solid var(--line); }
"""


def _safe_json(path: Path) -> dict[str, Any]:
    return read_json(path) if path.exists() else {}


def _load_many(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    return [{"name": path.stem, "payload": read_json(path)} for path in sorted(root.glob("*.json"))]


def _load_attempts(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    attempts = []
    for path in sorted(root.iterdir()):
        if path.is_dir():
            attempts.append({"name": path.name, "metadata": _safe_json(path / "metadata.json")})
    return attempts
