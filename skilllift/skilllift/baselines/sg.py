from __future__ import annotations

import re
import uuid

from ..llm_client import LLMClient
from ..errors import LLMOutputError, SkillPackageError
from .prompts import _mode_a_actionable_target_rubrics
from .prompts import build_sg_repair_prompt, build_sg_seed_prompt, build_sg_update_prompt, build_sg_variant_prompt
from ..schemas import SkillLiftConfig, EvoSkill, Receipt, SkillKey, TaskSpec, VerifierScore
from .skill_package import (
    GeneratedSkillPackage,
    ensure_openclaw_skill_frontmatter,
    merge_skill_patch,
    parse_skill_package,
    validate_agent_skill_name,
)

GUIDANCE_START = "<!-- skilllift-guidance:start -->"
GUIDANCE_END = "<!-- skilllift-guidance:end -->"
MODE_A_STATUS_METADATA_KEYS = {
    "fallback_update",
    "fallback_update_reason",
    "llm_update",
    "mode_a_update_failed",
    "mode_a_update_skipped",
    "mode_a_update_skip_reason",
    "repair_attempted",
}


def initialize_skill_group(
    task: TaskSpec,
    config: SkillLiftConfig,
    reference_material: str,
    llm_client: LLMClient | None,
) -> dict[SkillKey, EvoSkill]:
    seed = generate_seed_skill(task, reference_material, llm_client, skill_format=config.skill_format)
    group = {seed.key: seed}
    for slot in range(1, config.skill_count):
        variant = generate_variant_skill(task, seed, slot, llm_client, skill_format=config.skill_format)
        group[variant.key] = variant
    return group


def generate_seed_skill(
    task: TaskSpec,
    reference_material: str,
    llm_client: LLMClient | None,
    skill_format: str = "code_package",
) -> EvoSkill:
    package = _package_from_llm_or_fallback(task, reference_material, llm_client, "seed", skill_format)
    return _skill_from_package(task, package, SkillKey(0, 1), "seed", skill_format)


def generate_variant_skill(
    task: TaskSpec,
    seed: EvoSkill,
    slot: int,
    llm_client: LLMClient | None,
    skill_format: str = "code_package",
) -> EvoSkill:
    if llm_client is None:
        package = _fallback_package(task, f"variant_{slot}", extra=f"Variant slot {slot}.", skill_format=skill_format)
    else:
        content_block = _content_block_token()
        system, user = build_sg_variant_prompt(
            task,
            seed,
            slot,
            skill_format=skill_format,
            content_block=content_block,
        )
        try:
            payload = _call_skill_generator(llm_client, system, user, 0.5, skill_format)
            package = _parse_skill_package(task, payload, f"variant_{slot}", llm_client, skill_format=skill_format, content_block=content_block)
        except SkillPackageError:
            if skill_format == "agent_skill":
                raise
            package = _fallback_package(task, f"variant_{slot}", extra=f"Variant slot {slot}.", skill_format=skill_format)
    return _skill_from_package(task, package, SkillKey(slot, 1), f"variant_{slot}", skill_format)


def update_skill(
    task: TaskSpec,
    skill: EvoSkill,
    receipt: Receipt,
    score: VerifierScore,
    llm_client: LLMClient | None,
    skill_format: str = "code_package",
) -> EvoSkill:
    new_key = SkillKey(skill.key.slot, skill.key.version + 1)
    if not _actionable_rubrics(receipt, score):
        return _skip_update(skill, "no_actionable_rubrics")
    if llm_client is None:
        return _fallback_updated_skill(task, skill, new_key, receipt, score, "llm_disabled", skill_format=skill_format)
    content_block = _content_block_token()
    system, user = build_sg_update_prompt(task, skill, receipt, score, skill_format=skill_format, content_block=content_block)
    try:
        payload = _call_skill_generator(llm_client, system, user, 0.2, skill_format)
    except LLMOutputError as exc:
        if skill_format == "agent_skill":
            raise
        print(f"[skilllift] mode A update LLM call failed; preserving old skill {skill.key.token()}: {exc}", flush=True)
        return _preserve_old_skill(skill, str(exc), repair_attempted=False)
    try:
        package = _parse_mode_a_package(task, skill, receipt, score, payload, llm_client, skill_format, content_block)
    except SkillPackageError as exc:
        if skill_format == "agent_skill":
            raise
        print(f"[skilllift] mode A update failed; preserving old skill {skill.key.token()}: {exc}", flush=True)
        return _preserve_old_skill(skill, str(exc), repair_attempted=True)
    metadata = _clean_mode_a_status_metadata({**skill.metadata, **package.metadata})
    metadata.update(
        {
            "llm_update": True,
            "package_mode": str(package.metadata.get("package_mode", "full")).strip().lower(),
        }
    )
    files = _normalize_skill_files(
        package.files,
        skill.skill_name,
        _metadata_description_hint(metadata, package.files, skill.skill_name),
        skill_format,
    )
    return EvoSkill(new_key, skill.skill_name, files, package.entrypoint, metadata)


def update_skill_group(
    task: TaskSpec,
    skills: dict[SkillKey, EvoSkill],
    receipt: Receipt,
    scores: dict[SkillKey, VerifierScore],
    llm_client: LLMClient | None,
    skill_format: str = "code_package",
) -> dict[SkillKey, EvoSkill]:
    updated: dict[SkillKey, EvoSkill] = {}
    for key, skill in sorted(skills.items()):
        new_skill = update_skill(task, skill, receipt, scores[key], llm_client, skill_format=skill_format)
        updated[new_skill.key] = new_skill
    return updated


def _package_from_llm_or_fallback(
    task: TaskSpec,
    reference_material: str,
    llm_client: LLMClient | None,
    strategy: str,
    skill_format: str = "code_package",
) -> GeneratedSkillPackage:
    if llm_client is None:
        initial_skill = _extract_initial_skill_markdown(reference_material)
        return _fallback_package(task, strategy, initial_skill_md=initial_skill, skill_format=skill_format)
    content_block = _content_block_token()
    system, user = build_sg_seed_prompt(task, reference_material, skill_format=skill_format, content_block=content_block)
    try:
        payload = _call_skill_generator(llm_client, system, user, 0.0, skill_format)
        return _parse_skill_package(task, payload, strategy, llm_client, skill_format=skill_format, content_block=content_block)
    except (LLMOutputError, SkillPackageError) as exc:
        if skill_format == "agent_skill":
            raise
        print(f"[skilllift] seed package generation failed; using fallback package: {exc}", flush=True)
        return _fallback_package(task, strategy, skill_format=skill_format)


def _parse_skill_package(
    task: TaskSpec,
    payload: dict | str,
    strategy: str,
    llm_client: LLMClient | None = None,
    base_files: dict[str, str] | None = None,
    skill_format: str = "code_package",
    content_block: str | None = None,
) -> GeneratedSkillPackage:
    try:
        if skill_format == "markdown_guide":
            if not isinstance(payload, str):
                raise SkillPackageError("markdown_guide output must be text")
            return parse_markdown_guide_output(payload, skill_format, _require_content_block(content_block))
        if not isinstance(payload, dict):
            raise SkillPackageError("code_package output must be JSON object")
        package = parse_skill_package(payload, base_files=base_files, skill_format=skill_format)
        if skill_format == "agent_skill":
            validate_agent_skill_name(package, _agent_skill_name(task))
        return package
    except SkillPackageError as exc:
        if llm_client is None:
            raise
        print(f"[skilllift] skill package invalid for {strategy}; requesting repair: {exc}", flush=True)
        system, user = build_sg_repair_prompt(
            task,
            strategy,
            payload,
            _skill_package_error_report(exc, payload),
            skill_format=skill_format,
            content_block=content_block,
        )
        repaired_payload = _call_skill_generator(llm_client, system, user, 0.0, skill_format)
        if skill_format == "markdown_guide":
            return parse_markdown_guide_output(str(repaired_payload), skill_format, _require_content_block(content_block))
        package = parse_skill_package(repaired_payload, base_files=base_files, skill_format=skill_format)
        if skill_format == "agent_skill":
            validate_agent_skill_name(package, _agent_skill_name(task))
        return package


def _parse_mode_a_package(
    task: TaskSpec,
    skill: EvoSkill,
    receipt: Receipt,
    score: VerifierScore,
    payload: dict | str,
    llm_client: LLMClient,
    skill_format: str = "code_package",
    content_block: str | None = None,
) -> GeneratedSkillPackage:
    try:
        package = _package_from_mode_a_payload(payload, skill.files, receipt, score, skill_format, content_block)
        if skill_format == "agent_skill":
            validate_agent_skill_name(package, skill.skill_name)
        return package
    except SkillPackageError as exc:
        print(f"[skilllift] skill package invalid for mode_a_update; requesting repair: {exc}", flush=True)
        report = _skill_package_error_report(exc, payload)
        report["allowed_target_rubrics"] = _mode_a_actionable_target_rubrics(receipt, score)
        system, user = build_sg_repair_prompt(
            task,
            "mode_a_update",
            payload,
            report,
            skill_format=skill_format,
            content_block=content_block,
        )
        repaired_payload = _call_skill_generator(llm_client, system, user, 0.0, skill_format)
        package = _package_from_mode_a_payload(repaired_payload, skill.files, receipt, score, skill_format, content_block)
        if skill_format == "agent_skill":
            validate_agent_skill_name(package, skill.skill_name)
        return package


def _package_from_mode_a_payload(
    payload: dict | str,
    base_files: dict[str, str],
    receipt: Receipt,
    score: VerifierScore,
    skill_format: str = "code_package",
    content_block: str | None = None,
) -> GeneratedSkillPackage:
    if skill_format == "markdown_guide":
        if not isinstance(payload, str):
            raise SkillPackageError("markdown_guide mode A output must be text")
        package = parse_markdown_guide_output(payload, skill_format, _require_content_block(content_block))
        validation_payload = _markdown_package_validation_payload(package)
        _validate_mode_a_update_payload(validation_payload, package, receipt, score)
        return package
    if not isinstance(payload, dict):
        raise SkillPackageError("Mode A package output must be a JSON object")
    mode = str(payload.get("package_mode", "")).strip().lower()
    if mode == "patch":
        package = merge_skill_patch(base_files, payload, skill_format=skill_format)
    elif mode == "full":
        package = parse_skill_package(payload, skill_format=skill_format)
    else:
        raise SkillPackageError("Mode A update must return package_mode 'patch' or 'full'")
    if skill_format == "code_package" and package.entrypoint is None:
        raise SkillPackageError("Mode A package must include entrypoint")
    _validate_mode_a_update_payload(payload, package, receipt, score)
    return package


def parse_markdown_guide_output(
    raw: str,
    skill_format: str,
    content_block: str,
) -> GeneratedSkillPackage:
    """Parse markdown_guide XML envelope output.

    Markdown-guide mode rewrites SKILL.md as a single file and intentionally
    does not support delete_files; tau2 has no multi-file package to delete from.
    Only the dynamic content_block tag has structural meaning inside SKILL.md.
    """
    if skill_format != "markdown_guide":
        raise SkillPackageError("parse_markdown_guide_output requires skill_format='markdown_guide'")
    block = _require_content_block(content_block)
    text = str(raw or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    package_match = re.search(r"<package\b([^>]*)>", text, re.S)
    if not package_match:
        raise SkillPackageError("markdown_guide output missing <package> tag")
    if text.rfind("</package>") <= package_match.end():
        raise SkillPackageError("markdown_guide output missing closing </package> tag")
    package_attrs = _tag_attrs(package_match.group(1))
    mode = str(package_attrs.get("mode", "")).strip()
    strategy = str(package_attrs.get("strategy", "")).strip()
    if mode not in {"full", "patch"}:
        raise SkillPackageError("markdown_guide package mode must be 'full' or 'patch'")
    if not strategy:
        raise SkillPackageError("markdown_guide package strategy is required")

    skill_body = _extract_dynamic_content_block(text, block)
    envelope_without_body = _remove_dynamic_content_block(text, block)
    file_attrs = _file_tag_attrs(envelope_without_body)
    if file_attrs.get("path") != "SKILL.md":
        raise SkillPackageError("markdown_guide file path must be SKILL.md")
    if file_attrs.get("content_block") != block:
        raise SkillPackageError("markdown_guide file content_block does not match parser token")

    metadata = {
        "package_mode": mode,
        "strategy": strategy,
    }
    summary = _optional_simple_tag(envelope_without_body, "summary")
    if summary:
        metadata["summary"] = summary
    if mode == "patch":
        metadata["targeted_rubrics"] = _comma_list(_optional_simple_tag(envelope_without_body, "targeted_rubrics"))
        metadata["expected_effect"] = _optional_simple_tag(envelope_without_body, "expected_effect")
        metadata["changed_files"] = _comma_list(_optional_simple_tag(envelope_without_body, "changed_files"))

    package = GeneratedSkillPackage(
        files={"SKILL.md": _ensure_newline(skill_body.strip())},
        entrypoint=None,
        metadata=metadata,
    )
    from .skill_package import validate_skill_package

    validate_skill_package(package, skill_format="markdown_guide")
    return package


def _call_skill_generator(
    llm_client: LLMClient,
    system: str,
    user: str,
    temperature: float,
    skill_format: str,
) -> dict | str:
    if skill_format == "markdown_guide":
        if not hasattr(llm_client, "call_text"):
            raise SkillPackageError("markdown_guide generation requires llm_client.call_text")
        return llm_client.call_text(system, user, temperature)
    return llm_client.call_json(system, user, temperature)


def _content_block_token() -> str:
    return f"skill_block_{uuid.uuid4().hex[:8]}"


def _require_content_block(content_block: str | None) -> str:
    block = str(content_block or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", block):
        raise SkillPackageError("markdown_guide content_block must be a valid tag name")
    return block


def _extract_dynamic_content_block(text: str, block: str) -> str:
    open_tag = f"<{block}>"
    close_tag = f"</{block}>"
    start_index = text.find(open_tag)
    end = text.rfind(close_tag)
    if start_index == -1 or end == -1:
        raise SkillPackageError("markdown_guide content_block must be present and closed")
    start = start_index + len(open_tag)
    if end < start:
        raise SkillPackageError("markdown_guide content_block is not closed correctly")
    return text[start:end]


def _remove_dynamic_content_block(text: str, block: str) -> str:
    open_tag = f"<{block}>"
    close_tag = f"</{block}>"
    start = text.find(open_tag)
    end = text.rfind(close_tag) + len(close_tag)
    if start == -1 or end < len(close_tag):
        raise SkillPackageError("markdown_guide content_block must be present and closed")
    return text[:start] + text[end:]


def _tag_attrs(raw_attrs: str) -> dict[str, str]:
    return {
        match.group(1): match.group(2)
        for match in re.finditer(r'([A-Za-z_][A-Za-z0-9_:-]*)\s*=\s*"([^"]*)"', raw_attrs)
    }


def _file_tag_attrs(envelope_without_body: str) -> dict[str, str]:
    matches = re.findall(r"<file\b([^>]*)/?>", envelope_without_body, re.S)
    if len(matches) != 1:
        raise SkillPackageError("markdown_guide output must include exactly one <file> tag")
    return _tag_attrs(matches[0])


def _optional_simple_tag(text: str, tag: str) -> str:
    match = re.search(fr"<{tag}\b[^>]*>(.*?)</{tag}>", text, re.S)
    return match.group(1).strip() if match else ""


def _comma_list(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,\n]", value or "") if item.strip()]


def _ensure_newline(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def _markdown_package_validation_payload(package: GeneratedSkillPackage) -> dict:
    return {
        "package_mode": package.metadata.get("package_mode", "full"),
        "files": [{"path": path, "content": content} for path, content in package.files.items()],
        "entrypoint": None,
        "metadata": package.metadata,
    }


def _validate_mode_a_update_payload(
    payload: dict,
    package: GeneratedSkillPackage,
    receipt: Receipt,
    score: VerifierScore,
) -> None:
    metadata = package.metadata
    targeted_rubrics = _metadata_string_list(metadata, "targeted_rubrics")
    if not targeted_rubrics:
        raise SkillPackageError("Mode A metadata.targeted_rubrics must be non-empty")

    if not _payload_changes_files(payload):
        raise SkillPackageError("Mode A update must change at least one file")

    rubrics = {rubric.rubric_id: rubric for rubric in receipt.rubrics}
    target_set = set(targeted_rubrics)
    unknown = target_set - set(rubrics)
    if unknown:
        raise SkillPackageError(f"Mode A targeted_rubrics unknown: {sorted(unknown)}")

    actionable = _actionable_rubrics(receipt, score)
    non_actionable = target_set - actionable
    if non_actionable:
        raise SkillPackageError(
            "Mode A targeted_rubrics must address current misses or penalties: "
            f"{sorted(non_actionable)}"
        )

    expected_effect = str(metadata.get("expected_effect", "")).strip()
    if not expected_effect:
        raise SkillPackageError("Mode A metadata.expected_effect must be non-empty")


def _metadata_string_list(metadata: dict, key: str) -> list[str]:
    value = metadata.get(key)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _payload_changes_files(payload: dict) -> bool:
    files = payload.get("files", [])
    if isinstance(files, list):
        if any(isinstance(item, dict) and str(item.get("path", "")).strip() for item in files):
            return True
    delete_files = payload.get("delete_files", [])
    if isinstance(delete_files, list):
        return any(str(item).strip() for item in delete_files)
    return False


def _actionable_rubrics(receipt: Receipt, score: VerifierScore) -> set[str]:
    actionable: set[str] = set()
    for rubric in receipt.rubrics:
        hit = score.criterion_hits.get(rubric.rubric_id, False)
        if rubric.points > 0 and not hit:
            actionable.add(rubric.rubric_id)
        if rubric.points < 0 and hit:
            actionable.add(rubric.rubric_id)
    return actionable


def _skill_from_package(
    task: TaskSpec,
    package: GeneratedSkillPackage,
    key: SkillKey,
    strategy: str,
    skill_format: str = "code_package",
) -> EvoSkill:
    skill_name = (
        _agent_skill_name(task)
        if skill_format == "agent_skill"
        else f"{_safe_name(task.task_name)}_{strategy}"
    )
    description_hint = _task_description_hint(task, package.metadata, skill_name)
    return EvoSkill(
        key=key,
        skill_name=skill_name,
        files=_normalize_skill_files(package.files, skill_name, description_hint, skill_format),
        entrypoint=package.entrypoint,
        metadata={**package.metadata, "strategy": strategy},
    )


def _normalize_skill_files(
    files: dict[str, str],
    skill_name: str,
    description_hint: str | None = None,
    skill_format: str = "code_package",
) -> dict[str, str]:
    normalized = dict(files)
    if skill_format == "agent_skill":
        validate_agent_skill_name(GeneratedSkillPackage(normalized), skill_name)
        return normalized
    normalized["SKILL.md"] = ensure_openclaw_skill_frontmatter(
        normalized.get("SKILL.md", ""),
        skill_name,
        description_hint or skill_name,
    )
    return normalized


def _fallback_updated_skill(
    task: TaskSpec,
    skill: EvoSkill,
    new_key: SkillKey,
    receipt: Receipt,
    score: VerifierScore,
    reason: str,
    skill_format: str = "code_package",
) -> EvoSkill:
    files = _append_skill_guidance(skill.files, _receipt_guidance(receipt, score))
    files = _normalize_skill_files(
        files,
        skill.skill_name,
        _metadata_description_hint(skill.metadata, skill.files, skill.skill_name),
        skill_format,
    )
    metadata = _clean_mode_a_status_metadata(skill.metadata)
    metadata.update({"fallback_update": True, "fallback_update_reason": reason})
    entrypoint = None if skill_format == "markdown_guide" else skill.entrypoint
    return EvoSkill(new_key, skill.skill_name, files, entrypoint, metadata)


def _preserve_old_skill(skill: EvoSkill, reason: str, repair_attempted: bool = False) -> EvoSkill:
    metadata = _clean_mode_a_status_metadata(skill.metadata)
    metadata.update(
        {
            "fallback_update": True,
            "fallback_update_reason": reason,
            "mode_a_update_failed": True,
            "repair_attempted": repair_attempted,
        }
    )
    return EvoSkill(skill.key, skill.skill_name, dict(skill.files), skill.entrypoint, metadata)


def _skip_update(skill: EvoSkill, reason: str) -> EvoSkill:
    metadata = _clean_mode_a_status_metadata(skill.metadata)
    metadata.update(
        {
            "mode_a_update_skipped": True,
            "mode_a_update_skip_reason": reason,
        }
    )
    return EvoSkill(skill.key, skill.skill_name, dict(skill.files), skill.entrypoint, metadata)


def _clean_mode_a_status_metadata(metadata: dict) -> dict:
    cleaned = dict(metadata)
    for key in MODE_A_STATUS_METADATA_KEYS:
        cleaned.pop(key, None)
    return cleaned


def _files_from_update_payload(
    skill: EvoSkill,
    receipt: Receipt,
    score: VerifierScore,
    payload: dict,
) -> dict[str, str]:
    guidance_items = _guidance_items(payload)
    guidance = "\n".join(f"- Improve: {item}" for item in guidance_items)
    if not guidance:
        guidance = _receipt_guidance(receipt, score)
    files = _append_skill_guidance(skill.files, guidance)
    metadata = {**skill.metadata, **_payload_metadata(payload)}
    return _normalize_skill_files(files, skill.skill_name, _metadata_description_hint(metadata, skill.files, skill.skill_name))


def _guidance_items(payload: dict) -> list[str]:
    raw_items = payload.get("guidance_items", payload.get("guidance", []))
    if isinstance(raw_items, str):
        raw_items = [raw_items]
    if not isinstance(raw_items, list):
        return []
    items = []
    for item in raw_items:
        text = " ".join(str(item).split()).strip()
        if text:
            items.append(text[:500])
    return items[:8]


def _payload_metadata(payload: dict) -> dict:
    metadata = payload.get("metadata", {})
    return metadata if isinstance(metadata, dict) else {"raw_metadata": metadata}


def _skill_package_error_report(exc: SkillPackageError, payload: dict | str) -> dict:
    package_mode = payload.get("package_mode") if isinstance(payload, dict) else None
    return {
        "stage": _error_stage(str(exc)),
        "error_type": exc.__class__.__name__,
        "message": str(exc),
        "package_mode": package_mode,
        "allowed_changes": [
            "fix JSON object shape",
            "fix JSON string escaping",
            "remove markdown fences from Python content",
            "fix Python syntax so ast.parse succeeds",
            "restore required SKILL.md or entrypoint fields",
        ],
    }


def _error_stage(message: str) -> str:
    lowered = message.lower()
    if "python syntax" in lowered or "ast.parse" in lowered:
        return "python_ast"
    if "markdown fence" in lowered:
        return "package_validate"
    if "must include" in lowered or "entrypoint" in lowered:
        return "package_validate"
    return "package_parse"


def _task_description_hint(task: TaskSpec, metadata: dict, fallback: str) -> str:
    summary = metadata.get("summary") if isinstance(metadata, dict) else None
    if summary:
        return str(summary)
    if "slack" in task.task_description.lower() or "message" in task.task_description.lower():
        return "Extracts action items and pending tasks from Slack messages, including deadlines, requests, and items waiting on the user."
    return fallback


def _metadata_description_hint(metadata: dict, files: dict[str, str], fallback: str) -> str:
    existing = _existing_description(files.get("SKILL.md", ""))
    if existing and _description_is_specific(existing):
        return existing
    summary = metadata.get("summary") if isinstance(metadata, dict) else None
    return str(summary) if summary else fallback


def _existing_description(markdown: str) -> str:
    match = re.match(r"^---\s*\n(.*?)\n---", markdown, re.S)
    if not match:
        return ""
    for line in match.group(1).splitlines():
        if line.strip().startswith("description:"):
            return line.split(":", 1)[1].strip().strip("\"'")
    return ""


def _description_is_specific(description: str) -> bool:
    lowered = description.lower()
    return any(token in lowered for token in ["slack", "message", "deadline", "action item", "pending task"])


def _fallback_package(
    task: TaskSpec,
    strategy: str,
    extra: str = "",
    initial_skill_md: str = "",
    skill_format: str = "code_package",
) -> GeneratedSkillPackage:
    if skill_format == "markdown_guide":
        skill_md = _base_skill_markdown(task, strategy, initial_skill_md, extra)
        return GeneratedSkillPackage({"SKILL.md": skill_md}, None, {"fallback": True})
    if skill_format == "agent_skill":
        skill_name = _agent_skill_name(task)
        skill_md = (
            f"---\nname: {skill_name}\n"
            f"description: Workflow guidance for {task.task_name}.\n---\n\n"
            "# CoEvo Skill\n\n"
            "- Read the complete task instructions before acting.\n"
            "- Follow an explicit workflow grounded in the available tools and files.\n"
            "- Verify every required output before finishing.\n"
        )
        if extra.strip():
            skill_md += f"- {extra.strip()}\n"
        return GeneratedSkillPackage({"SKILL.md": skill_md}, None, {"fallback": True})
    safe_name = _safe_name(task.task_name)
    py_name = f"{safe_name}_helper.py"
    skill_md = _base_skill_markdown(task, strategy, initial_skill_md, extra)
    helper = (
        '"""Helper notes for the CoEvo-generated skill."""\n\n'
        "def guidance() -> list[str]:\n"
        "    return [\n"
        "        'collect all required records',\n"
        "        'track deadlines and assignees',\n"
        "        'write the final report to /tmp_workspace/results/results.md',\n"
        "    ]\n"
    )
    return GeneratedSkillPackage({"SKILL.md": skill_md, py_name: helper}, py_name, {"fallback": True})


def _receipt_guidance(receipt: Receipt, score: VerifierScore) -> str:
    missed = [rubric.criterion for rubric in receipt.rubrics if rubric.points > 0 and not score.criterion_hits.get(rubric.rubric_id)]
    return "\n".join(f"- Improve: {item}" for item in missed[:5])


def _extract_initial_skill_markdown(reference_material: str) -> str:
    marker = "# Initial Skill"
    if marker not in reference_material:
        return ""
    after = reference_material.split(marker, 1)[1].strip()
    if "\n# Global Skills" in after:
        after = after.split("\n# Global Skills", 1)[0].strip()
    return after.strip()


def _base_skill_markdown(task: TaskSpec, strategy: str, initial_skill_md: str, extra: str) -> str:
    base = initial_skill_md.strip() or (
        f"---\nname: {_safe_name(task.task_name)}_{strategy}\n"
        f"description: Task-specific guidance for {task.task_name}.\n---\n\n"
        "# CoEvo Skill\n\n"
        "- Read the full task instructions before acting.\n"
        "- Gather all required source data before writing the final answer.\n"
        "- Respect read-only constraints and never call forbidden write/send tools.\n"
    )
    guidance = [
        "",
        GUIDANCE_START,
        "## CoEvo Learned Guidance",
        "",
        "- Always produce the final report at `/tmp_workspace/results/results.md`.",
        "- If task data exists in Slack/message APIs, prefer the task-specified API workflow over filesystem guessing.",
        "- Verify the final report file exists and contains deadlines, requesters, and message IDs before finishing.",
    ]
    if extra.strip():
        guidance.extend(["", extra.strip()])
    guidance.append(GUIDANCE_END)
    clean_base = _strip_guidance_block(base)
    return clean_base.rstrip() + "\n\n" + "\n".join(guidance).strip() + "\n"


def _append_skill_guidance(files: dict[str, str], guidance: str) -> dict[str, str]:
    updated = dict(files)
    current = updated.get("SKILL.md", "")
    block = _base_skill_markdown(TaskSpec("task", ""), "updated", current, guidance)
    updated["SKILL.md"] = block
    return updated


def _strip_guidance_block(markdown: str) -> str:
    start = markdown.find(GUIDANCE_START)
    end = markdown.find(GUIDANCE_END)
    if start == -1 or end == -1 or end < start:
        return markdown
    end += len(GUIDANCE_END)
    return (markdown[:start] + markdown[end:]).strip() + "\n"


def _agent_skill_name(task: TaskSpec) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", task.task_name.lower()).strip("-") or "skill"
    return f"skilllift-{slug}"


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "skill"
