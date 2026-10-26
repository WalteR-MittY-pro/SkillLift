"""Embedded after http_retry.py in the OpenHands startup patch."""
import functools
import json
import os
from pathlib import Path


def install_openhands_retry(retry_fn):
    from openhands.sdk.llm.utils.retry_mixin import RetryMixin
    if getattr(RetryMixin, "_skilllift_retry_installed", False):
        return
    attempts = int(os.environ.get("SKILLLIFT_API_MAX_ATTEMPTS", "4"))

    def decorator(self, **settings):
        def decorate(function):
            @functools.wraps(function)
            def call(*args, **kwargs):
                def record(event):
                    path = Path("/logs/agent/api_retry.jsonl")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("a") as stream:
                        stream.write(json.dumps(event) + "\n")
                    listener = settings.get("retry_listener")
                    if listener:
                        listener(event["attempt"], attempts, None)
                return retry_fn(lambda: function(*args, **kwargs), attempts=attempts, base=5, cap=300,
                                  describe="openhands", log=record)
            return call
        return decorate

    RetryMixin.retry_decorator = decorator
    RetryMixin._skilllift_retry_installed = True
