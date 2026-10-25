import json

from experiments.revision2026.oracles import public_conversation
from experiments.revision2026.common import write_json


def test_acp_tool_feedback_is_read_but_thought_and_backup_are_not_duplicated(tmp_path):
    directory = tmp_path / "agent"
    directory.mkdir()
    events = [{"type": "user_message", "text": "task"}, {"type": "agent_thought", "text": "private thought"},
              {"type": "tool_call", "tool_call_id": "t1", "title": "run", "content": [{"content": {"text": "exit=1"}}]}]
    (directory / "acp_trajectory.jsonl").write_text("\n".join(json.dumps(x) for x in events))
    rows = public_conversation(tmp_path)
    assert len(rows) == 2
    assert rows[-1]["tool_call_id"] == "t1"
    assert "exit=1" in rows[-1]["content"]
    assert "private thought" not in str(rows)


def test_no_recursive_fallback_to_another_trial(tmp_path):
    anchor = tmp_path / "anchor" / "agent"
    anchor.mkdir(parents=True)
    (anchor / "conversation.json").write_text('[{"role":"assistant","content":"wrong trial"}]')
    assert public_conversation(tmp_path) == []


def test_wildclaw_tool_result_and_text_blocks(tmp_path):
    event = {"type": "message", "message": {"role": "toolResult", "toolCallId": "abc", "content": [{"type": "text", "text": "actual error"}, {"type": "thinking", "text": "exclude"}]}}
    (tmp_path / "chat.jsonl").write_text(json.dumps(event))
    assert public_conversation(tmp_path) == [{"role": "tool", "content": "actual error", "tool_call_id": "abc"}]


def test_wildclaw_follows_exact_output_dir_in_normalized_record(tmp_path):
    from experiments.revision2026.oracles import execution_root
    actual = tmp_path / "raw" / "exact-run"
    actual.mkdir(parents=True)
    (actual / "chat.jsonl").write_text("")
    write_json(tmp_path / "score.json", {"output_dir": str(actual)})
    write_json(tmp_path / "task_run_record.json", {"score_path": str(tmp_path / "score.json")})
    write_json(tmp_path / "marker.json", {"task_run_record_path": str(tmp_path / "task_run_record.json")})
    assert execution_root(tmp_path / "marker.json") == actual.resolve()
