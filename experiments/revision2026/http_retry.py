"""Bounded HTTP retries, shared with the generated OpenHands startup patch."""
import random
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

DEFAULT_POLICY = {"api_max_attempts": 4, "api_base_seconds": 5, "api_max_seconds": 300,
                  "oracle_max_attempts": 4, "oracle_base_seconds": 30, "oracle_max_seconds": 900,
                  "job_max_attempts": 1, "job_base_seconds": 60}

_TRANSIENT_STATUS = {404, 408, 409, 425, 429}
_PERMANENT_STATUS = {400, 401, 403, 413, 422}
_TRANSIENT_TEXT = re.compile(
    r"\b(404|408|409|425|429|5\d\d)\b|rate.?limit|too many requests|timed? ?out|timeout|temporar|"
    r"connection[ _]?(reset|refused|aborted|error)|reset by peer|remote end closed|broken pipe|eof occurred|"
    r"overloaded|bad gateway|service unavailable|gateway time|server error|try again|ssl", re.I)
_PERMANENT_TEXT = re.compile(
    r"\b(400|401|403|413|422|1210|1301)\b|invalid api key|authentication|unauthori[sz]ed|permission denied|"
    r"content.?filter|context.?length|maximum context|unsupported (image|content)|参数非法", re.I)


def policy_from(protocol):
    result = {**DEFAULT_POLICY, **(protocol or {}).get("retry", {})}
    for key in ("api_max_attempts", "oracle_max_attempts", "job_max_attempts"):
        if not isinstance(result[key], int) or result[key] < 1:
            raise ValueError(f"{key} must be a positive integer")
    return result


def is_transient(error):
    """True only for failures that a later identical attempt can plausibly fix."""
    if isinstance(error, RetryExhausted) or re.search(r"retry.?exhausted", str(error), re.I):
        return False  # An outer layer must not restart the same exhausted request.
    if re.search(r"insufficient_quota|quota.exceeded|billing.hard.limit|insufficient.balance", str(error), re.I):
        return False
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    if isinstance(status, int):
        if status in _PERMANENT_STATUS:
            return False
        if status in _TRANSIENT_STATUS or 500 <= status <= 599:
            return True
    name = type(error).__name__ if isinstance(error, BaseException) else ""
    if name in {"APIConnectionError", "APITimeoutError", "RateLimitError", "InternalServerError",
                "ConnectionError", "TimeoutError", "ReadTimeout", "ConnectTimeout", "LLMNoResponseError"}:
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
            return max(0.0, float(retry_after))
        except (TypeError, ValueError):
            try:
                instant = parsedate_to_datetime(str(retry_after))
                return max(0.0, (instant - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError):
                pass
    return min(cap, base * 2 ** attempt) * (0.5 + random.random() / 2)


class RetryExhausted(RuntimeError):
    """A request already consumed its retries; do not multiply them at outer layers."""


def retry_call(fn, *, attempts, base, cap, describe, log=None, sleep=None):
    """Initial request plus attempts-1 retries; exhaustion is an explicit boundary."""
    sleep = sleep or time.sleep
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - classification decides
            if not is_transient(exc):
                raise
            if attempt + 1 >= attempts:
                raise RetryExhausted(f"[retry-exhausted] {describe}: {attempts} attempts, {type(exc).__name__}") from exc
            source, headers = exc, {}
            while source is not None:
                headers = getattr(getattr(source, "response", None), "headers", None) or getattr(source, "headers", None) or {}
                if headers:
                    break
                source = source.__cause__
            delay = backoff_seconds(attempt, base, cap, headers.get("retry-after") if hasattr(headers, "get") else None)
            if log:
                log({"kind": "transient_retry", "what": describe, "attempt": attempt + 1, "error_type": type(exc).__name__,
                     "error": str(exc)[:300], "sleep_seconds": round(delay, 1)})
            sleep(delay)
