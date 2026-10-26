"""Transient-failure retries and crash-safe resume for long experiment runs.

Two mechanisms, both conservative about what they reuse:

* Retries. API/infrastructure failures that look transient (429, 404, 408, 409, 5xx,
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
import random
import re
import time

from .common import utc_now

DEFAULT_POLICY = {"api_max_attempts": 8, "api_base_seconds": 5, "api_max_seconds": 300,
                  "oracle_max_attempts": 6, "oracle_base_seconds": 30, "oracle_max_seconds": 900,
                  "job_max_attempts": 3, "job_base_seconds": 60}

_TRANSIENT_STATUS = {404, 408, 409, 425, 429}
_PERMANENT_STATUS = {400, 401, 403, 413, 422}
_TRANSIENT_TEXT = re.compile(
    r"\b(404|408|409|425|429|5\d\d)\b|rate.?limit|too many requests|timed? ?out|timeout|temporar|"
    r"connection (reset|refused|aborted|error)|reset by peer|remote end closed|broken pipe|eof occurred|"
    r"overloaded|bad gateway|service unavailable|gateway time|server error|try again|ssl", re.I)
_PERMANENT_TEXT = re.compile(
    r"\b(400|401|403|413|422|1210|1301)\b|invalid api key|authentication|unauthori[sz]ed|permission denied|"
    r"content.?filter|context.?length|maximum context|unsupported (image|content)|参数非法", re.I)


def policy_from(protocol):
    return {**DEFAULT_POLICY, **(protocol or {}).get("retry", {})}


def is_transient(error):
    """True only for failures that a later identical attempt can plausibly fix."""
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    if isinstance(status, int):
        if status in _PERMANENT_STATUS:
            return False
        if status in _TRANSIENT_STATUS or 500 <= status <= 599:
            return True
    name = type(error).__name__ if isinstance(error, BaseException) else ""
    if name in {"APIConnectionError", "APITimeoutError", "RateLimitError", "InternalServerError",
                "ConnectionError", "TimeoutError", "ReadTimeout", "ConnectTimeout"}:
        return True
    text = str(error)
    if isinstance(error, BaseException) and error.__cause__ is not None:
        text += " " + str(error.__cause__)
    if _PERMANENT_TEXT.search(text):
        return False
    return bool(_TRANSIENT_TEXT.search(text))


def backoff_seconds(attempt, base, cap, retry_after=None):
    if retry_after is not None:
        try:
            return min(float(retry_after), cap)
        except (TypeError, ValueError):
            pass
    return min(cap, base * 2 ** attempt) * (0.5 + random.random() / 2)


def retry_call(fn, *, attempts, base, cap, describe, log=None, sleep=time.sleep):
    """Call fn(); retry transient exceptions. The last exception is re-raised."""
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - classification decides
            if attempt + 1 >= attempts or not is_transient(exc):
                raise
            headers = getattr(getattr(exc, "response", None), "headers", None) or {}
            delay = backoff_seconds(attempt, base, cap, headers.get("retry-after") if hasattr(headers, "get") else None)
            if log:
                log({"kind": "transient_retry", "what": describe, "attempt": attempt + 1, "error_type": type(exc).__name__,
                     "error": str(exc)[:300], "sleep_seconds": round(delay, 1)})
            sleep(delay)


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
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    break  # a torn final line from a crash; everything before it is intact
                self.entries.setdefault(row["key"], []).append(row["value"])

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

    def __init__(self, client, policy, journal=None, log=None):
        self.client, self.policy, self.journal, self.log = client, policy, journal, log

    def __getattr__(self, name):
        return getattr(self.client, name)

    def call_text(self, system_prompt, user_prompt, temperature=0.0, *, response_format=None, max_completion_tokens=None):
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
        except Exception:
            if self.journal is not None:
                self.journal.release(key)
            raise
        if self.journal is not None:
            self.journal.record(key, {"text": text})
        return text

    def call_json(self, system_prompt, user_prompt, temperature=0.0, *, repair=True):
        from skilllift.llm_client import _parse_json_response
        return _parse_json_response(self.call_text(system_prompt, user_prompt, temperature), repair=repair)
