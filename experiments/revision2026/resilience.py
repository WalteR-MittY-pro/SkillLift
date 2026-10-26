"""Transient-failure retries and crash-safe resume for long experiment runs.

Two mechanisms, both conservative about what they reuse:

* Retries. API/infrastructure failures that look transient (404, 429, 408, 409, 5xx,
  connection resets, timeouts) are retried with exponential backoff and jitter. Errors
  that cannot succeed on retry (400 bad request, 401/403, content-filter or unsupported
  input codes, context overflow) fail immediately. A failed execution is never scored.

* Journal. Every completed LLM response and every valid oracle execution inside a
  training cell is appended (fsync) to ``<cell>/journal/*.jsonl`` before it is used.
  When the cell is restarted after a crash, identical requests are answered from the
  journal in the same order, so the optimizer replays its earlier decisions and only the
  missing work is executed live. Keys contain the full request (prompt / skill content
  hash + phase) plus an occurrence index, so a recorded result is reused only for the
  same logical request; nothing is ever reused across cells, models or methods.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time

from .common import utc_now

from .http_retry import DEFAULT_POLICY, RetryExhausted, policy_from, is_transient, backoff_seconds, retry_call


def request_key(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


class Journal:
    """Append-only, occurrence-indexed record of completed requests for one training cell."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.entries, self.seen = {}, {}
        self.hits = self.misses = 0
        if self.path.is_file():
            lines = self.path.read_bytes().splitlines(keepends=True)
            valid_bytes = 0
            for index, line in enumerate(lines):
                if not line.strip():
                    valid_bytes += len(line)
                    continue
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    if index != len(lines) - 1:
                        raise ValueError("Journal has corruption before its last line; refusing to discard completed work")
                    # Remove the torn tail before appending; otherwise new complete
                    # records remain hidden behind the same corrupt line on restart.
                    with self.path.open("r+b") as stream:
                        stream.truncate(valid_bytes)
                        stream.flush()
                        os.fsync(stream.fileno())
                    break
                self.entries.setdefault(row["key"], []).append(row["value"])
                valid_bytes += len(line)
            else:
                if lines and not lines[-1].endswith(b"\n"):
                    with self.path.open("ab") as stream:
                        stream.write(b"\n")

    def lookup(self, key):
        index = self.seen.get(key, 0)
        self.seen[key] = index + 1
        stored = self.entries.get(key, [])
        if index < len(stored):
            self.hits += 1
            return True, stored[index]
        self.misses += 1
        return False, None

    def release(self, key):
        """Undo a lookup whose live request failed, so occurrence indexes count only completed requests."""
        if self.seen.get(key, 0) > 0:
            self.seen[key] -= 1

    def record(self, key, value):
        row = {"key": key, "value": value, "recorded_at": utc_now()}
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self.entries.setdefault(key, []).append(value)


class ResilientLLM:
    """Wraps LLMClient: longer transient retries plus journal replay of identical prompts."""

    def __init__(self, client, policy, journal=None, log=None, failure_state=None):
        self.client, self.policy, self.journal, self.log = client, policy, journal, log
        self.failure_state = failure_state if failure_state is not None else {}

    def __getattr__(self, name):
        return getattr(self.client, name)

    def call_text(self, system_prompt, user_prompt, temperature=0.0, *, response_format=None, max_completion_tokens=None):
        if self.failure_state.get("error"):
            raise self.failure_state["error"]
        key = request_key("llm", getattr(self.client, "role", None), getattr(self.client, "model", None), system_prompt,
                          user_prompt, temperature, response_format, max_completion_tokens)
        if self.journal is not None:
            hit, value = self.journal.lookup(key)
            if hit:
                return value["text"]
        try:
            text = retry_call(lambda: self.client.call_text(system_prompt, user_prompt, temperature, response_format=response_format,
                                                            max_completion_tokens=max_completion_tokens),
                              attempts=self.policy["api_max_attempts"], base=self.policy["api_base_seconds"],
                              cap=self.policy["api_max_seconds"], describe=f"llm:{getattr(self.client, 'role', 'llm')}", log=self.log)
        except Exception as exc:
            self.failure_state["error"] = exc
            if self.journal is not None:
                self.journal.release(key)
            raise
        if self.journal is not None:
            self.journal.record(key, {"text": text})
        return text

    def call_json(self, system_prompt, user_prompt, temperature=0.0, *, repair=True):
        from skilllift.llm_client import _parse_json_response
        return _parse_json_response(self.call_text(system_prompt, user_prompt, temperature), repair=repair)
