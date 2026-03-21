"""Exceptions raised by LLM client implementations.

All provider-specific errors are mapped to these types so that the rest of
the application can handle LLM failures without importing provider SDKs.
"""

from __future__ import annotations


class LLMError(Exception):
    """Base class for all LLM client errors."""


class LLMAuthError(LLMError):
    """Raised when authentication fails (missing or invalid API key).

    Args:
        message: Human-readable description of the error.
        suggestion: Optional remediation hint shown to the user.
    """

    def __init__(self, message: str, suggestion: str = "") -> None:
        self.suggestion = suggestion
        full_message = message
        if suggestion:
            full_message = f"{message}\n\nSuggestion: {suggestion}"
        super().__init__(full_message)


class LLMTimeoutError(LLMError):
    """Raised when a call exceeds the configured timeout.

    Args:
        timeout_seconds: The timeout limit that was exceeded.
    """

    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"LLM call timed out after {timeout_seconds:.1f} seconds. "
            "Increase the safety.resources.timeout value in your workflow definition."
        )


class LLMProviderError(LLMError):
    """Raised for provider-level errors that are not auth or timeout issues.

    Args:
        message: Human-readable description of the error.
        status_code: HTTP status code if applicable.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)
