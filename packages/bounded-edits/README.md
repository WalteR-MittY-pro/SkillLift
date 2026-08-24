<div align="center">

# LLM-Output-Json-Repair

**Repair unpredictable LLM edit output into constrained, validated, replayable unified diffs.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-2ea44f)](LICENSE)
[![Runtime dependencies: 0](https://img.shields.io/badge/runtime_dependencies-0-0f766e)](pyproject.toml)

</div>

`LLM-Output-Json-Repair` gives an LLM a compact edit protocol instead of asking
it to return full files or fragile free-form patches. The model refers to
allowlisted files by opaque IDs and returns only anchors plus changed content.
The package then resolves every edit locally, rejects ambiguity, and emits a
canonical Git diff.

The Python distribution is named `bounded-edits` and its import package is
`bounded_edits`.

It does **not** call a model and does **not** modify the captured workspace.

```text
allowlisted files
      |
      v
snapshot + JSON Schema ---> structured model response
      |                              |
      +---------- local validation --+
                     |
                     v
            canonical unified diff
```

## Why this protocol?

| Common approach | Failure mode | LLM-Output-Json-Repair |
| --- | --- | --- |
| Return complete files | High output-token cost | Return changed fragments only |
| Return file paths | Hallucinated or escaping paths | Use schema-bound opaque file IDs |
| Return free-form patches | Inconsistent patch dialects | Use one strict JSON contract |
| Match short snippets | Wrong or ambiguous location | Require unique exact anchors |
| Trust the response | Silent corruption | Validate locally and fail closed |

## Quick start

Install from GitHub:

```bash
python -m pip install "bounded-edits @ git+https://github.com/WalteR-MittY-pro/LLM-Output-Json-Repair.git"
```

Capture the exact files the model may edit and build its response contract:

```python
from bounded_edits import (
    build_contract,
    capture_snapshot,
    parse_response,
    prepare_patch,
)
from bounded_edits.adapters.openai_compatible import response_format

snapshot = capture_snapshot(
    "portfolio",
    ["skill-a/SKILL.md", "skill-a/references/checks.md"],
)
contract = build_contract(snapshot)

raw = model_call(
    system=contract.protocol_instructions,
    user=contract.target_context + "\n\nAdd a concise final validation step.",
    response_format=response_format(contract),
)

prepared = prepare_patch(
    snapshot=snapshot,
    parsed=parse_response(raw, contract=contract),
)

print(prepared.changed_paths)
print(prepared.unified_diff)
```

`model_call` is intentionally yours. The package works with any endpoint that
can return the contract's JSON object; the included adapter generates an
OpenAI-compatible `response_format.type=json_schema` payload.

## Wire protocol

A typical response changes one fragment without repeating the file:

```json
{
  "schema_version": 1,
  "edits": [
    {
      "file_id": "f0",
      "op": "insert_after",
      "start_anchor": "## Final checks",
      "end_anchor": null,
      "content": "\n- Verify the generated artifact before delivery."
    }
  ]
}
```

The Schema binds `file_id` to the current snapshot and validates the legal
fields for each operation.

| Operation | Meaning |
| --- | --- |
| `append` | Append literal non-empty content |
| `insert_before` | Insert immediately before one unique anchor |
| `insert_after` | Insert immediately after one unique anchor |
| `replace_range` | Replace the half-open range `[start_anchor, end_anchor)` |
| `delete_range` | Delete the half-open range `[start_anchor, end_anchor)` |

## Safety model

Every response is treated as untrusted input. Before a diff is returned, the
package verifies:

- only captured file IDs are referenced;
- the JSON shape and operation fields match the contract;
- anchors contain substantive text and occur exactly once;
- range boundaries are ordered and edits do not overlap;
- LF/CRLF style and final-newline behavior are preserved;
- source hashes still match the captured snapshot;
- the generated patch passes `git apply --check` in an isolated environment.

Failures raise `BoundedEditError` with a stable `ErrorCode`, such as
`anchor_not_found`, `anchor_ambiguous`, `overlapping_edits`, or
`source_changed`. No partial result is returned.

## Scope

The protocol is deliberately small:

- existing UTF-8 text files only;
- at most 20 target files by default;
- LF or CRLF line endings;
- exact anchors, never fuzzy matching;
- no new files, renames, deletions, binary files, or symlinks;
- Git must be available on `PATH` for canonical diff generation.

These constraints are what make a low-token model response safe to apply
without asking the model to reproduce entire files.

## Development

```bash
python -m pip install -e ".[test]"
pytest
```

The test suite covers Python 3.11, 3.12, and 3.13, including CRLF, no newline
at EOF, duplicate JSON, ambiguous anchors, overlapping edits, source changes,
path isolation, and canonical diff replay.

## License

MIT. See [LICENSE](LICENSE).
