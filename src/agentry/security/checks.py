"""Concrete preflight check implementations.

Provides three concrete preflight checks:

1. ``AnthropicAPIKeyCheck`` — verifies the ``ANTHROPIC_API_KEY`` environment
   variable is set and the key is accepted by the Anthropic API (GET
   /v1/models).
2. ``DockerAvailableCheck`` — verifies the Docker daemon is running and
   accessible; only relevant when ``trust == "sandboxed"``.
3. ``FilesystemMountsCheck`` — verifies that every path declared in
   ``safety.filesystem.read`` and ``safety.filesystem.write`` exists on the
   host before container mount.

Each class satisfies the
:class:`~agentry.security.envelope.PreflightCheck` protocol::

    @property
    def name(self) -> str: ...
    def run(self) -> PreflightCheckResult: ...

Usage::

    from agentry.security.checks import (
        AnthropicAPIKeyCheck,
        DockerAvailableCheck,
        FilesystemMountsCheck,
    )

    checks = [
        AnthropicAPIKeyCheck(),
        DockerAvailableCheck(trust=workflow.safety.trust),
        FilesystemMountsCheck(
            read_paths=workflow.safety.filesystem.read,
            write_paths=workflow.safety.filesystem.write,
        ),
    ]
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lightweight shared result type (mirrors PreflightCheckResult)
# ---------------------------------------------------------------------------


@dataclass
class _CheckResult:
    """Minimal result returned by each concrete check.

    Satisfies the :class:`~agentry.security.envelope.PreflightCheckResult`
    interface expected by :class:`~agentry.security.preflight.PreflightChecker`.
    """

    passed: bool
    name: str
    message: str = ""
    remediation: str = ""


# ---------------------------------------------------------------------------
# AnthropicAPIKeyCheck
# ---------------------------------------------------------------------------


class AnthropicAPIKeyCheck:
    """Preflight check that validates the Anthropic API key.

    Verification steps:

    1. Check that ``ANTHROPIC_API_KEY`` is set and non-empty in the
       process environment.
    2. Make a lightweight ``GET /v1/models`` request to confirm the key is
       accepted by the API (not revoked or malformed).

    The HTTP call uses the stdlib ``urllib`` to avoid pulling in the
    ``anthropic`` SDK at preflight time and to keep the check fast.

    Args:
        env_var: Environment variable name that holds the API key.
            Defaults to ``"ANTHROPIC_API_KEY"``.
        api_base: Base URL for the Anthropic API.
            Defaults to ``"https://api.anthropic.com"``.
        timeout: Network timeout in seconds for the validation request.
            Defaults to ``10``.
    """

    def __init__(
        self,
        env_var: str = "ANTHROPIC_API_KEY",
        api_base: str = "https://api.anthropic.com",
        timeout: int = 10,
    ) -> None:
        self._env_var = env_var
        self._api_base = api_base
        self._timeout = timeout

    @property
    def name(self) -> str:
        """Name of this preflight check."""
        return "anthropic_api_key"

    def run(self) -> _CheckResult:
        """Execute the API key validation.

        Returns:
            :class:`_CheckResult` with ``passed=True`` on success, or a
            descriptive failure when the key is missing/invalid.
        """
        key = os.environ.get(self._env_var, "")
        if not key:
            return _CheckResult(
                passed=False,
                name=self.name,
                message=f"{self._env_var} is not set.",
                remediation=(
                    f"Export the API key: "
                    f"export {self._env_var}=<your-key>"
                ),
            )

        # Validate key against the API.
        url = f"{self._api_base}/v1/models"
        req = urllib.request.Request(
            url,
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                if resp.status == 200:
                    return _CheckResult(
                        passed=True,
                        name=self.name,
                        message="ANTHROPIC_API_KEY is set and accepted by the API.",
                    )
                # Unexpected non-error status (e.g. 204) — treat as failure.
                return _CheckResult(
                    passed=False,
                    name=self.name,
                    message=(
                        f"Unexpected API response status {resp.status}. "
                        "The key may be invalid."
                    ),
                    remediation="Verify the API key is correct and not revoked.",
                )
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                return _CheckResult(
                    passed=False,
                    name=self.name,
                    message="ANTHROPIC_API_KEY is invalid or revoked (401 Unauthorized).",
                    remediation=(
                        "Obtain a valid API key from https://console.anthropic.com "
                        f"and set {self._env_var}."
                    ),
                )
            return _CheckResult(
                passed=False,
                name=self.name,
                message=f"API key validation failed: HTTP {exc.code} {exc.reason}.",
                remediation="Check network connectivity and API key validity.",
            )
        except urllib.error.URLError as exc:
            return _CheckResult(
                passed=False,
                name=self.name,
                message=f"Could not reach Anthropic API: {exc.reason}.",
                remediation=(
                    "Check network connectivity. "
                    "If offline, use --skip-preflight for local testing only."
                ),
            )
        except OSError as exc:
            return _CheckResult(
                passed=False,
                name=self.name,
                message=f"Network error during API key validation: {exc}.",
                remediation="Check network connectivity.",
            )


# ---------------------------------------------------------------------------
# DockerAvailableCheck
# ---------------------------------------------------------------------------


class DockerAvailableCheck:
    """Preflight check that verifies Docker is running and accessible.

    This check is a no-op when ``trust != "sandboxed"`` because Docker is
    only required when the workflow runs inside a container.

    The check runs ``docker info`` (or the configured *ping_command*) to
    confirm the Docker daemon responds.

    Args:
        trust: Trust level string from the workflow safety block.  When
            set to ``"sandboxed"`` the check is active; all other values
            cause the check to pass immediately as a no-op.
        ping_command: The shell command used to probe Docker availability.
            Defaults to ``["docker", "info"]``.
        timeout: Subprocess timeout in seconds.  Defaults to ``10``.
    """

    def __init__(
        self,
        trust: str = "sandboxed",
        ping_command: list[str] | None = None,
        timeout: int = 10,
    ) -> None:
        self._trust = str(trust)
        self._ping_command: list[str] = ping_command or ["docker", "info"]
        self._timeout = timeout

    @property
    def name(self) -> str:
        """Name of this preflight check."""
        return "docker_available"

    def run(self) -> _CheckResult:
        """Execute the Docker availability check.

        Returns:
            :class:`_CheckResult` with ``passed=True`` when Docker is
            available or when the trust level does not require Docker.
        """
        if self._trust != "sandboxed":
            return _CheckResult(
                passed=True,
                name=self.name,
                message=(
                    f"Docker check skipped — trust level is '{self._trust}', "
                    "Docker is only required for sandboxed execution."
                ),
            )

        # Check if docker executable is on the PATH first.
        if not shutil.which(self._ping_command[0]):
            return _CheckResult(
                passed=False,
                name=self.name,
                message=(
                    f"Docker executable '{self._ping_command[0]}' not found on PATH. "
                    "Docker is required for sandboxed execution."
                ),
                remediation=(
                    "Install Docker Desktop from https://www.docker.com/products/docker-desktop "
                    "or install the Docker CLI via your package manager."
                ),
            )

        try:
            result = subprocess.run(
                self._ping_command,
                capture_output=True,
                timeout=self._timeout,
                check=False,
            )
            if result.returncode == 0:
                return _CheckResult(
                    passed=True,
                    name=self.name,
                    message="Docker daemon is running and accessible.",
                )
            stderr = result.stderr.decode(errors="replace").strip()
            return _CheckResult(
                passed=False,
                name=self.name,
                message=(
                    f"Docker is not running or not accessible "
                    f"(exit code {result.returncode}). "
                    + (f"Error: {stderr}" if stderr else "")
                ).strip(),
                remediation=(
                    "Start Docker Desktop or run 'sudo systemctl start docker'. "
                    "Docker is required for sandboxed execution."
                ),
            )
        except subprocess.TimeoutExpired:
            return _CheckResult(
                passed=False,
                name=self.name,
                message=f"Docker check timed out after {self._timeout}s.",
                remediation="Ensure Docker Desktop is fully started and responsive.",
            )
        except OSError as exc:
            return _CheckResult(
                passed=False,
                name=self.name,
                message=f"Failed to run Docker check: {exc}.",
                remediation="Ensure Docker is installed and the CLI is on your PATH.",
            )


# ---------------------------------------------------------------------------
# FilesystemMountsCheck
# ---------------------------------------------------------------------------


@dataclass
class FilesystemMountsCheck:
    """Preflight check that verifies declared filesystem paths exist on host.

    All paths declared in ``safety.filesystem.read`` and
    ``safety.filesystem.write`` must exist on the host before a container
    can bind-mount them.  This check catches configuration mistakes early
    rather than failing at container start time with an opaque error.

    Args:
        read_paths: List of host paths declared for read access.
        write_paths: List of host paths declared for write access.
    """

    read_paths: list[str] = field(default_factory=list)
    write_paths: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        """Name of this preflight check."""
        return "filesystem_mounts"

    def run(self) -> _CheckResult:
        """Verify all declared paths exist on the host.

        Returns:
            :class:`_CheckResult` with ``passed=True`` when every declared
            path exists, or ``passed=False`` listing the first missing path.
        """
        all_paths: list[tuple[str, str]] = [
            (p, "read") for p in self.read_paths
        ] + [(p, "write") for p in self.write_paths]

        missing: list[str] = []
        for path, _access in all_paths:
            if not os.path.exists(path):
                missing.append(path)

        if not missing:
            total = len(all_paths)
            return _CheckResult(
                passed=True,
                name=self.name,
                message=(
                    f"All {total} declared filesystem path(s) exist on the host."
                    if total
                    else "No filesystem paths declared; check passes trivially."
                ),
            )

        missing_list = ", ".join(f'"{p}"' for p in missing)
        return _CheckResult(
            passed=False,
            name=self.name,
            message=(
                f"{len(missing)} declared path(s) do not exist on the host: "
                f"{missing_list}."
            ),
            remediation=(
                "Create the missing directories/files or update the workflow's "
                "filesystem configuration to reference existing paths."
            ),
        )
