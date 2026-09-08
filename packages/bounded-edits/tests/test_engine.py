import hashlib
import json
import os
import subprocess
from dataclasses import replace

import pytest
import bounded_edits.engine as engine_module

from bounded_edits import (
    BoundedEditError,
    ErrorCode,
    build_contract,
    capture_snapshot,
    parse_response,
    prepare_patch,
)


BASE = "## Alpha\none\n## Beta\ntwo\n"


def _raw_edit(op, *, start_anchor=None, end_anchor=None, content=""):
    return json.dumps(
        {
            "schema_version": 1,
            "edits": [
                {
                    "file_id": "f0",
                    "op": op,
                    "start_anchor": start_anchor,
                    "end_anchor": end_anchor,
                    "content": content,
                }
            ],
        }
    )


def _prepare(tmp_path, raw, *, source=BASE.encode()):
    (tmp_path / "skill.md").write_bytes(source)
    snapshot = capture_snapshot(tmp_path, ["skill.md"])
    parsed = parse_response(raw, contract=build_contract(snapshot))
    return snapshot, prepare_patch(snapshot=snapshot, parsed=parsed)


def _apply(tmp_path, patch):
    env = os.environ.copy()
    for key in tuple(env):
        if key.startswith("GIT_"):
            env.pop(key)
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    completed = subprocess.run(
        ["git", "apply", "--no-index", "--recount", "-"],
        cwd=tmp_path,
        input=patch.encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr.decode()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (_raw_edit("append", content="tail\n"), BASE + "tail\n"),
        (
            _raw_edit("insert_before", start_anchor="## Beta\n", content="before\n"),
            "## Alpha\none\nbefore\n## Beta\ntwo\n",
        ),
        (
            _raw_edit("insert_after", start_anchor="## Alpha\n", content="after\n"),
            "## Alpha\nafter\none\n## Beta\ntwo\n",
        ),
        (
            _raw_edit(
                "replace_range",
                start_anchor="one\n",
                end_anchor="## Beta\n",
                content="replacement\n",
            ),
            "## Alpha\nreplacement\n## Beta\ntwo\n",
        ),
        (
            _raw_edit(
                "delete_range",
                start_anchor="one\n",
                end_anchor="## Beta\n",
            ),
            "## Alpha\n## Beta\ntwo\n",
        ),
    ],
)
def test_all_operations_generate_replayable_patch_without_source_mutation(
    tmp_path, raw, expected
):
    parent_hash = hashlib.sha256(BASE.encode()).hexdigest()

    snapshot, prepared = _prepare(tmp_path, raw)

    assert (
        hashlib.sha256((tmp_path / "skill.md").read_bytes()).hexdigest() == parent_hash
    )
    assert prepared.changed_paths == ("skill.md",)
    assert prepared.unified_diff.startswith("diff --git a/skill.md b/skill.md\n")
    _apply(tmp_path, prepared.unified_diff)
    assert (tmp_path / "skill.md").read_text() == expected
    assert snapshot.targets[0].content_sha256 == parent_hash


def test_line_id_range_uses_line_boundaries(tmp_path):
    _, prepared = _prepare(
        tmp_path,
        _raw_edit(
            "replace_range",
            start_anchor="L0002",
            end_anchor="L0003",
            content="TWO\n",
        ),
        source=b"one\ntwo\nthree\n",
    )

    _apply(tmp_path, prepared.unified_diff)
    assert (tmp_path / "skill.md").read_text() == "one\nTWO\nthree\n"


def test_line_id_range_rejects_same_line_boundary(tmp_path):
    with pytest.raises(BoundedEditError) as raised:
        _prepare(
            tmp_path,
            _raw_edit(
                "delete_range",
                start_anchor="L0002",
                end_anchor="L0002",
            ),
            source=b"one\ntwo\n",
        )
    assert raised.value.code is ErrorCode.INVALID_RANGE


def test_directly_constructed_string_op_does_not_degrade_to_delete(tmp_path):
    (tmp_path / "skill.md").write_bytes(BASE.encode())
    snapshot = capture_snapshot(tmp_path, ["skill.md"])
    contract = build_contract(snapshot)
    parsed = parse_response(
        _raw_edit(
            "replace_range",
            start_anchor="one\n",
            end_anchor="## Beta\n",
            content="replacement\n",
        ),
        contract=contract,
    )
    # Simulate a caller that bypasses parse_response and carries a plain
    # string op (EditOp is a StrEnum, so the string equals the enum value).
    string_op = replace(parsed.batch.edits[0], op="replace_range")
    parsed = replace(parsed, batch=replace(parsed.batch, edits=(string_op,)))

    # Unknown ops fail closed before any disk mutation.
    bogus = replace(parsed.batch.edits[0], op="bogus")
    with pytest.raises(BoundedEditError) as raised:
        prepare_patch(
            snapshot=snapshot,
            parsed=replace(parsed, batch=replace(parsed.batch, edits=(bogus,))),
        )
    assert raised.value.code is ErrorCode.INVALID_OPERATION

    prepared = prepare_patch(snapshot=snapshot, parsed=parsed)
    _apply(tmp_path, prepared.unified_diff)
    assert (tmp_path / "skill.md").read_text() == "## Alpha\nreplacement\n## Beta\ntwo\n"


@pytest.mark.parametrize(
    ("source", "content", "expected"),
    [
        (b"last", "tail", b"lasttail"),
        (b"last", "\ntail", b"last\ntail"),
        (b"last", "\ntail\n", b"last\ntail\n"),
        (b"last\n", "tail", b"last\ntail"),
        (b"last\n", "tail\n", b"last\ntail\n"),
        (b"last\r\n", "tail\n", b"last\r\ntail\r\n"),
        (b"last", "\ntail\n", b"last\r\ntail\r\n"),
    ],
)
def test_append_is_literal_and_preserves_eof_and_eol(
    tmp_path, source, content, expected
):
    if b"\r\n" in expected and b"\r\n" not in source:
        source = b"head\r\nlast"
        expected = b"head\r\n" + expected
    _, prepared = _prepare(
        tmp_path, _raw_edit("append", content=content), source=source
    )
    _apply(tmp_path, prepared.unified_diff)
    assert (tmp_path / "skill.md").read_bytes() == expected


def test_crlf_anchor_matching_and_restoration(tmp_path):
    source = BASE.replace("\n", "\r\n").encode()
    raw = _raw_edit("insert_after", start_anchor="## Alpha\n", content="after\n")

    _, prepared = _prepare(tmp_path, raw, source=source)
    _apply(tmp_path, prepared.unified_diff)
    changed = (tmp_path / "skill.md").read_bytes()

    assert b"after\r\n" in changed
    assert b"\n" not in changed.replace(b"\r\n", b"")


@pytest.mark.parametrize(
    ("raw", "source", "code"),
    [
        (
            _raw_edit("insert_after", start_anchor="\n", content="new\n"),
            BASE.encode(),
            ErrorCode.ANCHOR_WEAK,
        ),
        (
            _raw_edit("insert_after", start_anchor="one", content="new\n"),
            b"one\none\n",
            ErrorCode.ANCHOR_AMBIGUOUS,
        ),
    ],
)
def test_bad_anchors_are_rejected(tmp_path, raw, source, code):
    with pytest.raises(BoundedEditError) as raised:
        _prepare(tmp_path, raw, source=source)
    assert raised.value.code is code


def test_boundary_touching_edits_are_rejected(tmp_path):
    payload = {
        "schema_version": 1,
        "edits": [
            {
                "file_id": "f0",
                "op": "replace_range",
                "start_anchor": "one\n",
                "end_anchor": "## Beta\n",
                "content": "replacement\n",
            },
            {
                "file_id": "f0",
                "op": "insert_before",
                "start_anchor": "one\n",
                "end_anchor": None,
                "content": "touching\n",
            },
        ],
    }

    with pytest.raises(BoundedEditError) as raised:
        _prepare(tmp_path, json.dumps(payload))
    assert raised.value.code is ErrorCode.OVERLAPPING_EDITS


def test_non_overlapping_edits_in_one_file_are_applied(tmp_path):
    payload = {
        "schema_version": 1,
        "edits": [
            {
                "file_id": "f0",
                "op": "insert_after",
                "start_anchor": "L0001",
                "end_anchor": None,
                "content": "first\n",
            },
            {
                "file_id": "f0",
                "op": "insert_after",
                "start_anchor": "L0003",
                "end_anchor": None,
                "content": "last\n",
            },
        ],
    }

    _, prepared = _prepare(tmp_path, json.dumps(payload), source=b"one\ntwo\nthree\n")

    _apply(tmp_path, prepared.unified_diff)
    assert (tmp_path / "skill.md").read_text() == "one\nfirst\ntwo\nthree\nlast\n"


def test_source_drift_and_snapshot_mismatch_are_rejected(tmp_path):
    path = tmp_path / "skill.md"
    path.write_text(BASE)
    snapshot = capture_snapshot(tmp_path, ["skill.md"])
    parsed = parse_response(
        _raw_edit("append", content="tail\n"),
        contract=build_contract(snapshot),
    )
    path.write_text(BASE + "drift\n")
    with pytest.raises(BoundedEditError) as raised:
        prepare_patch(snapshot=snapshot, parsed=parsed)
    assert raised.value.code is ErrorCode.SOURCE_CHANGED

    other = tmp_path / "other"
    other.mkdir()
    (other / "skill.md").write_text("different\n")
    with pytest.raises(BoundedEditError) as raised:
        prepare_patch(snapshot=capture_snapshot(other, ["skill.md"]), parsed=parsed)
    assert raised.value.code is ErrorCode.CONTRACT_MISMATCH


def test_transport_audit_events_reach_prepared_patch(tmp_path):
    raw = _raw_edit("append", content="tail\n")
    (tmp_path / "skill.md").write_text(BASE)
    snapshot = capture_snapshot(tmp_path, ["skill.md"])
    parsed = parse_response(raw + raw, contract=build_contract(snapshot))

    prepared = prepare_patch(snapshot=snapshot, parsed=parsed)

    assert prepared.audit_events[0].code == "duplicate_identical_response"


def test_git_control_environment_cannot_redirect_temporary_repository(
    tmp_path, monkeypatch
):
    external = tmp_path / "external"
    external.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=external, check=True)
    external_git = external / ".git"
    before = tuple(
        sorted(path.relative_to(external_git) for path in external_git.rglob("*"))
    )
    monkeypatch.setenv("GIT_DIR", str(external_git))
    monkeypatch.setenv("GIT_WORK_TREE", str(external))
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "redirected-index"))

    work = tmp_path / "work"
    work.mkdir()
    _, prepared = _prepare(work, _raw_edit("append", content="tail\n"))

    after = tuple(
        sorted(path.relative_to(external_git) for path in external_git.rglob("*"))
    )
    assert after == before
    assert not (tmp_path / "redirected-index").exists()
    _apply(work, prepared.unified_diff)
    assert (work / "skill.md").read_text() == BASE + "tail\n"


def test_canonical_diff_uses_immutable_snapshot_baseline(tmp_path, monkeypatch):
    original = engine_module.canonical_diff
    path = tmp_path / "skill.md"

    def race_workspace(snapshot, changed):
        path.write_text("raced baseline\n")
        try:
            return original(snapshot, changed)
        finally:
            path.write_text(BASE)

    monkeypatch.setattr(engine_module, "canonical_diff", race_workspace)

    _, prepared = _prepare(tmp_path, _raw_edit("append", content="tail\n"))

    _apply(tmp_path, prepared.unified_diff)
    assert path.read_text() == BASE + "tail\n"
