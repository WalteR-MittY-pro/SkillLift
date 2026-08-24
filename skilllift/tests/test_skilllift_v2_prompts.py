from skilllift.baselines.prompts import (
    build_rubricator_init_prompt,
    build_rubricator_revision_prompt,
    build_sg_repair_prompt,
    build_sg_seed_prompt,
    build_sg_update_prompt,
    build_sg_variant_prompt,
    build_verifier_prompt,
)
from skilllift.schemas import (
    EvoSkill,
    OracleFeedback,
    OracleScore,
    Receipt,
    ReceiptRevisionInput,
    RubricCriterion,
    SkillKey,
    TaskRanking,
    TaskSpec,
    VerifierScore,
)


def _fixtures():
    task = TaskSpec("task", "Do the task")
    key_a = SkillKey(0, 1)
    key_b = SkillKey(1, 1)
    skill = EvoSkill(key_a, "demo", {"SKILL.md": "x\n", "a.py": "print(1)\n"}, "a.py")
    receipt = Receipt(1, [RubricCriterion("r1", "Planning", "Has plan", 1)], 1, 0)
    score = VerifierScore(key_a, 1, {"r1": True}, 1, 1.0)
    revision = ReceiptRevisionInput(
        task,
        receipt,
        {key_a: skill},
        {key_a: score, key_b: VerifierScore(key_b, 1, {"r1": False}, 0, 0.0)},
        {key_a: OracleScore(key_a, 0.1, 0), key_b: OracleScore(key_b, 0.9, 1)},
        [key_a, key_b],
        [key_b, key_a],
        -1.0,
    )
    return task, skill, receipt, score, revision


def _markdown_skill() -> EvoSkill:
    return EvoSkill(
        SkillKey(0, 1),
        "guide",
        {"SKILL.md": "# Guide\n\nCheck observable preconditions.\n"},
        None,
    )


def test_prompt_builders_return_system_and_user_text() -> None:
    task, skill, receipt, score, revision = _fixtures()
    markdown_skill = _markdown_skill()
    builders = [
        ("rubricator_init", build_rubricator_init_prompt(task, "reference")),
        ("rubricator_revision", build_rubricator_revision_prompt(revision, {"skills": []})),
        ("verifier_code_package", build_verifier_prompt(task, skill, receipt)),
        (
            "verifier_markdown_guide",
            build_verifier_prompt(task, markdown_skill, receipt, skill_format="markdown_guide"),
        ),
        ("sg_seed_code_package", build_sg_seed_prompt(task, "reference")),
        ("sg_seed_markdown_guide", build_sg_seed_prompt(task, "reference", skill_format="markdown_guide")),
        ("sg_variant_code_package", build_sg_variant_prompt(task, skill, 1)),
        (
            "sg_variant_markdown_guide",
            build_sg_variant_prompt(task, markdown_skill, 1, skill_format="markdown_guide"),
        ),
        ("sg_update_code_package", build_sg_update_prompt(task, skill, receipt, score)),
        (
            "sg_update_markdown_guide",
            build_sg_update_prompt(task, markdown_skill, receipt, score, skill_format="markdown_guide"),
        ),
        ("sg_repair_code_package", build_sg_repair_prompt(task, "seed", {"bad": True}, {"message": "bad"})),
        (
            "sg_repair_markdown_guide",
            build_sg_repair_prompt(
                task,
                "seed",
                "<package></package>",
                {"message": "bad"},
                skill_format="markdown_guide",
            ),
        ),
    ]
    for name, (system, user) in builders:
        assert system, f"empty system for {name}"
        assert user, f"empty user for {name}"


def test_rubricator_init_prompt_defaults_to_strict_discriminative_receipts() -> None:
    task, _, _, _, _ = _fixtures()
    _, user = build_rubricator_init_prompt(task, "reference")

    assert "strict, discriminative criteria" in user
    assert "retrieval-only" in user
    assert "state-changing intents" in user
    assert "mid-conversation intent changes" in user
    assert "Missing required behavior must be a missed positive criterion" in user
    assert "Do not give credit for merely naming an action" in user


def test_rubricator_init_prompt_does_not_repeat_task_in_reference_material() -> None:
    task = TaskSpec("task", "Unique public task body")
    reference = "# Task Description\nUnique public task body\n\n# Initial Skill\nseed guidance"

    _, user = build_rubricator_init_prompt(task, reference)

    assert user.count("Unique public task body") == 1
    assert "seed guidance" in user


def test_revision_prompt_contains_rank_mismatch() -> None:
    _, _, _, _, revision = _fixtures()
    _, user = build_rubricator_revision_prompt(revision, {})
    assert "rank_mismatches" in user
    assert '"delta"' in user
    assert "oracle_scores separate skills" in user
    assert "current receipt is too weak" in user
    assert "Return a complete receipt, not a patch" in user
    assert "adding or strengthening binary criteria" in user
    assert "removed_rubrics" in user
    assert "Do not silently remove any criterion" in user
    assert "a concrete reason, and visible evidence_refs" in user
    assert "Do not invent policy requirements, hidden oracle rules, private labels, expected actions, missing actions, or unobserved execution details" in user
    assert "Do not overfit to exact tool names" in user


def test_revision_prompt_requires_oracle_contrast_diagnosis() -> None:
    _, _, _, _, revision = _fixtures()
    _, user = build_rubricator_revision_prompt(revision, {})

    assert "[Oracle Contrast Diagnosis]" in user
    assert "receipt.metadata.oracle_contrast_diagnosis" in user
    assert "evidence-bounded hypothesis" in user
    assert "no_visible_explanation" in user
    assert "oracle_contrast.oracle_rank_groups" in user
    assert "oracle_contrast.tiers" in user
    assert "same positive-score rank group can support shared evidence" in user
    assert "Do not compare skills inside the same zero-score rank group" in user
    assert "oracle_contrast.oracle_has_signal is false" in user
    assert "oracle_contrast.tiers.top" in user
    assert "skill package alone" in user


def test_revision_prompt_uses_sanitized_oracle_payload_only() -> None:
    task, skill, receipt, _, revision = _fixtures()
    key = SkillKey(0, 1)
    unsafe_feedback = OracleFeedback(
        summary="SECRET_SUMMARY",
        stderr_excerpt="SECRET_STDERR",
        traceback_summary="SECRET_TRACEBACK",
        metadata={
            "chat_summary": "SECRET_CHAT",
            "skill_observation": {
                "skill_key": "s000_v001",
                "execution_summary": {"status": "task_low_score"},
            },
        },
    )
    unsafe_oracle = {
        key: OracleScore(
            key,
            0.1,
            0,
            feedback=unsafe_feedback,
            output_dir="/private/output/SECRET_PATH",
        )
    }
    revision = ReceiptRevisionInput(
        task,
        receipt,
        {key: skill},
        revision.verifier_scores,
        unsafe_oracle,
        [key],
        [key],
        0.0,
        oracle_contrast={
            "oracle_contrast": {
                "high_scoring_skills": [],
                "low_scoring_skills": [],
                "tie_groups": [["s000_v001"]],
            },
        },
    )

    _, user = build_rubricator_revision_prompt(revision, {})

    for forbidden in [
        "SECRET_SUMMARY",
        "SECRET_STDERR",
        "SECRET_TRACEBACK",
        "SECRET_CHAT",
        "SECRET_PATH",
        "output_dir",
        "stderr_excerpt",
        "traceback_summary",
        "chat_summary",
    ]:
        assert forbidden not in user


def test_revision_prompt_without_per_task_rankings_keeps_legacy_shape() -> None:
    _, _, _, _, revision = _fixtures()
    assert revision.per_task_rankings is None

    _, user = build_rubricator_revision_prompt(revision, {})

    assert "per_task_rankings" not in user
    assert "[Per-Task Ranking Evidence]" not in user
    assert "aggregate view" not in user


def test_revision_prompt_includes_per_task_ranking_evidence() -> None:
    task, skill, receipt, _, revision = _fixtures()
    key_a = SkillKey(0, 1)
    key_b = SkillKey(1, 1)
    rankings = []
    for index in range(8):
        rankings.append(
            TaskRanking(
                task_id=f"task-{index:03d}",
                domain="demo",
                oracle_rank=[key_b, key_a],
                skill_scores={
                    key_a: OracleScore(
                        key_a,
                        0.25 + index / 100,
                        0,
                        feedback=OracleFeedback(summary=f"skill a task {index}"),
                    ),
                    key_b: OracleScore(
                        key_b,
                        0.75 + index / 100,
                        1,
                        feedback=OracleFeedback(summary=f"skill b task {index}"),
                    ),
                },
                has_signal=True,
            )
        )
    rankings[3] = TaskRanking(
        task_id="task-003",
        domain="demo",
        oracle_rank=[],
        skill_scores={
            key_a: OracleScore(key_a, 1.0, 1, feedback=OracleFeedback(summary="tie")),
            key_b: OracleScore(key_b, 1.0, 1, feedback=OracleFeedback(summary="tie")),
        },
        has_signal=False,
        no_signal_reason="all skills tied on oracle_score/oracle_pass",
    )
    revision = ReceiptRevisionInput(
        task,
        receipt,
        {key_a: skill},
        revision.verifier_scores,
        revision.oracle_scores,
        revision.verifier_rank,
        revision.oracle_rank,
        revision.rank_alignment,
        per_task_rankings=rankings,
    )

    _, user = build_rubricator_revision_prompt(revision, {})

    assert "[Per-Task Ranking Evidence]" in user
    assert '"per_task_rankings"' in user
    assert '"task_index": 1' in user
    assert '"task_id": "task-000"' not in user
    assert "skill a task 0" not in user
    assert "skill b task 0" not in user
    assert '"domain": "demo"' in user
    assert '"oracle_rank"' in user
    assert '"s001_v001"' in user
    assert '"s000_v001"' in user
    assert '"has_signal": false' in user
    assert "all skills tied on oracle_score/oracle_pass" in user
    assert "`oracle_rank` is aggregate; `per_task_rankings` is granular" in user
    assert "messages" not in user
    assert "transcript" not in user
    assert "evaluation_criteria" not in user
    assert "gold_action" not in user
    assert "target_db" not in user


def test_verifier_prompt_contains_rubrics() -> None:
    task, skill, receipt, _, _ = _fixtures()
    _, user = build_verifier_prompt(task, skill, receipt)
    assert "r1" in user
    assert "criterion_hits" in user
    assert "observable evidence" in user
    assert "same standard" in user
    assert "comparable for ranking" in user
    assert "hidden outputs" in user
    assert '"raw_score"' not in user
    assert "computed locally from criterion_hits" in user


def test_verifier_prompt_adds_mode_a_rules() -> None:
    task, skill, receipt, _, _ = _fixtures()
    _, user = build_verifier_prompt(task, skill, receipt, mode="mode_a")
    assert "fixed receipt" in user
    assert "actionable missed positive criteria" in user
    assert "explicit negative flaws" in user
    assert "oracle rank" not in user


def test_verifier_prompt_adds_mode_b_rules() -> None:
    task, skill, receipt, _, _ = _fixtures()
    _, user = build_verifier_prompt(task, skill, receipt, mode="mode_b")
    assert "local rank comparison" in user
    assert "Do not use oracle results" in user
    assert "Automated Checks" in user
    assert "metric weights" in user


def test_prompts_do_not_use_hidden_answer_field_name() -> None:
    task, skill, receipt, score, revision = _fixtures()
    texts = [
        *build_rubricator_init_prompt(task, "reference"),
        *build_rubricator_revision_prompt(revision, {}),
        *build_verifier_prompt(task, skill, receipt),
        *build_sg_update_prompt(task, skill, receipt, score),
    ]
    assert "hidden_answer" not in "\n".join(texts)


def test_code_package_sg_prompts_use_neutral_agent_skill_description() -> None:
    task, skill, receipt, score, _ = _fixtures()
    texts = [
        *build_sg_seed_prompt(task, "reference"),
        *build_sg_variant_prompt(task, skill, 1),
        *build_sg_update_prompt(task, skill, receipt, score),
    ]
    joined = "\n".join(texts)
    assert "description" in joined
    assert "agent skill" in joined
    assert "OpenClaw" not in joined
    assert "trigger" in joined


def test_markdown_guide_seed_prompt_uses_xml_contract() -> None:
    task, _, _, _, _ = _fixtures()
    system, user = build_sg_seed_prompt(task, "reference", skill_format="markdown_guide")

    assert "JSON" not in system
    assert '<package mode="full" strategy="seed">' in user
    assert '<file path="SKILL.md" content_block="' in user
    assert "content_block" in user
    assert "SKILL.md" in user
    assert "executor.py" not in user
    assert "ast.parse" not in user


def test_agent_facing_workflow_discipline_in_seed_and_variant_prompts() -> None:
    task, skill, _, _, _ = _fixtures()
    markdown_skill = _markdown_skill()
    prompts = [
        build_sg_seed_prompt(task, "reference"),
        build_sg_seed_prompt(task, "reference", skill_format="markdown_guide"),
        build_sg_variant_prompt(task, skill, 1),
        build_sg_variant_prompt(task, markdown_skill, 1, skill_format="markdown_guide"),
    ]

    for _, user in prompts:
        assert "[Agent-Facing Workflow Discipline]" in user
        assert "operational workflow" in user
        assert "preconditions" in user
        assert "post-action verification" in user
        assert "capabilities, APIs, tools" in user
        assert "data fields, policies, labels, expected outputs" in user


def test_variant_prompt_anti_restate_and_agent_facing() -> None:
    """variant prompt 不能含换皮暗示（Rewrite / based on the seed），
    必须含 agent-facing 差异化要求。"""
    task, skill, _, _, _ = _fixtures()
    markdown_skill = _markdown_skill()
    for _, user in [
        build_sg_variant_prompt(task, skill, 1),
        build_sg_variant_prompt(task, markdown_skill, 1, skill_format="markdown_guide"),
    ]:
        # 反换皮：禁用语
        assert "Rewrite the seed" not in user
        assert "based on the seed" not in user
        # 区块标题明示 seed 是参考、禁止复述
        assert "[Seed Skill (reference; do not restate)]" in user
        # 正向要求：agent-facing 差异化 + 反 cosmetic
        assert "agent-facing workflow differences" in user
        assert "do not merely rephrase" in user
        assert "cosmetic wording" in user


def test_variant_prompt_injects_slot_specific_strategy() -> None:
    task, skill, _, _, _ = _fixtures()

    _, slot_1_user = build_sg_variant_prompt(task, skill, 1)
    _, slot_2_user = build_sg_variant_prompt(task, skill, 2)
    _, slot_4_user = build_sg_variant_prompt(task, skill, 4)
    _, slot_5_user = build_sg_variant_prompt(task, skill, 5)

    assert "[Variant Strategy]" in slot_1_user
    assert '"axis": "precondition_policy_boundary"' in slot_1_user
    assert '"axis": "evidence_reconciliation"' in slot_2_user
    assert '"axis": "post_action_verification"' in slot_4_user
    assert '"axis": "precondition_policy_boundary"' in slot_5_user
    assert '"stress": "ambiguous_or_changed_user_intent"' in slot_1_user
    assert '"stress": "insufficient_or_conflicting_evidence"' in slot_5_user
    assert "Follow the Variant Strategy" in slot_1_user
    assert "materially different in agent-facing workflow" in slot_1_user


def test_markdown_guide_update_prompt_includes_patch_xml_contract() -> None:
    task, _, receipt, score, _ = _fixtures()
    skill = _markdown_skill()
    _, user = build_sg_update_prompt(task, skill, receipt, score, skill_format="markdown_guide")

    assert '<package mode="patch" strategy="mode_a_update">' in user
    assert "<targeted_rubrics>" in user
    assert "<expected_effect>" in user
    assert '<file path="SKILL.md" content_block="' in user
    assert "delete_files" not in user


def test_agent_facing_workflow_repair_in_update_prompts() -> None:
    task, skill, receipt, score, _ = _fixtures()
    markdown_skill = _markdown_skill()
    prompts = [
        build_sg_update_prompt(task, skill, receipt, score),
        build_sg_update_prompt(task, markdown_skill, receipt, score, skill_format="markdown_guide"),
    ]

    for _, user in prompts:
        assert "[Agent-Facing Workflow Repair]" in user
        assert "agent-facing workflow" in user
        assert "post-action verification" in user
        assert "benchmark-agnostic" in user


def test_markdown_guide_sg_prompts_avoid_benchmark_and_code_package_terms() -> None:
    task, _, receipt, score, _ = _fixtures()
    skill = _markdown_skill()
    texts = [
        *build_sg_seed_prompt(task, "reference", skill_format="markdown_guide"),
        *build_sg_variant_prompt(task, skill, 1, skill_format="markdown_guide"),
        *build_sg_update_prompt(task, skill, receipt, score, skill_format="markdown_guide"),
        *build_sg_repair_prompt(
            task,
            "mode_a_update",
            "<package></package>",
            {"message": "bad"},
            skill_format="markdown_guide",
        ),
    ]
    joined = "\n".join(texts)

    for forbidden in ["tau2", "OpenClaw", "wildclaw", "客服", "订单", "账号", "数据库"]:
        assert forbidden not in joined
    for code_only in ["executor.py", "ast.parse", "tmp_workspace", "entrypoint", "Python files"]:
        assert code_only not in joined


def test_markdown_guide_verifier_prompt_avoids_code_only_rules() -> None:
    task, _, receipt, _, _ = _fixtures()
    _, user = build_verifier_prompt(task, _markdown_skill(), receipt, skill_format="markdown_guide")

    assert "SKILL.md instructions" in user
    assert "agent-facing workflow" in user
    for code_only in ["entrypoint source code", "Python source code", "ast.parse", ".py"]:
        assert code_only not in user


def test_code_package_seed_prompt_keeps_json_output_contract() -> None:
    task, _, _, _, _ = _fixtures()
    _, user = build_sg_seed_prompt(task, "reference")

    assert '"entrypoint": "executor.py"' in user
    assert "ast.parse" in user
    assert "<package mode=" not in user
    assert "Return exactly one JSON object" in user or "Every file content must be a JSON string" in user


def test_mode_a_prompt_requires_package_not_guidance() -> None:
    task, skill, receipt, score, _ = _fixtures()
    _, user = build_sg_update_prompt(task, skill, receipt, score)
    assert '"package_mode": "patch"' in user
    assert '"package_mode": "full"' in user
    assert "Do not return guidance_items" in user
    assert "ast.parse" in user
    assert "[Acceptance Target]" in user
    assert "accepted only if this slot's normalized_score does not decrease" in user
    assert "[Target Selection]" in user


def test_mode_a_repair_prompt_allows_metadata_repair_without_new_task_logic() -> None:
    task, _, _, _, _ = _fixtures()

    system, user = build_sg_repair_prompt(
        task,
        "mode_a_update",
        {"package_mode": "patch", "files": []},
        {"allowed_target_rubrics": [{"rubric_id": "r1"}]},
    )

    assert "failed skill-update package" in system
    assert "When [Strategy] is mode_a_update" in user
    assert "metadata.targeted_rubrics" in user
    assert "metadata.expected_effect" in user
    assert "metadata.changed_files" in user
    assert "Do not introduce new task logic" in user


def test_mode_a_prompt_lists_exact_actionable_rubric_ids() -> None:
    task, skill, _, _, _ = _fixtures()
    receipt = Receipt(
        1,
        [
            RubricCriterion("r6", "figure_counting", "Accurately counts figures", 3),
            RubricCriterion("r13", "output_noise", "Creates extra output files", -2),
        ],
        3,
        -2,
    )
    score = VerifierScore(skill.key, 1, {"r6": False, "r13": True}, -2, 0.0)

    _, user = build_sg_update_prompt(task, skill, receipt, score)

    assert "[Current Actionable Target Rubrics]" in user
    assert '"rubric_id": "r6"' in user
    assert '"rubric_id": "r13"' in user
    assert "Do not use category names, criterion labels, oracle metric names" in user
    assert "metadata.targeted_rubrics must contain exact rubric_id strings" in user


def test_mode_a_repair_prompt_preserves_targeting_metadata() -> None:
    task, _, _, _, _ = _fixtures()
    _, user = build_sg_repair_prompt(
        task,
        "mode_a_update",
        {"package_mode": "patch", "metadata": {"targeted_rubrics": ["r1"]}},
        {"message": "Mode A metadata.expected_effect must be non-empty"},
    )
    assert "metadata.targeted_rubrics" in user
    assert "metadata.expected_effect" in user


def test_agent_skill_prompts_use_multifile_contract_and_stable_runtime_name() -> None:
    task, skill, receipt, score, _ = _fixtures()
    seed = build_sg_seed_prompt(task, "reference", skill_format="agent_skill")
    variant = build_sg_variant_prompt(task, skill, 1, skill_format="agent_skill")
    update = build_sg_update_prompt(task, skill, receipt, score, skill_format="agent_skill")
    joined = "\n".join([*seed, *variant, *update])

    assert "agent_skill" in joined
    assert "at most 12 text files" in joined
    assert "40,000 characters" in joined
    assert "file_manifest" in joined
    assert "at least one Python file" not in joined
    assert "entrypoint must name an existing .py file" not in joined


def test_agent_skill_verifier_uses_manifest_payload_without_code_only_assumptions() -> None:
    task, skill, receipt, _, _ = _fixtures()
    _, user = build_verifier_prompt(task, skill, receipt, skill_format="agent_skill")

    assert "file_manifest" in user
    assert "aux_files" in user
    assert "entrypoint source code" not in user
    assert "Large hardcoded multi-line strings" not in user
