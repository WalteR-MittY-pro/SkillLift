from bounded_edits import EditOp, build_contract, capture_snapshot
from bounded_edits.adapters.openai_compatible import response_format


def test_contract_binds_file_ids_schema_prompt_and_context(tmp_path):
    (tmp_path / "a.md").write_text("# A\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("# B", encoding="utf-8")

    contract = build_contract(capture_snapshot(tmp_path, ["b.md", "a.md"]))

    branches = contract.json_schema["properties"]["edits"]["items"]["anyOf"]
    assert all(
        branch["properties"]["file_id"]["enum"] == ["f0", "f1"] for branch in branches
    )
    assert [target.relative_path for target in contract.targets] == ["a.md", "b.md"]
    assert '[{"file_id":"f0","path":"a.md"}' in contract.target_context
    assert (
        "[Target File f0: a.md; final_newline=true]\nL0001 | # A\nL0002 | <EOF>" in contract.target_context
    )
    assert "[Target File f1: b.md; final_newline=false]\nL0001 | # B" in contract.target_context


def test_contract_exposes_exactly_five_operations_and_key_guidance(tmp_path):
    (tmp_path / "a.md").write_text("# A\n", encoding="utf-8")
    contract = build_contract(capture_snapshot(tmp_path, ["a.md"]))

    branches = contract.json_schema["properties"]["edits"]["items"]["anyOf"]
    operations = [branch["properties"]["op"]["const"] for branch in branches]

    assert operations == [
        "append",
        "insert_before",
        "insert_after",
        "replace_range",
        "delete_range",
    ]
    assert "line IDs" in contract.protocol_instructions
    assert "never copy source text" in contract.protocol_instructions
    assert (
        "combine both changes into one replace_range" in contract.protocol_instructions
    )
    assert "multiple non-overlapping edits for one file are valid" in contract.protocol_instructions
    assert "reproduce boundary text that must remain" in contract.protocol_instructions
    assert "end_anchor remains unchanged" in contract.protocol_instructions
    assert "Append content is literal" in contract.protocol_instructions
    assert "leading newline" in contract.protocol_instructions

    by_operation = {
        branch["properties"]["op"]["const"]: branch["properties"] for branch in branches
    }
    assert by_operation["append"]["start_anchor"] == {"type": "null"}
    assert by_operation["append"]["end_anchor"] == {"type": "null"}
    assert by_operation["insert_after"]["start_anchor"]["minLength"] == 1
    assert by_operation["insert_after"]["end_anchor"] == {"type": "null"}
    assert by_operation["replace_range"]["content"]["minLength"] == 1
    assert by_operation["delete_range"]["content"] == {
        "type": "string",
        "const": "",
    }


def test_openai_adapter_wraps_the_dynamic_schema(tmp_path):
    (tmp_path / "a.md").write_text("# A\n", encoding="utf-8")
    contract = build_contract(capture_snapshot(tmp_path, ["a.md"]))

    value = response_format(contract)

    assert value == {
        "type": "json_schema",
        "json_schema": {
            "name": "bounded_edits",
            "strict": True,
            "schema": contract.json_schema,
        },
    }


def test_contract_can_limit_operations_for_a_direction(tmp_path):
    (tmp_path / "a.py").write_text("def load():\n    return []\n", encoding="utf-8")

    contract = build_contract(
        capture_snapshot(tmp_path, ["a.py"]),
        allowed_ops=(EditOp.REPLACE_RANGE,),
    )

    branches = contract.json_schema["properties"]["edits"]["items"]["anyOf"]
    assert [branch["properties"]["op"]["const"] for branch in branches] == [
        "replace_range"
    ]
    assert contract.allowed_ops == frozenset({EditOp.REPLACE_RANGE})
