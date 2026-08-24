import os

import pytest

from bounded_edits import BoundedEditError, ErrorCode, capture_snapshot
from bounded_edits.documents import assert_snapshot_current, logical_to_bytes


@pytest.mark.parametrize(
    ("raw", "newline", "final_newline"),
    [
        (b"# Skill\nBody\n", "\n", True),
        (b"# Skill\r\nBody\r\n", "\r\n", True),
        (b"No final newline", "\n", False),
    ],
)
def test_snapshot_normalizes_logical_view_and_preserves_physical_bytes(
    tmp_path, raw, newline, final_newline
):
    (tmp_path / "skill.md").write_bytes(raw)

    target = capture_snapshot(tmp_path, ["skill.md"]).targets[0]

    assert "\r" not in target.logical_content
    assert target.newline == newline
    assert target.final_newline is final_newline
    assert logical_to_bytes(target.logical_content, target.newline) == raw


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b"first\r\nsecond\n", ErrorCode.MIXED_LINE_ENDINGS),
        (b"first\rsecond\r", ErrorCode.UNSUPPORTED_CR_LINE_ENDINGS),
        (b"a\x00b", ErrorCode.BINARY_CONTENT),
        (b"\xff", ErrorCode.INVALID_UTF8),
    ],
)
def test_invalid_text_fails_closed(tmp_path, raw, code):
    (tmp_path / "skill.md").write_bytes(raw)

    with pytest.raises(BoundedEditError) as raised:
        capture_snapshot(tmp_path, ["skill.md"])

    assert raised.value.code is code


def test_snapshot_detects_source_drift(tmp_path):
    path = tmp_path / "skill.md"
    path.write_text("# Original\n", encoding="utf-8")
    snapshot = capture_snapshot(tmp_path, ["skill.md"])
    path.write_text("# Changed\n", encoding="utf-8")

    with pytest.raises(BoundedEditError) as raised:
        assert_snapshot_current(snapshot)

    assert raised.value.code is ErrorCode.SOURCE_CHANGED


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink privileges vary")
def test_symlink_targets_are_rejected(tmp_path):
    outside = tmp_path.parent / "outside-bounded-edits.md"
    outside.write_text("outside\n", encoding="utf-8")
    (tmp_path / "link.md").symlink_to(outside)
    try:
        with pytest.raises(BoundedEditError) as raised:
            capture_snapshot(tmp_path, ["link.md"])
        assert raised.value.code is ErrorCode.SYMLINK_FORBIDDEN
    finally:
        outside.unlink(missing_ok=True)


@pytest.mark.parametrize("path", ["../outside.md", "/tmp/outside.md", "a\\b.md"])
def test_unsafe_paths_are_rejected(tmp_path, path):
    with pytest.raises(BoundedEditError) as raised:
        capture_snapshot(tmp_path, [path])

    assert raised.value.code is ErrorCode.INVALID_PATH
