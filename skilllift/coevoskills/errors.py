"""CoEvoSkills-specific errors."""


class CoEvoSkillsError(Exception):
    """Base class for baseline failures."""


class SkillPackageError(CoEvoSkillsError):
    """Raised when a generated multi-file skill is invalid."""


class SurrogateSuiteError(CoEvoSkillsError):
    """Raised when a generated surrogate suite is invalid or cannot run."""


class LLMResponseError(CoEvoSkillsError):
    """Raised when an LLM response does not satisfy the required schema."""


class PersistenceError(CoEvoSkillsError):
    """Raised when an artifact path is unsafe or cannot be persisted."""
