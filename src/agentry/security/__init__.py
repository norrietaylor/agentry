"""Security envelope for sandboxed agent execution.

Wraps the AgentExecutor with security controls including tool manifest
enforcement, runner lifecycle management, preflight checks, and output
validation. The SecurityEnvelope is the primary entry point for executing
workflows that require sandbox isolation.

Public API
----------
- :class:`~agentry.security.envelope.SecurityEnvelope` -- Wraps AgentExecutor
  with security controls.
- :class:`~agentry.security.envelope.RunnerProtocol` -- Protocol for execution
  environment runners.
- :class:`~agentry.security.envelope.PreflightCheck` -- Protocol for preflight
  checks.
- :class:`~agentry.security.envelope.EnvelopeResult` -- Result of a secured
  agent execution.
- :exc:`~agentry.security.envelope.SecurityEnvelopeError` -- Base error for
  envelope failures.
- :exc:`~agentry.security.envelope.ToolManifestViolationError` -- Raised when
  tools exceed the workflow manifest.
- :exc:`~agentry.security.envelope.PreflightError` -- Raised when a preflight
  check fails.
"""

from agentry.security.envelope import (
    EnvelopeResult,
    PreflightCheck,
    PreflightError,
    RunnerProtocol,
    SecurityEnvelope,
    SecurityEnvelopeError,
    ToolManifestViolationError,
)

__all__ = [
    "EnvelopeResult",
    "PreflightCheck",
    "PreflightError",
    "RunnerProtocol",
    "SecurityEnvelope",
    "SecurityEnvelopeError",
    "ToolManifestViolationError",
]
