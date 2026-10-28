from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
from threading import Barrier, Event
from types import SimpleNamespace, ModuleType

import pytest

from experiments.revision2026 import batch, runner, http_retry
from experiments.revision2026.common import ROOT, load_protocol, tree_hash, write_json


class NotFound(Exception):
    status_code = 404


def test_framework_journal_separates_thinking_policies(tmp_path):
    from experiments.revision2026.resilience import Journal, ResilientLLM
    calls = []
    class Client:
        role, model = "generator", "glm"
        def __init__(self, extra_body):
            self.extra_body = extra_body
        def call_text(self, *a, **kw):
            calls.append(self.extra_body)
            return str(len(calls))
    def client(options):
        # A new Journal represents restarting the worker; occurrences within
        # one worker intentionally remain distinct trials.
        return ResilientLLM(Client(options), http_retry.policy_from(load_protocol()), Journal(tmp_path / "journal.jsonl"))
    assert client({}).call_text("system", "user") == "1"
    disabled = {"thinking": {"type": "disabled"}}
    assert client(disabled).call_text("system", "user") == "2"
    assert client(disabled).call_text("system", "user") == "2"
    assert client({}).call_text("system", "user") == "1"
    assert len(calls) == 2


def test_three_404_retries_succeed_on_fourth_attempt_and_exhaustion_does_not_multiply():
    calls, sleeps = [], []
    def request():
        calls.append(1)
        if len(calls) <= 3:
            raise NotFound("gateway route unavailable")
        return "ok"
    retry = lambda fn: http_retry.retry_call(fn, attempts=4, base=1, cap=1, describe="test", sleep=sleeps.append)
    assert retry(request) == "ok" and len(calls) == 4 and len(sleeps) == 3
    calls.clear()
    def failed():
        calls.append(1)
        raise NotFound("404")
    with pytest.raises(http_retry.RetryExhausted):
        retry(lambda: retry(failed))
    assert len(calls) == 4  # Not four outer attempts times four API attempts.


def test_exhausted_framework_call_stops_other_roles_in_the_same_task(tmp_path, monkeypatch):
    from experiments.revision2026.resilience import ResilientLLM
    calls, state = [], {}
    class Client:
        role, model = "generator", "fake"
        def call_text(self, *a, **k):
            calls.append(1)
            raise NotFound("404")
    monkeypatch.setattr(http_retry.time, "sleep", lambda _: None)
    policy = http_retry.policy_from(load_protocol())
    first, second = [ResilientLLM(Client(), policy, failure_state=state) for _ in range(2)]
    with pytest.raises(http_retry.RetryExhausted):
        first.call_text("system", "first")
    with pytest.raises(http_retry.RetryExhausted):
        second.call_text("system", "different role/prompt")
    assert len(calls) == 4


def test_skillopt_replays_completed_llm_calls_without_rebilling_tokens(tmp_path, monkeypatch):
    from openai.types.chat import ChatCompletion
    from experiments.revision2026.optimization import _harden_skillopt
    from experiments.revision2026.resilience import Journal
    calls = []
    reply = ChatCompletion.model_validate({"id": "test", "object": "chat.completion", "created": 0, "model": "fake",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "saved response"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
    class Client:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        def with_options(self, **kwargs):
            return self
        def create(self, **kwargs):
            calls.append(1)
            return reply
    def fresh_backend():
        backend = SimpleNamespace(_get_client=lambda role: Client())
        def impl(messages, max_completion_tokens, retries, stage, *, role):
            return backend._get_client(role).chat.completions.create(model="fake", messages=messages)
        backend._chat_messages_impl = impl
        parent, model = ModuleType("skillopt"), ModuleType("skillopt.model")
        model.openai_compatible_backend = backend
        monkeypatch.setitem(sys.modules, "skillopt", parent)
        monkeypatch.setitem(sys.modules, "skillopt.model", model)
        return backend
    first = fresh_backend()
    _harden_skillopt(load_protocol(), tmp_path / "first.usage.jsonl", Journal(tmp_path / "journal.jsonl"))
    assert first._chat_messages_impl([], 100, 3, "stage", role="optimizer").usage.total_tokens == 15
    resumed = fresh_backend()
    _harden_skillopt(load_protocol(), tmp_path / "resumed.usage.jsonl", Journal(tmp_path / "journal.jsonl"))
    result = resumed._chat_messages_impl([], 100, 3, "stage", role="optimizer")
    assert result.choices[0].message.content == "saved response" and result.usage is None
    assert len(calls) == 1


def test_native_openhands_404_retry_uses_the_same_four_attempt_cap(tmp_path, monkeypatch):
    from experiments.revision2026 import native_retry
    class RetryMixin:
        pass
    module = ModuleType("openhands.sdk.llm.utils.retry_mixin")
    module.RetryMixin = RetryMixin
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setenv("SKILLLIFT_API_MAX_ATTEMPTS", "4")
    monkeypatch.setattr(native_retry, "Path", lambda _: tmp_path / "retry.jsonl")
    calls, sleeps = [], []
    retry = lambda fn, **kwargs: http_retry.retry_call(fn, sleep=sleeps.append, **kwargs)
    native_retry.install_openhands_retry(retry)
    @RetryMixin().retry_decorator(num_retries=99)
    def request():
        calls.append(1)
        raise NotFound("404")
    with pytest.raises(http_retry.RetryExhausted):
        request()
    assert len(calls) == 4 and len(sleeps) == 3


def make_job(root, family, kind):
    job = dict(root=str(root), benchmark="skilllearnbench", model="glm", method="skilllift", run=0,
               family=family, kind=kind, config_hash="cfg", protocol=load_protocol())
    if kind == "evaluate":
        job.update(task_id=family + "/2", phase="heldout", repeat=0)
    return job


def freeze(job):
    cell = runner.cell_path(job["root"], job["benchmark"], job["model"], job["method"], job["run"], job["family"])
    (cell / "skills").mkdir(parents=True, exist_ok=True)
    (cell / "skills/SKILL.md").write_text("frozen skill")
    write_json(cell / "frozen.json", {"status": "complete", "config_hash": "cfg", "skill_hash": tree_hash(cell / "skills")})


def test_failed_task_does_not_block_others_and_resume_runs_only_missing_work(tmp_path, monkeypatch):
    jobs = [make_job(tmp_path, name, kind) for kind in ("train", "evaluate") for name in ("bad", "good")]
    calls, fail = [], {"enabled": True}
    def execute(argv, **kwargs):
        path = Path(argv[-1]); job = json.loads(path.read_text())
        calls.append((job["kind"], job["family"]))
        if job["family"] == "bad" and fail["enabled"]:
            kwargs["stderr"].write("original failed attempt\n")
            return SimpleNamespace(returncode=1)
        if job["kind"] == "train":
            freeze(job)
        else:
            write_json(runner.evaluation_path(job) / "selected.json", {"status": "OK", "config_hash": "cfg",
                       "cell_identity": runner.evaluation_identity(job), "reward": 1})
        write_json(path.with_suffix(".status.json"), {"status": "complete"})
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(runner.subprocess, "run", execute)
    first = runner.run_jobs(jobs, tmp_path, 1)
    assert calls == [("train", "bad"), ("train", "good"), ("evaluate", "good")]
    assert first["status"] == "completed_with_errors"
    assert any(row["status"] == "waiting_for_training" for row in first["outcomes"])
    log = next(path for path in (tmp_path / "jobs").glob("*.stderr.log") if "original failed attempt" in path.read_text())
    before = log.read_text()
    fail["enabled"] = False; calls.clear()
    second = runner.run_jobs(jobs, tmp_path, 1)
    assert calls == [("train", "bad"), ("evaluate", "bad")]
    assert second["status"] == "complete" and second["skipped_complete"] == 2
    assert log.read_text().startswith(before)


def test_cancelled_queue_does_not_start_more_processes(tmp_path, monkeypatch):
    stop = Event(); stop.set()
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: pytest.fail("Started after cancellation"))
    summary = runner.run_jobs([make_job(tmp_path, "task", "train")], tmp_path, 1, cancel_event=stop)
    assert summary["outcomes"][0]["status"] == "cancelled"


@pytest.mark.parametrize("method", ["skillopt", "skilllift"])
@pytest.mark.parametrize("kind", ["train", "evaluate"])
def test_restart_skips_33_completed_jobs_and_runs_remaining_67(tmp_path, monkeypatch, method, kind):
    """Completion comes from artifacts, even when task 34 still says running."""
    from experiments.revision2026.common import digest
    jobs = [dict(make_job(tmp_path, f"task-{i:03d}", kind), method=method) for i in range(1, 101)]
    for index, job in enumerate(jobs):
        if kind == "evaluate" or index < 33:
            freeze(job)
        if kind == "evaluate" and index < 33:
            write_json(runner.evaluation_path(job) / "selected.json", {
                "status": "OK", "config_hash": "cfg", "cell_identity": runner.evaluation_identity(job), "reward": 1,
            })
    interrupted = tmp_path / "jobs" / (digest(jobs[33]) + ".status.json")
    write_json(interrupted, {"status": "running"})
    calls = []

    def execute(argv, **kwargs):
        path = Path(argv[-1])
        job = json.loads(path.read_text())
        calls.append(job["family"])
        if kind == "train":
            freeze(job)
        else:
            write_json(runner.evaluation_path(job) / "selected.json", {
                "status": "OK", "config_hash": "cfg", "cell_identity": runner.evaluation_identity(job), "reward": 1,
            })
        write_json(path.with_suffix(".status.json"), {"status": "complete"})
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner.subprocess, "run", execute)
    summary = runner.run_jobs(jobs, tmp_path, 1)
    assert summary["status"] == "complete" and summary["skipped_complete"] == 33
    assert calls == [f"task-{i:03d}" for i in range(34, 101)]
    calls.clear()
    summary = runner.run_jobs(jobs, tmp_path, 1)
    assert summary["skipped_complete"] == 100 and not calls
    assert all(json.loads(p.read_text())["status"] == "complete" for p in (tmp_path / "jobs").glob("*.status.json"))


def test_freeze_recovers_crash_between_copy_and_marker_without_losing_data(tmp_path):
    job = make_job(tmp_path, "task", "train")
    cell = tmp_path / "cell"
    source = tmp_path / "source"; source.mkdir(); (source / "SKILL.md").write_text("new")
    (cell / "skills").mkdir(parents=True); (cell / "skills/SKILL.md").write_text("old unmarked output")
    runner.freeze_cell(source, cell, job, {})
    assert (cell / "skills/SKILL.md").read_text() == "new"
    assert next((cell / "attempts").glob("*/SKILL.md")).read_text() == "old unmarked output"


def test_both_experiments_share_model_pipeline_and_models_run_together(tmp_path, monkeypatch):
    barrier, seen = Barrier(2), []
    monkeypatch.setattr(batch, "prepare_run", lambda folder, settings: (settings, "cfg"))
    monkeypatch.setattr(batch, "seal_evaluation_plan", lambda *a: None)
    def jobs(root, protocol, cfg, **kwargs):
        benchmark = "skillsbench" if root.name == "ablation" else "skilllearnbench"
        return [dict(root=str(root), protocol=protocol, config_hash=cfg, model=protocol["models"][0],
                     kind="train", method="skilllift", benchmark=benchmark, family="task", run=0)]
    monkeypatch.setattr(batch, "generalization_jobs", jobs)
    monkeypatch.setattr(batch, "ablation_jobs", jobs)
    def execute(jobs, root, workers, **kwargs):
        model = jobs[0]["model"]
        seen.append((model, root.name, workers, [j["benchmark"] for j in jobs]))
        if root.name == "queue":
            barrier.wait(timeout=5)
        return {"status": "complete"}
    monkeypatch.setattr(batch, "run_jobs", execute)
    from experiments.revision2026 import statistics
    monkeypatch.setattr(statistics, "cost_report", lambda *a: {})
    monkeypatch.setattr(statistics, "process_best_report", lambda *a: [])
    result = batch.run_supplementary(tmp_path, load_protocol())
    assert result["status"] == "complete"
    for model, limit in [("glm", 2), ("deepseek", 2)]:
        rows = [row for row in seen if row[0] == model]
        assert [row[1] for row in rows] == ["pilot", "queue"]
        assert all(row[2] == limit for row in rows)
        assert rows[1][3] == ["skilllearnbench", "skillsbench"]


def test_model_slots_bound_concurrency_across_separate_processes(tmp_path):
    script = '''
import json, sys, time
from pathlib import Path
from experiments.revision2026.common import file_lock
from experiments.revision2026.concurrency import model_slot
root, model = Path(sys.argv[1]), sys.argv[2]
def event(delta):
    with file_lock(root / 'events.lock'), (root / 'events.jsonl').open('a') as stream:
        stream.write(json.dumps({'model': model, 'delta': delta}) + '\\n')
        stream.flush()
with model_slot(model, root / 'slots'):
    event(1)
    time.sleep(0.4)
    event(-1)
'''
    def execute(model):
        subprocess.run([sys.executable, "-c", script, str(tmp_path), model], cwd=ROOT, check=True, capture_output=True)
    with ThreadPoolExecutor(max_workers=20) as executor:
        list(executor.map(execute, ["glm"] * 8 + ["deepseek"] * 12))
    active, peaks = {"glm": 0, "deepseek": 0}, {"glm": 0, "deepseek": 0}
    for line in (tmp_path / "events.jsonl").read_text().splitlines():
        row = json.loads(line); model = row["model"]
        active[model] += row["delta"]; peaks[model] = max(peaks[model], active[model])
    assert active == {"glm": 0, "deepseek": 0}
    assert peaks["glm"] == 2 and peaks["deepseek"] == 2
