"""Mode A update 失败容错测试。

验证契约:Mode A update package invalid(parse/repair/validation 失败)时,
保留旧 skill(version 不递增)、打失败标记、不 raise,继续处理同组其他 skill。
LLMOutputError(call_text 层,底层已重试)不在此捕获,继续向上 raise。
"""

from __future__ import annotations

import pytest

from skilllift.schemas import (  # 与 smoke 脚本同路径,保证 SkillPackageError 类身份一致
    EvoSkill,
    Receipt,
    RubricCriterion,
    SkillKey,
    VerifierScore,
)
from scripts import skilllift_tau2_real_llm_train_evolve_smoke as smoke
from scripts.skilllift_tau2_run_domain import (
    _aggregate_mode_a_outcomes,
    _mode_a_update_outcomes,
)

# 用 smoke 模块已 import 的异常类,避免 skilllift vs skilllift.skilllift 双路径下的类身份分歧
SkillPackageError = smoke.SkillPackageError
LLMOutputError = smoke.LLMOutputError


def _make_skill(slot: int = 0, version: int = 1) -> EvoSkill:
    return EvoSkill(
        key=SkillKey(slot, version),
        skill_name=f"test_skill_s{slot}",
        files={"SKILL.md": "# test\n"},
        entrypoint=None,
        metadata={},
    )


def _make_receipt_and_score(*, actionable: bool) -> tuple[Receipt, VerifierScore]:
    """actionable=True → r1 是未命中的正分 rubric(actionable);False → r1 已命中(non-actionable)。"""
    rubric = RubricCriterion(rubric_id="r1", category="correctness", criterion="must pass", points=2)
    receipt = Receipt(version=1, rubrics=[rubric], maximum_score=2, minimum_score=0)
    score = VerifierScore(
        skill=SkillKey(0, 1),
        receipt_version=1,
        criterion_hits={"r1": not actionable},  # hit=False → actionable
        raw_score=0 if actionable else 2,
        normalized_score=0.0 if actionable else 1.0,
    )
    return receipt, score


def _build_args(receipt, score, monkeypatch, *, llm_stub) -> dict:
    """构造 _update_markdown_skill_group 的 kwargs,llm 调用被 monkeypatch 替换。"""
    monkeypatch.setattr(smoke, "_call_and_parse_markdown_package", llm_stub)
    # build_sg_update_prompt 在 LLM 调用之前被调用且需要真 task_spec;单元测试里 stub 掉它
    # (返回的 system/user 字符串在 llm_stub 路径下不会被实际使用)。
    monkeypatch.setattr(smoke, "build_sg_update_prompt", lambda *a, **k: ("sys", "usr"))
    return dict(
        task_spec=None,
        skills={SkillKey(0, 1): _make_skill()},
        receipt=receipt,
        scores={SkillKey(0, 1): score},
        llm_client=object(),
        d12_metrics=smoke.D12RepairMetrics(),
        source_model="test-model",
    )


# --- 契约 1: SkillPackageError → preserve 旧 skill, version 不变, 不 raise ---


def test_mode_a_skill_package_error_preserves_old_skill(monkeypatch) -> None:
    def raise_spe(*args, **kwargs):
        raise SkillPackageError("simulated invalid package")

    receipt, score = _make_receipt_and_score(actionable=True)
    args = _build_args(receipt, score, monkeypatch, llm_stub=raise_spe)

    updated = smoke._update_markdown_skill_group(**args)

    # 旧 key 保留(version 未递增到 2),没有新 key
    assert SkillKey(0, 1) in updated
    assert SkillKey(0, 2) not in updated
    preserved = updated[SkillKey(0, 1)]
    assert preserved.metadata.get("mode_a_update_failed") is True
    assert preserved.metadata.get("repair_attempted") is True
    assert "simulated invalid package" in preserved.metadata.get("mode_a_update_skip_reason", "")
    # d12_metrics 记录了 preserve 事件
    assert any(a.get("outcome") == "failed_after_repair_preserved" for a in args["d12_metrics"].attempts)


# --- 契约 2: 非 actionable → skip 标记 ---


def test_mode_a_no_actionable_rubrics_skipped(monkeypatch) -> None:
    def should_not_be_called(*args, **kwargs):  # noqa: ANN002
        raise AssertionError("LLM should not be called when no actionable rubrics")

    receipt, score = _make_receipt_and_score(actionable=False)
    args = _build_args(receipt, score, monkeypatch, llm_stub=should_not_be_called)

    updated = smoke._update_markdown_skill_group(**args)

    preserved = updated[SkillKey(0, 1)]
    assert preserved.metadata.get("mode_a_update_skipped") is True
    assert preserved.metadata.get("mode_a_update_skip_reason") == "no_actionable_rubrics"
    # 未调 LLM
    assert args["d12_metrics"].total_generation_attempts == 0


# --- 契约 3: LLMOutputError 仍 raise(不在 catch 范围) ---


def test_mode_a_llm_output_error_still_raises(monkeypatch) -> None:
    def raise_llm_err(*args, **kwargs):  # noqa: ANN002
        raise LLMOutputError("simulated call_text failure")

    receipt, score = _make_receipt_and_score(actionable=True)
    args = _build_args(receipt, score, monkeypatch, llm_stub=raise_llm_err)

    with pytest.raises(LLMOutputError, match="simulated call_text failure"):
        smoke._update_markdown_skill_group(**args)


# --- 契约 4: 单个候选失败不阻断同组其他候选 ---


def test_mode_a_one_failure_does_not_block_others(monkeypatch) -> None:
    call_count = {"n": 0}

    def fail_first_then_succeed(*args, **kwargs):  # noqa: ANN002
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise SkillPackageError("first candidate invalid")
        # 第二个候选返回合法 package(用最简构造,不走 _skill_from_package 的完整路径会被测到)
        raise SkillPackageError("second also invalid")  # 让两个都 preserve,简化断言

    receipt, score = _make_receipt_and_score(actionable=True)
    skill_a = _make_skill(slot=0, version=1)
    skill_b = _make_skill(slot=1, version=1)
    monkeypatch.setattr(smoke, "_call_and_parse_markdown_package", fail_first_then_succeed)
    monkeypatch.setattr(smoke, "build_sg_update_prompt", lambda *a, **k: ("sys", "usr"))

    updated = smoke._update_markdown_skill_group(
        task_spec=None,
        skills={SkillKey(0, 1): skill_a, SkillKey(1, 1): skill_b},
        receipt=receipt,
        scores={SkillKey(0, 1): score, SkillKey(1, 1): score},
        llm_client=object(),
        d12_metrics=smoke.D12RepairMetrics(),
        source_model="test-model",
    )

    # 两个候选都被 preserve(没有因为第一个失败而中断)
    assert updated[SkillKey(0, 1)].metadata.get("mode_a_update_failed") is True
    assert updated[SkillKey(1, 1)].metadata.get("mode_a_update_failed") is True
    assert call_count["n"] == 2


# --- 契约 5: _mode_a_update_outcomes 纯函数 ---


def test_mode_a_update_outcomes_classification() -> None:
    skills = {
        SkillKey(0, 1): EvoSkill(SkillKey(0, 1), "a", {}, None, {}),
        SkillKey(1, 1): EvoSkill(SkillKey(1, 1), "b", {}, None, {"mode_a_update_skipped": True}),
        SkillKey(2, 1): EvoSkill(SkillKey(2, 1), "c", {}, None, {"mode_a_update_failed": True}),
    }
    out = _mode_a_update_outcomes(skills)
    assert out["s000_v001"]["outcome"] == "active"
    assert out["s001_v001"]["outcome"] == "no_actionable_skipped"
    assert out["s002_v001"]["outcome"] == "mode_a_failed_preserved"


# --- 契约 6: _aggregate_mode_a_outcomes 纯函数 + 向后兼容(旧数据无字段) ---


def test_aggregate_mode_a_outcomes_and_backward_compat() -> None:
    round_metrics = [
        {"mode_a_update_outcomes": {"k1": {"outcome": "active"}, "k2": {"outcome": "active"}}},
        {
            "mode_a_update_outcomes": {
                "k1": {"outcome": "no_actionable_skipped"},
                "k2": {"outcome": "mode_a_failed_preserved"},
            }
        },
        {"some_old_field": "no mode_a_update_outcomes here"},  # 旧数据兼容
    ]
    agg = _aggregate_mode_a_outcomes(round_metrics)
    assert agg["counts"] == {"active": 2, "no_actionable_skipped": 1, "mode_a_failed_preserved": 1}
    assert agg["inert_total"] == 2
    assert agg["inert_rate"] == 0.5  # 2 inert / 4 total
