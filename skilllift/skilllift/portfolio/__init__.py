"""Public surface of skilllift.portfolio."""

from .ref import (
    PortfolioError,
    PortfolioRef,
    PortfolioPatchResult,
    portfolio_tree_hash,
    validate_portfolio,
    extract_unified_diff,
    apply_portfolio_patch,
    portfolio_manifest,
    portfolio_skill_names,
)

from .store import (
    STORE_SCHEMA_VERSION,
    CandidateRecord,
    PortfolioStore,
)

from .prompts import (
    PortfolioPromptError,
    PortfolioContextLimitError,
    PublicTask,
    RubricHypothesis,
    SearchDirection,
    SearchPlan,
    ScalarOutcome,
    PromptBundle,
    LLMRubricatorPlanner,
    LLMPatchSkillGenerator,
    parse_search_plan_text,
    parse_search_plan,
    serialize_search_history,
    build_rubricator_prompt,
    build_skill_generator_prompt,
)

__all__ = [
    "CandidateRecord",
    "LLMPatchSkillGenerator",
    "LLMRubricatorPlanner",
    "PortfolioContextLimitError",
    "PortfolioError",
    "PortfolioPatchResult",
    "PortfolioPromptError",
    "PortfolioRef",
    "PortfolioStore",
    "PromptBundle",
    "PublicTask",
    "RubricHypothesis",
    "STORE_SCHEMA_VERSION",
    "ScalarOutcome",
    "SearchDirection",
    "SearchPlan",
    "apply_portfolio_patch",
    "build_rubricator_prompt",
    "build_skill_generator_prompt",
    "extract_unified_diff",
    "parse_search_plan",
    "parse_search_plan_text",
    "portfolio_manifest",
    "portfolio_skill_names",
    "portfolio_tree_hash",
    "serialize_search_history",
    "validate_portfolio",
]
