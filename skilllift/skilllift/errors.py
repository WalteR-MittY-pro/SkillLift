"""Shared exception types for Rubric-CoEvoSkills v2."""


class SkillLiftError(Exception):
    """Base class for all v2 CoEvo errors."""


class SchemaError(SkillLiftError):
    """Raised when structured data cannot be decoded or validated."""


class ReceiptValidationError(SkillLiftError):
    """Raised when a receipt is invalid."""


class VerifierError(SkillLiftError):
    """Raised when verifier output is malformed."""


class RankError(SkillLiftError):
    """Raised when rank inputs are incompatible."""


class SkillPackageError(SkillLiftError):
    """Raised when a generated skill package is invalid."""


class LLMOutputError(SkillLiftError):
    """Raised when an LLM response cannot be parsed into the required shape."""


class OracleAdapterError(SkillLiftError):
    """Raised for oracle adapter preflight or execution errors."""


class PersistenceError(SkillLiftError):
    """Raised when experiment artifacts cannot be persisted."""
