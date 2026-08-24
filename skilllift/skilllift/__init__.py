"""SkillLift core package: portfolio coordinator, Mode A/B, rubric, verifier, adapters."""


def __getattr__(name: str):
    if name == "SkillLiftPortfolioCoordinator":
        from .coordinator import SkillLiftPortfolioCoordinator

        return SkillLiftPortfolioCoordinator
    if name == "TaskPortfolioCoordinator":
        from .coordinator import TaskPortfolioCoordinator

        return TaskPortfolioCoordinator
    if name == "SKILLLIFT_COORDINATOR_VERSION":
        from .coordinator import SKILLLIFT_COORDINATOR_VERSION

        return SKILLLIFT_COORDINATOR_VERSION
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "SkillLiftPortfolioCoordinator",
    "TaskPortfolioCoordinator",
    "SKILLLIFT_COORDINATOR_VERSION",
    "adapters",
    "baselines",
    "coordinator",
    "errors",
    "experiment_trace",
    "llm_client",
    "model_config",
    "normalization",
    "oracle",
    "oracle_contrast",
    "persistence",
    "portfolio",
    "ranking",
    "report",
    "rubricator",
    "schemas",
    "task_loader",
    "verifier",
]
