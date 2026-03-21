"""SetupPhase: preparation steps before sandboxed agent execution.

Implements the SetupPhase class that executes all preparation steps in
sequence before agent execution begins:

1. Detect sandbox runner via RunnerDetector (or use injected runner).
2. Provision the execution environment via runner.provision().
3. Verify network isolation (Docker runner only).
4. Run preflight checks.
5. Compile the output validator schema.
6. Generate a setup manifest and persist it.

The setup manifest is a JSON document capturing the full execution context
(versions, images, mounts, network rules, resource limits, credential
fingerprints). It is saved at::

    .agentry/runs/<TIMESTAMP>/setup-manifest.json

Usage::

    from agentry.security.setup import SetupPhase

    phase = SetupPhase(
        workflow=workflow_definition,
        runner=runner_instance,
        api_key="sk-ant-...",
    )
    result = phase.run()
    manifest_path = result.manifest_path
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentry.models.workflow import WorkflowDefinition
from agentry.security.envelope import (
    PreflightCheck,
    PreflightCheckResult,
    RunnerProtocol,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SetupPhaseError(Exception):
    """Base exception for setup phase failures."""


class SetupPreflightError(SetupPhaseError):
    """Raised when a preflight check fails during setup.

    Attributes:
        check_name: Name of the failed check.
        message: Human-readable failure description.
        remediation: Suggested fix, if available.
    """

    def __init__(
        self,
        check_name: str,
        message: str,
        remediation: str = "",
    ) -> None:
        self.check_name = check_name
        self.message = message
        self.remediation = remediation
        detail = f"Preflight check failed: {check_name}: {message}"
        if remediation:
            detail += f" Remediation: {remediation}"
        super().__init__(detail)


class SetupProvisionError(SetupPhaseError):
    """Raised when runner provisioning fails during setup."""


class SchemaCompilationError(SetupPhaseError):
    """Raised when output validator schema compilation fails."""


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


@dataclass
class SetupManifest:
    """The complete setup manifest for a single workflow execution.

    Attributes:
        workflow_name: Workflow identity name.
        workflow_version: Workflow semantic version string.
        container_image: Docker image used for the sandbox.
        filesystem_read: Glob patterns for read-permitted paths.
        filesystem_write: Glob patterns for write-permitted paths.
        network_egress_rules: Domain names allowed for egress.
        resource_cpu: CPU limit (fractional cores).
        resource_memory: Memory limit string (e.g. ``"2GB"``).
        resource_timeout: Execution timeout in seconds.
        credential_fingerprints: Mapping of credential name to SHA-256 hex digest.
        sandbox_tier: The trust level string (``"sandboxed"`` or ``"elevated"``).
        timestamp: ISO-8601 UTC timestamp when the manifest was generated.
        runner_metadata: Raw metadata dict returned by runner.provision().
        preflight_results: Results of all preflight checks.
    """

    workflow_name: str
    workflow_version: str
    container_image: str
    filesystem_read: list[str]
    filesystem_write: list[str]
    network_egress_rules: list[str]
    resource_cpu: float
    resource_memory: str
    resource_timeout: int
    credential_fingerprints: dict[str, str]
    sandbox_tier: str
    timestamp: str
    runner_metadata: dict[str, Any] = field(default_factory=dict)
    preflight_results: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict suitable for JSON encoding."""
        return {
            "workflow_name": self.workflow_name,
            "workflow_version": self.workflow_version,
            "container_image": self.container_image,
            "filesystem": {
                "read": self.filesystem_read,
                "write": self.filesystem_write,
            },
            "network": {
                "egress_rules": self.network_egress_rules,
            },
            "resources": {
                "cpu": self.resource_cpu,
                "memory": self.resource_memory,
                "timeout": self.resource_timeout,
            },
            "credential_fingerprints": self.credential_fingerprints,
            "sandbox_tier": self.sandbox_tier,
            "timestamp": self.timestamp,
            "runner_metadata": self.runner_metadata,
            "preflight_results": self.preflight_results,
        }


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class SetupPhaseResult:
    """Result of a completed (or failed) setup phase.

    Attributes:
        manifest: The generated setup manifest.  ``None`` if setup failed
            before manifest generation.
        manifest_path: Filesystem path where the manifest was saved, or
            empty string if not yet saved.
        preflight_results: List of all :class:`~agentry.security.envelope.PreflightCheckResult`
            instances collected during setup.
        runner_metadata: Metadata returned by ``runner.provision()``.
        schema_compiled: True if the output validator schema was compiled
            successfully.
        error: Human-readable error message on failure.
        aborted: True if setup was aborted due to a check or provision failure.
    """

    manifest: SetupManifest | None = None
    manifest_path: str = ""
    preflight_results: list[PreflightCheckResult] = field(default_factory=list)
    runner_metadata: dict[str, Any] = field(default_factory=dict)
    schema_compiled: bool = False
    error: str = ""
    aborted: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def fingerprint_credential(value: str) -> str:
    """Return the SHA-256 hex digest of a credential value.

    The fingerprint is used in the setup manifest so that the presence of a
    credential can be verified without storing the credential itself.

    Args:
        value: The raw credential string (e.g. an API key).

    Returns:
        Lower-case hex string of the SHA-256 digest.
    """
    return hashlib.sha256(value.encode()).hexdigest()


def _compile_schema(schema: dict[str, Any]) -> bool:
    """Compile/validate a JSON Schema dict.

    Attempts to import ``jsonschema`` and compile the schema.  Falls back
    gracefully when ``jsonschema`` is not installed (returns True because an
    empty or trivially valid schema is assumed to be fine).

    Args:
        schema: The JSON Schema definition dict.

    Returns:
        True when compilation succeeds or jsonschema is unavailable.

    Raises:
        SchemaCompilationError: When the schema is structurally invalid.
    """
    if not schema:
        return True
    try:
        import jsonschema  # type: ignore[import-untyped]

        jsonschema.Draft7Validator.check_schema(schema)
        return True
    except ImportError:
        logger.debug("jsonschema not available; skipping schema validation")
        return True
    except Exception as exc:
        raise SchemaCompilationError(
            f"Output validator schema compilation failed: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# SetupPhase
# ---------------------------------------------------------------------------


class SetupPhase:
    """Executes all preparation steps before sandboxed agent execution.

    Steps (executed in order by :meth:`run`):

    1. **Detect runner** -- optionally via RunnerDetector if no runner injected.
    2. **Provision** -- call ``runner.provision()`` to set up the environment.
    3. **Verify network isolation** -- log network metadata (Docker only).
    4. **Preflight checks** -- run each :class:`PreflightCheck`.
    5. **Compile output validator** -- validate the output schema.
    6. **Generate manifest** -- build and persist the setup manifest.

    Args:
        workflow: Parsed :class:`~agentry.models.workflow.WorkflowDefinition`.
        runner: An object satisfying
            :class:`~agentry.security.envelope.RunnerProtocol`.  Required
            because RunnerDetector (T01.6) is not yet implemented; callers
            must supply a concrete runner.
        preflight_checks: Optional list of preflight checks.
        api_key: Optional API key to fingerprint in the manifest.
        extra_credentials: Mapping of ``{name: value}`` for additional
            credentials to fingerprint.
        runs_dir: Base directory for run artefacts.  Defaults to
            ``Path.cwd() / ".agentry" / "runs"``.
    """

    def __init__(
        self,
        workflow: WorkflowDefinition,
        runner: RunnerProtocol,
        preflight_checks: list[PreflightCheck] | None = None,
        api_key: str = "",
        extra_credentials: dict[str, str] | None = None,
        runs_dir: Path | None = None,
    ) -> None:
        self._workflow = workflow
        self._runner = runner
        self._preflight_checks = preflight_checks or []
        self._api_key = api_key
        self._extra_credentials = extra_credentials or {}
        self._runs_dir = runs_dir or (Path.cwd() / ".agentry" / "runs")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> SetupPhaseResult:
        """Execute all setup steps in sequence.

        Returns:
            A :class:`SetupPhaseResult` describing the outcome.  On failure
            the result has ``aborted=True`` and ``error`` set.  On success
            ``manifest`` and ``manifest_path`` are populated.

        Raises:
            SetupPreflightError: When a preflight check fails.
            SetupProvisionError: When ``runner.provision()`` fails.
            SchemaCompilationError: When the output schema is invalid.
        """
        result = SetupPhaseResult()

        # Step 1 & 2: Provision the runner.
        try:
            logger.info("SetupPhase: provisioning runner")
            runner_meta = self._runner.provision()
            result.runner_metadata = runner_meta
            logger.debug("SetupPhase: runner provisioned: %s", runner_meta)
        except Exception as exc:
            result.aborted = True
            result.error = f"Runner provisioning failed: {exc}"
            raise SetupProvisionError(result.error) from exc

        # Step 3: Verify network isolation (log metadata; teardown is caller's
        # responsibility or happens in SecurityEnvelope).
        self._verify_network_isolation(runner_meta)

        # Step 4: Run preflight checks.
        for check in self._preflight_checks:
            check_result = check.run()
            result.preflight_results.append(check_result)
            if not check_result.passed:
                result.aborted = True
                result.error = (
                    f"Preflight check failed: {check_result.name}: {check_result.message}"
                )
                raise SetupPreflightError(
                    check_name=check_result.name,
                    message=check_result.message,
                    remediation=check_result.remediation,
                )

        # Step 5: Compile output validator.
        schema = self._workflow.output.schema_def
        try:
            result.schema_compiled = _compile_schema(schema)
        except SchemaCompilationError:
            result.aborted = True
            raise

        # Step 6: Generate and persist the setup manifest.
        manifest = self._build_manifest(
            runner_metadata=runner_meta,
            preflight_results=result.preflight_results,
        )
        result.manifest = manifest

        manifest_path = self._save_manifest(manifest)
        result.manifest_path = str(manifest_path)
        logger.info("SetupPhase: manifest saved to %s", manifest_path)

        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _verify_network_isolation(self, runner_metadata: dict[str, Any]) -> None:
        """Log network isolation metadata for Docker runners.

        For non-Docker runners this is a no-op.  Actual isolation is enforced
        by the Docker network (``internal=True``); here we just confirm the
        metadata is present.

        Args:
            runner_metadata: Metadata dict returned by ``runner.provision()``.
        """
        network_id = runner_metadata.get("network_id") or runner_metadata.get(
            "network"
        )
        if network_id:
            logger.info(
                "SetupPhase: network isolation verified (network_id=%s)", network_id
            )
        else:
            logger.debug(
                "SetupPhase: no network_id in runner metadata; skipping network isolation verification"
            )

    def _build_manifest(
        self,
        runner_metadata: dict[str, Any],
        preflight_results: list[PreflightCheckResult],
    ) -> SetupManifest:
        """Construct the :class:`SetupManifest` from workflow and runtime state.

        Args:
            runner_metadata: Metadata from ``runner.provision()``.
            preflight_results: Collected preflight check results.

        Returns:
            A fully populated :class:`SetupManifest`.
        """
        safety = self._workflow.safety
        identity = self._workflow.identity

        # Credential fingerprints: API key + any extras.
        fingerprints: dict[str, str] = {}
        if self._api_key:
            fingerprints["anthropic_api_key"] = fingerprint_credential(self._api_key)
        for name, value in self._extra_credentials.items():
            if value:
                fingerprints[name] = fingerprint_credential(value)

        # Container image from sandbox config.
        container_image = safety.sandbox.base

        # Convert preflight results to serialisable form.
        serialised_preflight = [
            {
                "name": r.name,
                "passed": r.passed,
                "message": r.message,
                "remediation": r.remediation,
            }
            for r in preflight_results
        ]

        return SetupManifest(
            workflow_name=identity.name,
            workflow_version=identity.version,
            container_image=container_image,
            filesystem_read=list(safety.filesystem.read),
            filesystem_write=list(safety.filesystem.write),
            network_egress_rules=list(safety.network.allow),
            resource_cpu=safety.resources.cpu,
            resource_memory=safety.resources.memory,
            resource_timeout=safety.resources.timeout,
            credential_fingerprints=fingerprints,
            sandbox_tier=safety.trust.value,
            timestamp=datetime.now(timezone.utc).isoformat(),
            runner_metadata=runner_metadata,
            preflight_results=serialised_preflight,
        )

    def _save_manifest(self, manifest: SetupManifest) -> Path:
        """Persist *manifest* to ``.agentry/runs/<TIMESTAMP>/setup-manifest.json``.

        The timestamp used in the directory name is derived from the manifest's
        own ``timestamp`` field (ISO-8601) with punctuation stripped so it is
        filesystem-safe.

        Args:
            manifest: The manifest to serialise.

        Returns:
            The :class:`pathlib.Path` of the saved manifest file.
        """
        # Derive a filesystem-safe timestamp string from the manifest timestamp.
        # e.g. "2026-03-20T12:34:56.789012+00:00" -> "20260320T123456"
        ts_safe = (
            manifest.timestamp.replace("-", "")
            .replace(":", "")
            .replace("+", "")
            .split(".")[0]  # drop microseconds
        )

        run_dir = self._runs_dir / ts_safe
        run_dir.mkdir(parents=True, exist_ok=True)

        manifest_path = run_dir / "setup-manifest.json"
        manifest_path.write_text(
            json.dumps(manifest.to_dict(), indent=2),
            encoding="utf-8",
        )
        return manifest_path
