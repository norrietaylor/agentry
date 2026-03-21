"""SetupPhase: preparation steps before sandboxed agent execution.

Implements the SetupPhase class that executes all preparation steps in
sequence before agent execution begins:

1. Detect sandbox runner via RunnerDetector (or use injected runner).
2. Provision the execution environment via runner.provision().
3. Verify network isolation (Docker runner only).
4. Run preflight checks.
5. Verify workflow signature (when signature block present and public key available).
6. Compile the output validator schema.
7. Generate a setup manifest and persist it.

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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from agentry.models.workflow import WorkflowDefinition
from agentry.security.envelope import (
    PreflightCheck,
    PreflightCheckResult,
    RunnerProtocol,
)
from agentry.security.signing import (
    DEFAULT_PUBLIC_KEY_PATH,
    SignatureVerificationError,
    verify_workflow_signature,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SetupPhaseError(Exception):
    """Base exception for setup phase failures."""


class SetupSignatureError(SetupPhaseError):
    """Raised when workflow signature verification fails during setup.

    Attributes:
        message: Human-readable failure description including the signed
            timestamp from the signature block.
    """

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


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


class NetworkIsolationError(SetupPhaseError):
    """Raised when network isolation verification fails during setup.

    Attributes:
        message: Human-readable failure description including which checks
            failed and diagnostic guidance.
    """

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


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


def _sanitise_runner_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *metadata* with non-JSON-serialisable values removed.

    Runner metadata may contain Python objects (e.g. a
    :class:`~agentry.runners.dns_proxy.DNSFilteringProxy` instance or a Docker
    client) that cannot be serialised to JSON. This function produces a shallow
    copy that retains only primitive-typed values (str, int, float, bool, None)
    and recursively sanitised dicts/lists.

    Args:
        metadata: The raw runner metadata dict.

    Returns:
        A new dict containing only JSON-serialisable entries.
    """
    result: dict[str, Any] = {}
    for key, value in metadata.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = value
        elif isinstance(value, dict):
            result[key] = _sanitise_runner_metadata(value)
        elif isinstance(value, list):
            safe_list: list[Any] = []
            for item in value:
                if isinstance(item, (str, int, float, bool)) or item is None:
                    safe_list.append(item)
                elif isinstance(item, dict):
                    safe_list.append(_sanitise_runner_metadata(item))
                # Skip non-serialisable items.
            result[key] = safe_list
        # Skip other non-serialisable types (objects, etc.).
    return result


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
        workflow_path: Optional path to the workflow YAML file.  When
            supplied and the YAML contains a ``signature`` block, signature
            verification is performed before execution proceeds.  If no
            *workflow_path* is given, signature verification is skipped.
        public_key_path: Path to the Ed25519 public key used for signature
            verification.  Defaults to ``DEFAULT_PUBLIC_KEY_PATH``
            (``.agentry/public-key.pem`` relative to cwd).
        blocked_verification_domain: Domain used to verify that isolation is
            active during :meth:`_verify_network_isolation`. Defaults to
            ``"example.com"``. Override in tests to use a domain that is
            explicitly added to the allow list to trigger a failure.
        allowed_verification_domain: Domain used to verify that the LLM API
            is reachable. Defaults to ``"api.anthropic.com"``.
    """

    def __init__(
        self,
        workflow: WorkflowDefinition,
        runner: RunnerProtocol,
        preflight_checks: list[PreflightCheck] | None = None,
        api_key: str = "",
        extra_credentials: dict[str, str] | None = None,
        runs_dir: Path | None = None,
        workflow_path: Path | str | None = None,
        public_key_path: Path | None = None,
        blocked_verification_domain: str = "example.com",
        allowed_verification_domain: str = "api.anthropic.com",
    ) -> None:
        self._workflow = workflow
        self._runner = runner
        self._preflight_checks = preflight_checks or []
        self._api_key = api_key
        self._extra_credentials = extra_credentials or {}
        self._runs_dir = runs_dir or (Path.cwd() / ".agentry" / "runs")
        self._workflow_path = Path(workflow_path) if workflow_path else None
        self._public_key_path = public_key_path or DEFAULT_PUBLIC_KEY_PATH
        self._blocked_verification_domain = blocked_verification_domain
        self._allowed_verification_domain = allowed_verification_domain

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
            SetupSignatureError: When a signature block is present but
                verification fails.
            NetworkIsolationError: When network isolation verification fails.
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

        # Step 3: Verify network isolation. When the runner metadata includes
        # a dns_proxy, the verifier confirms that blocking is active and raises
        # NetworkIsolationError if verification fails.
        try:
            self._verify_network_isolation(runner_meta)
        except NetworkIsolationError:
            result.aborted = True
            raise

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

        # Step 5: Verify workflow signature (opt-in; skip when no signature block).
        self._verify_signature(result)

        # Step 6: Compile output validator.
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

    def _verify_signature(self, result: SetupPhaseResult) -> None:
        """Verify the workflow signature when a signature block is present.

        Signature verification is opt-in.  The check is skipped when:
        - No *workflow_path* was provided to :class:`SetupPhase`.
        - The workflow YAML does not contain a ``signature`` block.

        When a signature block **is** present and a public key exists at
        *public_key_path*, the signature is verified.  If verification fails
        the setup is aborted with :class:`SetupSignatureError`.

        The public key is looked up at *public_key_path* (defaults to
        ``.agentry/public-key.pem``).  If the public key file does not exist,
        verification is also skipped (the public key must be explicitly
        deployed to enforce signing).

        Args:
            result: The in-progress :class:`SetupPhaseResult` to mark aborted
                on failure.

        Raises:
            SetupSignatureError: When the signature block is present but
                invalid.
        """
        if self._workflow_path is None:
            logger.debug("SetupPhase: no workflow_path provided; skipping signature verification")
            return

        if not self._workflow_path.exists():
            logger.debug(
                "SetupPhase: workflow_path %s not found; skipping signature verification",
                self._workflow_path,
            )
            return

        # Load the raw YAML to check for a signature block without going
        # through the Pydantic model (which strips unknown keys).
        with self._workflow_path.open() as fh:
            raw_workflow: dict[str, Any] = yaml.safe_load(fh) or {}

        if "signature" not in raw_workflow:
            logger.debug(
                "SetupPhase: no signature block in %s; skipping verification (opt-in)",
                self._workflow_path,
            )
            return

        if not self._public_key_path.exists():
            logger.debug(
                "SetupPhase: public key not found at %s; skipping signature verification",
                self._public_key_path,
            )
            return

        logger.info(
            "SetupPhase: verifying workflow signature (public_key=%s)",
            self._public_key_path,
        )
        try:
            timestamp = verify_workflow_signature(
                self._workflow_path,
                public_key_path=self._public_key_path,
            )
            logger.info("SetupPhase: signature verified (signed_at=%s)", timestamp)
        except SignatureVerificationError as exc:
            result.aborted = True
            result.error = str(exc)
            raise SetupSignatureError(str(exc)) from exc

    def _verify_network_isolation(self, runner_metadata: dict[str, Any]) -> None:
        """Verify network isolation for Docker sandbox runners.

        When the runner metadata includes a ``dns_proxy`` entry (a
        :class:`~agentry.runners.dns_proxy.DNSFilteringProxy` instance
        attached during provisioning), this method uses
        :class:`~agentry.runners.network_isolation.NetworkIsolationVerifier`
        to confirm that:

        - A known-blocked domain (``example.com``) is rejected.
        - The LLM API domain (``api.anthropic.com``) is allowed.

        If verification fails, :class:`NetworkIsolationError` is raised to
        abort the setup phase with a diagnostic.

        For non-Docker runners (or when no ``dns_proxy`` is present in
        metadata), this step is skipped with a debug log.

        Args:
            runner_metadata: Metadata dict returned by ``runner.provision()``.

        Raises:
            NetworkIsolationError: When isolation checks fail.
        """
        from agentry.runners.network_isolation import NetworkIsolationVerifier

        dns_proxy = runner_metadata.get("dns_proxy")
        network_id = runner_metadata.get("network_id") or runner_metadata.get("network")

        if dns_proxy is None:
            if network_id:
                logger.info(
                    "SetupPhase: network_id=%s present but no dns_proxy; "
                    "skipping DNS isolation verification",
                    network_id,
                )
            else:
                logger.debug(
                    "SetupPhase: no dns_proxy in runner metadata; "
                    "skipping network isolation verification"
                )
            return

        logger.info(
            "SetupPhase: verifying network isolation via DNS filtering proxy"
        )

        docker_client = runner_metadata.get("docker_client")
        container_id = runner_metadata.get("container_id", "")

        verifier = NetworkIsolationVerifier(
            proxy=dns_proxy,
            blocked_domain=self._blocked_verification_domain,
            allowed_domain=self._allowed_verification_domain,
            docker_client=docker_client,
            container_id=container_id,
        )
        result = verifier.verify()

        if not result.passed:
            raise NetworkIsolationError(result.diagnostic)

        logger.info(
            "SetupPhase: network isolation verified (%d check(s) passed)",
            len(result.checks),
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

        # Sanitise runner metadata to remove non-JSON-serialisable objects
        # (e.g. DNSFilteringProxy instances, Docker client objects).
        serialisable_metadata = _sanitise_runner_metadata(runner_metadata)

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
            runner_metadata=serialisable_metadata,
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
