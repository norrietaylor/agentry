"""Exceptions for the output validation pipeline."""

from __future__ import annotations


class ValidationError(Exception):
    """Base class for output validation errors."""


class SchemaValidationError(ValidationError):
    """Raised when agent output fails JSON Schema validation (Layer 1).

    Attributes:
        schema_path: The JSON Schema path where validation failed (e.g., "$.findings").
        failed_keyword: The JSON Schema keyword that triggered the failure
            (e.g., "type", "required", "minimum").
        message: Human-readable description of the failure.
    """

    def __init__(
        self,
        schema_path: str,
        failed_keyword: str,
        message: str,
    ) -> None:
        self.schema_path = schema_path
        self.failed_keyword = failed_keyword
        self.message = message
        super().__init__(
            f"Schema validation failed at {schema_path!r}: "
            f"keyword={failed_keyword!r} — {message}"
        )


class UndeclaredSideEffectError(ValidationError):
    """Raised when an agent attempts a side effect not in the allowlist (Layer 2).

    Attributes:
        side_effect: The side-effect identifier that was attempted
            (e.g., "file:write", "pr:comment").
        allowlist: The declared side-effect allowlist from the workflow definition.
    """

    def __init__(self, side_effect: str, allowlist: list[str]) -> None:
        self.side_effect = side_effect
        self.allowlist = allowlist
        allowed = ", ".join(repr(s) for s in allowlist) if allowlist else "(none)"
        super().__init__(
            f"Undeclared side effect {side_effect!r} is not in the allowlist. "
            f"Allowed: {allowed}. "
            f"Add {side_effect!r} to output.side_effects in your workflow definition."
        )
