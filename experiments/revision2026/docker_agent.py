"""Shared OpenAI-compatible solver for SkillLearnBench's pinned environments.

The official Claude/Codex/Gemini CLI solver is deliberately not relabelled as a
GLM/DeepSeek run. Only public task inputs enter the solver; scoring starts after
its container is stopped. Original verifier code is mounted only in the scorer.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import tomllib
import uuid

from .common import append_event, digest, endpoint, tree_hash, utc_now, write_json
from .sources import PINS, source_path

TOOLS = [{"type": "function", "function": {"name": "shell", "description": "Run a shell command inside the task's Linux container. Read and write task files with this tool.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
         {"type": "function", "function": {"name": "read_image", "description": "Inspect a PNG/JPEG/WebP/GIF image at an absolute container path.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]


def command(args, *, timeout=1800, check=True, text=True):
    return subprocess.run(args, capture_output=True, text=text, timeout=timeout, check=check)


def official_helpers():
    path = source_path("skilllearnbench") / "core" / "eval_runner.py"
    spec = importlib.util.spec_from_file_location("revision_skilllearn_helpers", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def base_image(instance, run_root):
    import fcntl
    name = "skilllift-slb-" + digest({"source": PINS["skilllearnbench"][1], "task": instance.task_id})[:20]
    locks = Path(run_root) / "image-locks"
    locks.mkdir(parents=True, exist_ok=True)
    with (locks / (name + ".lock")).open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if command(["docker", "image", "inspect", name], check=False).returncode == 0:
            return name
        helpers = official_helpers()
        context = helpers._prepare_build_env(instance.path / "environment", "no_skill", None)
        try:
            for src, _ in helpers._parse_skill_copies(context / "Dockerfile"):
                if src.startswith("skills/"):
                    (context / src).mkdir(parents=True, exist_ok=True)
            built = command(["docker", "build", "-t", name, str(context)], timeout=3600, check=False)
            (locks / (name + ".build.log")).write_text(built.stdout + built.stderr)
            if built.returncode:
                raise RuntimeError(f"Image build failed; see {locks / (name + '.build.log')}")
        finally:
            shutil.rmtree(context.parent)
    return name


def solver_command(container, image, required_env):
    args = ["docker", "run", "-d", "--name", container]
    for name in required_env:
        if not os.environ.get(name):
            raise ValueError(f"Task requires {name}")
        args += ["-e", name]
    return args + [image, "sleep", "7200"]


def scorer_command(container, snapshot, task_path, output):
    return ["docker", "run", "--name", container,
            "--mount", f"type=bind,source={task_path / 'tests'},target=/tests,readonly",
            "--mount", f"type=bind,source={output},target=/logs",
            "--entrypoint", "/bin/bash", snapshot, "/tests/test.sh"]


def inject_skills(container, source, dockerfile):
    source = Path(source).resolve()
    copies = [(source, "/root/.claude/skills")]
    for pattern, destination in official_helpers()._parse_skill_copies(dockerfile):
        local = source if pattern == "skills" else source / pattern.removeprefix("skills/")
        if local.exists() and (local, destination) not in copies:
            copies.append((local, destination))
    for local, destination in copies:
        command(["docker", "exec", container, "mkdir", "-p", destination])
        command(["docker", "cp", str(local) + ("/." if local.is_dir() else ""), f"{container}:{destination}"])


def tool_result(container, name, arguments, timeout):
    if name == "shell":
        # The in-container timeout also terminates descendants if docker exec times out.
        result = command(["docker", "exec", container, "timeout", "-k", "5", str(timeout),
                          "bash", "-c", arguments["command"]], timeout=timeout + 15, check=False)
        return f"exit={result.returncode}\n{result.stdout}\n{result.stderr}"[-30000:], None
    if name == "read_image":
        path = str(arguments["path"])
        if not path.startswith("/"):
            raise ValueError("Image path must be absolute")
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "image"
            command(["docker", "cp", f"{container}:{path}", str(local)], timeout=30)
            data = local.read_bytes()
        if len(data) > 10 * 1024 * 1024:
            raise ValueError("Image exceeds 10 MiB")
        mime = ("image/png" if data.startswith(b"\x89PNG") else "image/jpeg" if data.startswith(b"\xff\xd8")
                else "image/gif" if data.startswith(b"GIF8") else "image/webp" if data[:4] == b"RIFF" and data[8:12] == b"WEBP" else None)
        if mime is None:
            raise ValueError("Unsupported image format")
        return "Image attached in the next message.", f"data:{mime};base64,{base64.b64encode(data).decode()}"
    raise ValueError("Unknown tool")


def run_solver(container, instruction, skill_dir, model, settings, output, ledger):
    from openai import OpenAI
    ep = endpoint(model)
    client = OpenAI(api_key=ep["API_KEY"], base_url=ep["BASE_URL"], timeout=300, max_retries=0)
    catalog = []
    if skill_dir:
        import yaml
        for path in sorted(Path(skill_dir).glob("*/SKILL.md")):
            meta = yaml.safe_load(path.read_text().split("---", 2)[1])
            catalog.append({"name": meta["name"], "description": meta["description"], "path": f"/root/.claude/skills/{path.parent.name}/SKILL.md"})
    system = "Solve the user's task in the provided Linux environment. Use the tools to inspect inputs and create the requested artifacts. Available skills are listed below; read applicable SKILL.md files before using them. Do not search for hidden benchmark tests, reference solutions, or reward files.\n" + json.dumps(catalog, ensure_ascii=False)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": instruction}]
    from .resilience import policy_from, retry_call
    policy = policy_from({"retry": settings.get("retry", {})})
    reader = None
    if model in settings.get("vision", {}).get("enabled_for", []):
        from .vision_bridge import ImageReader
        reader = ImageReader(endpoint("glm_vision"), ledger, attempts=policy["api_max_attempts"],
                             max_tokens=settings["vision"]["max_output_tokens"])
    began = time.monotonic()
    excluded = 0.0  # time lost to transient API failures and backoff never counts against the solver budget
    status = "step_limit"
    for step in range(settings["max_steps"]):
        if time.monotonic() - began - excluded > settings["timeout_seconds"]:
            status = "solver_timeout"
            break
        call_started, last_attempt = time.monotonic(), {}

        def request():
            last_attempt["start"] = time.monotonic()
            return client.chat.completions.create(model=ep["MODEL"], messages=messages, tools=TOOLS,
                                                  temperature=settings["temperature"], max_tokens=settings["max_output_tokens"])

        reply = retry_call(request, attempts=policy["api_max_attempts"], base=policy["api_base_seconds"], cap=policy["api_max_seconds"],
                           describe="solver", log=lambda event: append_event(ledger, {**event, "step": step}))
        excluded += last_attempt["start"] - call_started
        usage = reply.usage.model_dump() if reply.usage else None
        append_event(ledger, {"kind": "solver_usage", "step": step, "model": ep["MODEL"], "usage": usage,
                              "usage_available": usage is not None, "response_model": reply.model})
        msg = reply.choices[0].message
        value = msg.model_dump(exclude_none=True)
        # Preserve reasoning_content when returned: thinking tool-call APIs require it.
        messages.append(value)
        write_json(output / "conversation.json", messages)
        if not msg.tool_calls:
            status = "completed"
            break
        images = []
        for call in msg.tool_calls:
            try:
                content, image = tool_result(container, call.function.name, json.loads(call.function.arguments), settings["command_timeout_seconds"])
                if image:
                    if reader is not None:
                        content = reader.describe(image)
                    else:
                        images.append({"type": "image_url", "image_url": {"url": image}})
            except (ValueError, KeyError, subprocess.SubprocessError) as exc:
                content = f"Tool failed: {type(exc).__name__}"
            messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
        if images:
            messages.append({"role": "user", "content": images})
        write_json(output / "conversation.json", messages)  # Retain completed tool actions if the next API request fails.
    write_json(output / "conversation.json", messages)
    return status, messages


def evaluate(instance, skill_dir, model, settings, output, ledger, run_root):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "verifier").mkdir()
    token = uuid.uuid4().hex
    solver, scorer, snapshot = f"slb-s-{token}", f"slb-v-{token}", f"slb-state:{token}"
    started = utc_now()
    began = time.monotonic()
    result = {"task_id": instance.task_id, "family": instance.family, "instance": instance.number,
              "model": endpoint(model)["MODEL"], "skill_hash": tree_hash(skill_dir) if skill_dir else None,
              "started_at": started, "status": "ERROR", "reward": None, "harness": "revision-openai-docker-v1"}
    messages = []
    try:
        config_path = instance.path / "task.toml"
        cfg = tomllib.loads(config_path.read_text()) if config_path.exists() else {}
        image = base_image(instance, run_root)
        command(solver_command(solver, image, cfg.get("environment", {}).get("required_env", [])))
        if skill_dir:
            inject_skills(solver, skill_dir, instance.path / "environment/Dockerfile")
        result["environment_setup_seconds"] = time.monotonic() - began
        solver_began = time.monotonic()
        state, messages = run_solver(solver, (instance.path / "instruction.md").read_text(), skill_dir, model, settings, output, ledger)
        result["solver_seconds"] = time.monotonic() - solver_began
        result["solver_status"] = state
        # Stop all solver processes before injecting any hidden verifier material.
        command(["docker", "stop", "-t", "1", solver])
        command(["docker", "commit", solver, snapshot], timeout=300)
        verifier_began = time.monotonic()
        verification = command(scorer_command(scorer, snapshot, instance.path.resolve(), output), check=False, timeout=1800)
        result["verifier_seconds"] = time.monotonic() - verifier_began
        (output / "verifier.stdout").write_text(verification.stdout)
        (output / "verifier.stderr").write_text(verification.stderr)
        result["verifier_exit"] = verification.returncode
        if verification.returncode != 0:
            raise RuntimeError("Verifier process failed; a partially written reward is not a valid score")
        script = instance.path / "tests/test.sh"
        if script.is_file() and "--ctrf" in script.read_text():
            report = output / "verifier/ctrf.json"
            if not report.is_file():
                raise RuntimeError("Verifier promised a CTRF report but produced none")
            summary = json.loads(report.read_text()).get("results", {}).get("summary", {})
            if int(summary.get("passed", 0)) + int(summary.get("failed", 0)) == 0:
                raise RuntimeError("No verifier tests actually ran")
        reward_path = output / "verifier" / "reward.txt"
        if not reward_path.is_file():
            raise RuntimeError("Verifier did not write reward.txt")
        raw = reward_path.read_text().strip()
        if raw not in {"0", "1", "0.0", "1.0"}:
            raise ValueError("Invalid official reward")
        result.update(status="OK", reward=float(raw))
        # Raw assertion text may contain hidden answers. It never enters training feedback.
        result["public_feedback"] = {"reward": float(raw), "solver_status": state,
                                     "verifier_exit": verification.returncode,
                                     "solver_last_message": str(messages[-1].get("content", ""))[-12000:] if messages else ""}
    except Exception as exc:
        result["error_type"] = type(exc).__name__
        # Keep the error class and status so transient API failures can be retried; never a score.
        status_code = getattr(exc, "status_code", None)
        result["error"] = (str(exc) if isinstance(exc, (ValueError, RuntimeError))
                           else f"{type(exc).__name__}{f' status={status_code}' if status_code else ''}: {str(exc)[:300]}")
    finally:
        for name in (solver, scorer):
            command(["docker", "rm", "-f", name], check=False, timeout=60)
        command(["docker", "image", "rm", snapshot], check=False, timeout=60)
    result["finished_at"] = utc_now()
    result["total_elapsed_seconds"] = time.monotonic() - began
    write_json(output / "result.json", result)
    append_event(ledger, {"kind": "oracle_rollout", "result": str(output / "result.json"), **result})
    return result
