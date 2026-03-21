"""Security envelope and setup phase for sandboxed agent execution.

Wraps the AgentExecutor with security controls including tool manifest
enforcement, runner lifecycle management, preflight checks, and output
validation. The SecurityEnvelope is the primary entry point for executing
workflows that require sandbox isolation.

SetupPhase executes all preparation steps (provisioning, network isolation
verification, preflight checks, schema compilation, manifest generation)
before the agent runs.

Public API
----------
Envelope:
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

Setup:
- :class:`~agentry.security.setup.SetupPhase` -- Runs all preparation steps.
- :class:`~agentry.security.setup.SetupManifest` -- The generated manifest.
- :class:`~agentry.security.setup.SetupPhaseResult` -- Outcome of setup.
- :func:`~agentry.security.setup.fingerprint_credential` -- SHA-256 credential
  fingerprinting utility.
- :exc:`~agentry.security.setup.SetupPhaseError` -- Base error for setup.
- :exc:`~agentry.security.setup.SetupPreflightError` -- Preflight failure.
- :exc:`~agentry.security.setup.SetupProvisionError` -- Provision failure.
- :exc:`~agentry.security.setup.SchemaCompilationError` -- Schema invalid.

Concrete checks:
- :class:`~agentry.security.checks.AnthropicAPIKeyCheck` -- Validates API key.
- :class:`~agentry.security.checks.DockerAvailableCheck` -- Validates Docker.
- :class:`~agentry.security.checks.FilesystemMountsCheck` -- Validates paths.
"""

from agentry.security.checks import (
    AnthropicAPIKeyCheck,
    DockerAvailableCheck,
    FilesystemMountsCheck,
)
from agentry.security.envelope import (
    EnvelopeResult,
    PreflightCheck,
    PreflightError,
    RunnerProtocol,
    SecurityEnvelope,
    SecurityEnvelopeError,
    ToolManifestViolationError,
)
from agentry.security.setup import (
    SchemaCompilationError,
    SetupManifest,
    SetupPhase,
    SetupPhaseError,
    SetupPhaseResult,
    SetupPreflightError,
    SetupProvisionError,
    fingerprint_credential,
)

__all__ = [
    # Envelope
    "EnvelopeResult",
    "PreflightCheck",
    "PreflightError",
    "RunnerProtocol",
    "SecurityEnvelope",
    "SecurityEnvelopeError",
    "ToolManifestViolationError",
    # Setup
    "SchemaCompilationError",
    "SetupManifest",
    "SetupPhase",
    "SetupPhaseError",
    "SetupPhaseResult",
    "SetupPreflightError",
    "SetupProvisionError",
    "fingerprint_credential",
    # Concrete checks
    "AnthropicAPIKeyCheck",
    "DockerAvailableCheck",
    "FilesystemMountsCheck",
]
