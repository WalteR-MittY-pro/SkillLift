from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
import json
import os
import re
import sys
import time

from skilllift_eval.benchmarks.tau2_feedback import sanitize_tau2_feedback
from skilllift_eval.model_endpoints import ModelEndpointProfile
from skilllift_eval.parsers.tau2_scores import (
    SCORE_PARSER_VERSION,
    messages_for_feedback,
    parse_tau2_simulation,
    simulation_to_jsonable,
)
from skilllift_eval.resume import ResumeInputs, plan_resume
from skilllift_eval.schemas import LoadedSkillContext, TaskRunRecord, stable_hash
from skilllift_eval.skills.tau2_loader import MANIFEST_SCHEMA_VERSION
from skilllift_eval.tokens import estimate_tokens


RUNNER_VERSION = "tau2_runner_v1"
EMPTY_CONTEXT_LOADER_VERSION = "tau2_empty_context_v1"
EMPTY_CONTEXT_MANIFEST_SCHEMA_VERSION = "tau2_empty_context_manifest_v1"
NO_SKILL_HASH = stable_hash({"baseline": "no_skill", "skills": []})
EMPTY_TAU2_PROMPT_HASH = stable_hash({"tau2_prompt_context": "empty"})
ALLOWED_DOMAINS = {"airline", "retail", "telecom"}
PHASE3_SPLIT = "base"
TaskLoader = Callable[[str, str, list[str] | None], list[Any]]
TaskRunner = Callable[[Any, Any, int], Any]


@dataclass(frozen=True)
class Tau2RunSettings:
    run_root: Path
    model_endpoint: ModelEndpointProfile
    matrix_cell_id: str
    model_label: str
    endpoint_config_hash: str
    algorithm_param_hash: str
    benchmark_source_hash: str
    task_set_hash: str
    run_config_hash: str
    evaluation_mode: str
    context_budget_profile: str
    visible_feedback_policy_id: str
    oracle_call_budget: str
    task_sample_policy_id: str
    round_budget: str
    token_budget: str
    domains: tuple[str, ...]
    split: str
    tasks_mode: str
    prompt_hash: str = EMPTY_TAU2_PROMPT_HASH
    task_filter: str | None = None
    seed: int = 42
    max_steps: int = 100


@dataclass(frozen=True)
class Tau2RunResult:
    records: list[TaskRunRecord]
    summary: dict[str, Any]
    resume: dict[str, int]


class Tau2Runner:
    def __init__(
        self,
        *,
        tau2_src: Path | None = None,
        task_loader: TaskLoader | None = None,
        task_runner: TaskRunner | None = None,
    ) -> None:
        self.tau2_src = tau2_src
        self._task_loader = task_loader
        self._task_runner = task_runner

    def run(
        self,
        settings: Tau2RunSettings,
        context: LoadedSkillContext | None = None,
    ) -> Tau2RunResult:
        _validate_settings(settings)
        if self._task_loader is None or self._task_runner is None:
            self._ensure_tau2_importable()
        if self._task_runner is None:
            _install_tau2_llm_routing(settings.model_endpoint)
        context = context or _empty_context(settings.run_root)
        context.validate_for_runner("tau2")
        if context.kind != "empty_context":
            raise ValueError("Phase 3 tau2 no-skill runner only accepts empty_context")

        records: list[TaskRunRecord] = []
        resume = {"skipped": 0, "rerun": 0, "run": 0, "failed": 0}
        task_ids = [settings.task_filter] if settings.tasks_mode == "single" and settings.task_filter else None
        for domain in settings.domains:
            try:
                tasks = self._task_loader_func()(domain, settings.split, task_ids)
            except Exception as exc:
                record = self._task_set_failure(settings, context, domain, exc)
                records.append(record)
                resume["run"] += 1
                resume["failed"] += 1
                continue
            for task in tasks:
                record, action = self._run_or_resume(settings, context, domain, task)
                records.append(record)
                resume[action] = resume.get(action, 0) + 1
                if record.status != "succeeded":
                    resume["failed"] += 1
        return Tau2RunResult(records, _cell_summary(settings, records), resume)

    def _run_or_resume(
        self,
        settings: Tau2RunSettings,
        context: LoadedSkillContext,
        domain: str,
        task: Any,
    ) -> tuple[TaskRunRecord, str]:
        task_dir = settings.run_root / "tasks" / domain / _task_id(task)
        task_dir.mkdir(parents=True, exist_ok=True)
        _write_redacted_run_config(task_dir / "run_config.redacted.json", settings)
        manifest_path = _write_empty_manifest(task_dir, settings, context, domain, task)
        current = _resume_inputs(settings, context)
        existing = _load_record(task_dir / "task_run_record.json")
        decision = plan_resume(existing, current)
        if decision.action == "skip" and existing is not None:
            return existing, "skipped"
        action = "rerun" if existing is not None else "run"
        record = self._execute_task(settings, context, domain, task, task_dir, manifest_path)
        return record, action

    def _execute_task(
        self,
        settings: Tau2RunSettings,
        context: LoadedSkillContext,
        domain: str,
        task: Any,
        task_dir: Path,
        manifest_path: Path,
    ) -> TaskRunRecord:
        started_at = _utc_now()
        raw_path = task_dir / "raw_output.json"
        score_path = task_dir / "score.json"
        sanitized_path = task_dir / "sanitized_feedback.json"
        try:
            simulation = self._task_runner_func()(
                self._run_config(settings, domain),
                task,
                settings.seed,
            )
            raw = simulation_to_jsonable(simulation)
            score = parse_tau2_simulation(simulation)
            sanitized = sanitize_tau2_feedback(
                {
                    "task_id": _task_id(task),
                    "domain": domain,
                    "score": score["score"],
                    "messages": messages_for_feedback(simulation),
                },
                token_budget=settings.token_budget,
            )
            _write_json(raw_path, raw)
            _write_json(score_path, score)
            _write_json(sanitized_path, sanitized)
            return _record(settings, context, domain, task, manifest_path, raw_path, score_path, started_at, score)
        except Exception as exc:
            error = {"status": "failed", "error_type": type(exc).__name__, "error_summary": str(exc)}
            _write_json(raw_path, error)
            _write_json(score_path, error)
            return _record(settings, context, domain, task, manifest_path, raw_path, score_path, started_at, error)

    def _task_set_failure(
        self,
        settings: Tau2RunSettings,
        context: LoadedSkillContext,
        domain: str,
        exc: Exception,
    ) -> TaskRunRecord:
        task = {"id": "__task_set_resolution__"}
        task_dir = settings.run_root / "tasks" / domain / _task_id(task)
        task_dir.mkdir(parents=True, exist_ok=True)
        _write_redacted_run_config(task_dir / "run_config.redacted.json", settings)
        manifest_path = _write_empty_manifest(task_dir, settings, context, domain, task)
        raw_path = task_dir / "raw_output.json"
        score_path = task_dir / "score.json"
        error = {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error_summary": str(exc),
            "requested_split": settings.split,
            "domain": domain,
        }
        _write_json(raw_path, error)
        _write_json(score_path, error)
        return _record(
            settings,
            context,
            domain,
            task,
            manifest_path,
            raw_path,
            score_path,
            _utc_now(),
            error,
        )

    def _task_loader_func(self) -> TaskLoader:
        if self._task_loader is not None:
            return self._task_loader
        self._ensure_tau2_importable()
        from tau2.run import get_tasks

        return lambda domain, split, task_ids=None: get_tasks(domain, task_split_name=split, task_ids=task_ids)

    def _task_runner_func(self) -> TaskRunner:
        if self._task_runner is not None:
            return self._task_runner
        self._ensure_tau2_importable()
        from tau2.run import run_single_task

        return lambda config, task, seed: run_single_task(config, task, seed=seed)

    def _text_run_config(self, settings: Tau2RunSettings, domain: str) -> Any:
        self._ensure_tau2_importable()
        from tau2.data_model.simulation import TextRunConfig

        llm_args = _endpoint_llm_args(settings.model_endpoint)
        return TextRunConfig(
            domain=domain,
            task_split_name=settings.split,
            agent="llm_agent",
            user="user_simulator",
            llm_agent=_litellm_model(settings.model_endpoint),
            llm_user=_litellm_model(settings.model_endpoint),
            llm_args_agent=llm_args,
            llm_args_user=llm_args,
            max_steps=settings.max_steps,
            seed=settings.seed,
        )

    def _run_config(self, settings: Tau2RunSettings, domain: str) -> Any:
        if self._task_runner is not None:
            return {
                "domain": domain,
                "task_split_name": settings.split,
                "agent": "llm_agent",
                "user": "user_simulator",
                "llm_agent": _litellm_model(settings.model_endpoint),
                "llm_user": _litellm_model(settings.model_endpoint),
                "max_steps": settings.max_steps,
                "seed": settings.seed,
            }
        return self._text_run_config(settings, domain)

    def _ensure_tau2_importable(self) -> None:
        if self.tau2_src is None:
            root = Path(__file__).resolve().parents[2]
            self.tau2_src = root / "tau2-bench" / "src"
        src = str(self.tau2_src)
        if src not in sys.path:
            sys.path.insert(0, src)


def _record(
    settings: Tau2RunSettings,
    context: LoadedSkillContext,
    domain: str,
    task: Any,
    manifest_path: Path,
    raw_path: Path,
    score_path: Path,
    started_at: str,
    score: dict[str, Any],
) -> TaskRunRecord:
    status = "succeeded" if score.get("status") != "failed" else "failed"
    usage = score.get("usage") or {}
    record = TaskRunRecord(
        run_id=f"{settings.matrix_cell_id}__{domain}__{_task_id(task)}",
        matrix_cell_id=settings.matrix_cell_id,
        baseline="no_skill",
        benchmark="tau2",
        model_label=settings.model_label,
        model_endpoint_id=settings.model_endpoint.model_endpoint_id,
        provider_model_id=settings.model_endpoint.provider_model_id,
        endpoint_config_hash=settings.endpoint_config_hash,
        algorithm_param_hash=settings.algorithm_param_hash,
        benchmark_source_hash=settings.benchmark_source_hash,
        task_set_hash=settings.task_set_hash,
        loader_version=EMPTY_CONTEXT_LOADER_VERSION,
        loader_manifest_schema_version=EMPTY_CONTEXT_MANIFEST_SCHEMA_VERSION,
        score_parser_version=SCORE_PARSER_VERSION,
        run_config_hash=settings.run_config_hash,
        task_id=_task_id(task),
        domain=domain,
        skill_hash=context.skill_hash,
        prompt_hash=settings.prompt_hash,
        loader_manifest_path=str(manifest_path.resolve()),
        raw_output_path=str(raw_path.resolve()),
        score_path=str(score_path.resolve()),
        status=status,
        started_at=started_at,
        finished_at=_utc_now(),
        error_summary=score.get("error_summary"),
        token_estimate=_estimate_context(task),
        token_actual=usage.get("total_tokens", "unavailable"),
        cost_actual=score.get("cost_actual", "unavailable"),
        evaluation_mode=settings.evaluation_mode,
        context_budget_profile=settings.context_budget_profile,
        visible_feedback_policy_id=settings.visible_feedback_policy_id,
        oracle_calls_used=1,
        oracle_call_budget=settings.oracle_call_budget,
        task_sample_policy_id=settings.task_sample_policy_id,
        round_budget=settings.round_budget,
        token_budget=settings.token_budget,
        context_input_chars=len(json.dumps(_task_public_data(task), ensure_ascii=False)),
        context_input_tokens_estimate=_estimate_context(task),
        context_truncated=False,
    )
    _write_json(raw_path.parent / "task_run_record.json", record.to_dict())
    return record


def _cell_summary(settings: Tau2RunSettings, records: list[TaskRunRecord]) -> dict[str, Any]:
    scores = []
    for record in records:
        if record.status != "succeeded":
            continue
        payload = json.loads(Path(record.score_path).read_text(encoding="utf-8"))
        scores.append(float(payload["score"]))
    expected = len(records)
    succeeded = len(scores)
    completion = succeeded / expected if expected else 0.0
    status = "SUCCEEDED" if completion == 1.0 else "PARTIAL" if completion >= 0.8 else "FAILED"
    return {
        "matrix_cell_id": settings.matrix_cell_id,
        "baseline": "no_skill",
        "benchmark": "tau2",
        "model_label": settings.model_label,
        "evaluation_mode": settings.evaluation_mode,
        "context_budget_profile": settings.context_budget_profile,
        "expected_count": expected,
        "succeeded_count": succeeded,
        "completion_ratio": completion,
        "status": status,
        "mean_score": sum(scores) / len(scores) if scores and completion >= 0.8 else None,
        "artifact": str(settings.run_root.resolve()),
        "source": "self_run",
    }


def _resume_inputs(settings: Tau2RunSettings, context: LoadedSkillContext) -> ResumeInputs:
    return ResumeInputs(
        provider_model_id=settings.model_endpoint.provider_model_id,
        endpoint_config_hash=settings.endpoint_config_hash,
        algorithm_param_hash=settings.algorithm_param_hash,
        benchmark_source_hash=settings.benchmark_source_hash,
        task_set_hash=settings.task_set_hash,
        loader_version=EMPTY_CONTEXT_LOADER_VERSION,
        loader_manifest_schema_version=EMPTY_CONTEXT_MANIFEST_SCHEMA_VERSION,
        score_parser_version=SCORE_PARSER_VERSION,
        run_config_hash=settings.run_config_hash,
        skill_hash=context.skill_hash,
        prompt_hash=settings.prompt_hash,
        evaluation_mode=settings.evaluation_mode,
        context_budget_profile=settings.context_budget_profile,
        visible_feedback_policy_id=settings.visible_feedback_policy_id,
        oracle_call_budget=settings.oracle_call_budget,
        task_sample_policy_id=settings.task_sample_policy_id,
        round_budget=settings.round_budget,
        token_budget=settings.token_budget,
    )


def _empty_context(run_root: Path) -> LoadedSkillContext:
    return LoadedSkillContext(
        kind="empty_context",
        benchmark="tau2",
        loader_type="empty",
        skill_hash=NO_SKILL_HASH,
        manifest_path=str((run_root / "empty_context_manifest.json").resolve()),
    )


def _write_empty_manifest(
    task_dir: Path,
    settings: Tau2RunSettings,
    context: LoadedSkillContext,
    domain: str,
    task: Any,
) -> Path:
    path = task_dir / "loader_manifest.json"
    _write_json(
        path,
        {
            "loader_version": EMPTY_CONTEXT_LOADER_VERSION,
            "loader_manifest_schema_version": EMPTY_CONTEXT_MANIFEST_SCHEMA_VERSION,
            "kind": "empty_context",
            "benchmark": "tau2",
            "loader_type": "empty",
            "agent": "llm_agent",
            "skill_hash": context.skill_hash,
            "prompt_hash": settings.prompt_hash,
            "domain": domain,
            "task_id": _task_id(task),
            "no_skill_policy": "standard_llm_agent_empty_context",
            "skill_injected_reserved_for": "Phase 4-7",
            "source_loader_manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        },
    )
    return path


def _write_redacted_run_config(path: Path, settings: Tau2RunSettings) -> None:
    data = asdict(settings)
    data["run_root"] = str(settings.run_root.resolve())
    data["model_endpoint"] = settings.model_endpoint.redacted()
    _write_json(path, data)


def _load_record(path: Path) -> TaskRunRecord | None:
    if not path.exists():
        return None
    return TaskRunRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _endpoint_llm_args(endpoint: ModelEndpointProfile) -> dict[str, Any]:
    key = endpoint.api_key or (os.environ.get(endpoint.api_key_env or ""))
    if not key:
        raise ValueError(f"missing API key env {endpoint.api_key_env} for {endpoint.model_label}")
    return {"api_key": key, "api_base": endpoint.base_url, "temperature": 0}


def _litellm_model(endpoint: ModelEndpointProfile) -> str:
    model = endpoint.provider_model_id
    if endpoint.provider in {"openai-compatible", "openai-completions"} and "/" not in model:
        return f"openai/{model}"
    return model


_TAU2_LLM_ROUTING_ATTR = "_skilllift_eval_routing_installed"

# Gateway-side transient failures observed on the proxies. These are
# infrastructure flakiness (the upstream rotates backends / sheds load), not
# real model or argument errors, so retrying is the correct response. litellm
# itself only retries 408/429/5xx, so BadRequest/empty-body failures need an
# explicit retry here.
_TRANSIENT_MARKERS = (
    "模型不存在",                      # gateway: model lookup flicker
    "not supported when using",        # gateway: Codex-routing misfire
    "Expecting value",                 # empty/non-JSON response body
    "JSONDecodeError",
    "Connection error",                # network/transient gateway drop
    "BadGateway",                      # gateway 502
    "Service Unavailable",             # gateway 503
    "Overloaded",                      # provider-side capacity
    "timeout",                         # request timeout (incl. hung TCP)
    "Timed out",
    "ReadTimeout",
)
_TRANSIENT_MAX_RETRIES = 7


def _is_transient(exc: BaseException) -> bool:
    msg = str(exc)
    return any(marker in msg for marker in _TRANSIENT_MARKERS)


_CODE_FENCE_RE = re.compile(r"^\s*```(?:json|JSON)?\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL)


def _strip_code_fence(content: str | None) -> str | None:
    """Strip a single surrounding markdown code fence.

    Some providers (notably Claude via OpenAI-compatible gateways) wrap JSON
    answers in ```json ... ``` fences. tau2's evaluators ``json.loads`` the
    raw content and fail at column 0. Only strips when the *entire* content is
    one fenced block, so ordinary conversational responses are untouched.
    """
    if not content or not content.lstrip().startswith("```"):
        return content
    match = _CODE_FENCE_RE.match(content.strip())
    return match.group(1).strip() if match else content


def _install_tau2_llm_routing(endpoint: ModelEndpointProfile) -> None:
    """Redirect every tau2 LLM call to the configured endpoint.

    tau2's evaluators (NL assertions, auth classifier, env interface, reviewers)
    call ``litellm.completion`` via ``tau2.utils.llm_utils.completion`` with
    hardcoded defaults (e.g. ``gpt-4.1``, ``claude-opus-4-5``) and no
    ``api_key``/``api_base``. That single symbol is the convergence point for
    all tau2 LLM traffic. Wrapping it forces every call — simulation and
    evaluation alike — onto the skilllift_eval-configured endpoint, without
    editing tau2-bench source. Also retries the gateway-side transient
    failures (model-lookup flicker, Codex-routing misfire, empty body) that
    litellm does not retry by default. Idempotent.
    """
    from tau2.utils import llm_utils

    if getattr(llm_utils.completion, _TAU2_LLM_ROUTING_ATTR, False):
        return

    model = _litellm_model(endpoint)
    api_key = endpoint.api_key or os.environ.get(endpoint.api_key_env or "")
    if not api_key:
        raise ValueError(
            f"missing API key env {endpoint.api_key_env} for {endpoint.model_label}"
        )
    api_base = endpoint.base_url
    original = llm_utils.completion

    def _routed(*args, **kwargs):
        # Pace calls to the gateway: a 1s gap before every tau2 LLM call
        # avoids the upstream RateLimitError seen under tight serial load.
        time.sleep(1)
        # litellm.completion(model, **kwargs) — model may be positional or keyword.
        if args:
            args = (model,) + args[1:]
        else:
            kwargs["model"] = model
        kwargs.setdefault("api_key", api_key)
        kwargs.setdefault("api_base", api_base)
        # Bound the request so a hung TCP connection (observed on telecom
        # long simulations) fails fast instead of hanging the whole run.
        kwargs.setdefault("timeout", endpoint.timeout_seconds)
        # litellm handles its own retry budget for 408/429/5xx; disable it so
        # our transient retry (which also covers 400/empty-body) is the single
        # authority and total attempts stay bounded.
        kwargs.setdefault("num_retries", 0)
        last_exc: BaseException | None = None
        for attempt in range(_TRANSIENT_MAX_RETRIES + 1):
            try:
                response = original(*args, **kwargs)
                # Normalize fenced JSON so tau2's json.loads evaluators parse it.
                try:
                    msg = response.choices[0].message
                    if msg.content:
                        msg.content = _strip_code_fence(msg.content)
                except (AttributeError, IndexError, TypeError):
                    pass
                return response
            except Exception as exc:
                last_exc = exc
                if attempt == _TRANSIENT_MAX_RETRIES or not _is_transient(exc):
                    raise
                time.sleep(min(2 ** attempt, 8))
        raise last_exc  # pragma: no cover - loop returns or raises above

    _routed._skilllift_eval_routing_installed = True  # type: ignore[attr-defined]
    llm_utils.completion = _routed


def _validate_settings(settings: Tau2RunSettings) -> None:
    if settings.split != PHASE3_SPLIT:
        raise ValueError("tau2 no-skill Phase 3 only runs base split")
    bad = [domain for domain in settings.domains if domain not in ALLOWED_DOMAINS]
    if bad:
        raise ValueError(f"unsupported tau2 domains: {', '.join(bad)}")
    if settings.tasks_mode not in {"single", "full", "resume"}:
        raise ValueError(f"unsupported tasks_mode {settings.tasks_mode}")
    if settings.tasks_mode == "single" and not settings.task_filter:
        raise ValueError("tasks_mode single requires task_filter")


def _task_id(task: Any) -> str:
    if isinstance(task, dict):
        return str(task.get("id", "unknown-task"))
    return str(getattr(task, "id", "unknown-task"))


def _task_public_data(task: Any) -> dict[str, Any]:
    if isinstance(task, dict):
        data = task
    elif hasattr(task, "model_dump"):
        data = task.model_dump(mode="json")
    else:
        data = {"id": _task_id(task)}
    return {key: data[key] for key in ("id", "user_scenario", "ticket") if key in data}


def _estimate_context(task: Any) -> int:
    return estimate_tokens(json.dumps(_task_public_data(task), ensure_ascii=False))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
