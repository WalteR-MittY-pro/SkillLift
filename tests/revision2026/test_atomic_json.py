import json
from pathlib import Path

import pytest

from experiments.revision2026 import common


def test_windows_atomic_write_retries_sharing_conflict(tmp_path, monkeypatch):
    target = tmp_path / "conversation.json"
    target.write_text('{"old": true}', encoding="utf-8")
    replace = Path.replace
    attempts = []

    def temporarily_locked(source, destination):
        attempts.append(source)
        assert json.loads(target.read_text()) == {"old": True}
        if len(attempts) < 3:
            raise PermissionError("Windows sharing conflict")
        return replace(source, destination)

    monkeypatch.setattr(common, "IS_WINDOWS", True)
    monkeypatch.setattr(common.time, "sleep", lambda _: None)
    monkeypatch.setattr(Path, "replace", temporarily_locked)
    common.write_json(target, {"new": True})
    assert json.loads(target.read_text()) == {"new": True}
    assert len(attempts) == 3
    assert not list(tmp_path.glob("*.tmp"))


def test_permanent_write_failure_preserves_previous_json(tmp_path, monkeypatch):
    target = tmp_path / "result.json"
    target.write_text('{"old": true}', encoding="utf-8")
    calls = []

    def always_locked(source, destination):
        calls.append(source)
        raise PermissionError("locked")

    monkeypatch.setattr(common, "IS_WINDOWS", True)
    monkeypatch.setattr(common.time, "sleep", lambda _: None)
    monkeypatch.setattr(Path, "replace", always_locked)
    with pytest.raises(PermissionError):
        common.write_json(target, {"new": True})
    assert len(calls) == 10
    assert json.loads(target.read_text()) == {"old": True}
    assert not list(tmp_path.glob("*.tmp"))


def test_repeated_writes_use_distinct_temporary_files(tmp_path, monkeypatch):
    replace = Path.replace
    names = []

    def capture(source, destination):
        names.append(source.name)
        return replace(source, destination)

    monkeypatch.setattr(Path, "replace", capture)
    for index in range(3):
        common.write_json(tmp_path / "conversation.json", {"index": index})
    assert len(set(names)) == 3
