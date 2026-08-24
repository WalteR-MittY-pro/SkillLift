from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CoEvoSkillsConfig:
    max_oracle_interventions: int = 5
    max_surrogate_retries: int = 15
    min_turns: int = 1
    max_turns: int = 20
    token_budget: int = 0
    context_cap_ratio: float = 0.7
    max_context_chars: int = 200_000
    oracle_threshold: float = 1.0
    surrogate_timeout_seconds: int = 30
    surrogate_docker_image: str | None = None
    evaluation_repeats: int = 5
    initial_suite_policy: str = "generate_before_first_score_v1"
    surrogate_counter_policy: str = "surrogate_failure_repairs_v1"

    def __post_init__(self) -> None:
        if self.max_oracle_interventions < 1:
            raise ValueError("max_oracle_interventions must be >= 1")
        if self.max_surrogate_retries < 0:
            raise ValueError("max_surrogate_retries must be >= 0")
        if self.min_turns < 1:
            raise ValueError("min_turns must be >= 1")
        if self.max_turns < self.min_turns:
            raise ValueError("max_turns must be >= min_turns")
        if self.token_budget < 0:
            raise ValueError("token_budget must be >= 0")
        if not 0 < self.context_cap_ratio <= 1:
            raise ValueError("context_cap_ratio must be in (0, 1]")
        if self.max_context_chars < 1:
            raise ValueError("max_context_chars must be >= 1")
        if not 0 <= self.oracle_threshold <= 1:
            raise ValueError("oracle_threshold must be in [0, 1]")
        if self.surrogate_timeout_seconds < 1:
            raise ValueError("surrogate_timeout_seconds must be >= 1")
        if self.evaluation_repeats < 1:
            raise ValueError("evaluation_repeats must be >= 1")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
