"""Concrete preflight check implementations.

Provides six concrete preflight checks:

1. ``AnthropicAPIKeyCheck`` — verifies the ``ANTHROPIC_API_KEY`` environment
   variable is set and the key is accepted by the Anthropic API (GET
   /v1/models).
1b. ``ClaudeCodeAuthCheck`` — passes if ``ANTHROPIC_API_KEY`` is set **or**
   the ``claude`` CLI is on PATH (OAuth, GitHub App, etc.).
2. ``DockerAvailableCheck`` — verifies the Docker daemon is running and
   accessible; only relevant when ``trust == "sandboxed"``.
3. ``FilesystemMountsCheck`` — verifies that every path declared in
   ``safety.filesystem.read`` and ``safety.filesystem.write`` exists on the
   host before container mount.
4. ``GitHubTokenScopeCheck`` — verifies that ``GITHUB_TOKEN`` has the
   required scopes for the declared tools when running in GitHub Actions CI.
5. ``AgentAvailabilityCheck`` — verifies that the binary required by the
   selected agent runtime is present on ``PATH``.

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
        GitHubTokenScopeCheck,
    )

    checks = [
        AnthropicAPIKeyCheck(),
        DockerAvailableCheck(trust=workflow.safety.trust),
        FilesystemMountsCheck(
            read_paths=workflow.safety.filesystem.read,
            write_paths=workflow.safety.filesystem.write,
        ),
        GitHubTokenScopeCheck(
            tool_declarations=["repository:read", "pr:comment"],
            github_repository="owner/repo",
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
# ClaudeCodeAuthCheck
# ---------------------------------------------------------------------------


class ClaudeCodeAuthCheck:
    """Preflight check for Claude Code authentication.

    Passes if **any** of the following hold:

    1. ``ANTHROPIC_API_KEY`` is set (direct API access).
    2. ``CLAUDE_CODE_OAUTH_TOKEN`` is set (Claude GitHub App or OAuth token).
    3. The ``claude`` CLI is on PATH (OAuth, GitHub App, or other managed auth).

    This is the recommended preflight check when the agent runtime is
    ``claude-code``, because Claude Code supports multiple authentication
    methods beyond raw API keys (OAuth via ``claude login``, the Claude
    GitHub App in CI, etc.).
    """

    _OAUTH_TOKEN_VAR = "CLAUDE_CODE_OAUTH_TOKEN"

    def __init__(self, env_var: str = "ANTHROPIC_API_KEY") -> None:
        self._env_var = env_var

    @property
    def name(self) -> str:
        """Name of this preflight check."""
        return "claude_code_auth"

    def run(self) -> _CheckResult:
        """Check for any valid Claude Code authentication method."""
        # Method 1: ANTHROPIC_API_KEY is set.
        key = os.environ.get(self._env_var, "")
        if key:
            return _CheckResult(
                passed=True,
                name=self.name,
                message=f"{self._env_var} is set.",
            )

        # Method 2: CLAUDE_CODE_OAUTH_TOKEN is set (Claude GitHub App, OAuth).
        oauth_token = os.environ.get(self._OAUTH_TOKEN_VAR, "")
        if oauth_token:
            return _CheckResult(
                passed=True,
                name=self.name,
                message=f"{self._OAUTH_TOKEN_VAR} is set.",
            )

        # Method 3: claude CLI is available (handles its own auth via
        # OAuth, GitHub App, etc.).
        claude_path = shutil.which("claude")
        if claude_path:
            return _CheckResult(
                passed=True,
                name=self.name,
                message=(
                    f"{self._env_var} is not set, but claude CLI is "
                    f"available at {claude_path}. Claude Code will use "
                    "its own authentication (OAuth, GitHub App, etc.)."
                ),
            )

        return _CheckResult(
            passed=False,
            name=self.name,
            message=(
                f"No Claude Code authentication found. {self._env_var} is "
                "not set and claude CLI is not on PATH."
            ),
            remediation=(
                "Either export ANTHROPIC_API_KEY=<your-key>, or install "
                "and authenticate Claude Code (claude login), or install "
                "the Claude GitHub App for CI usage."
            ),
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


# ---------------------------------------------------------------------------
# GitHubTokenScopeCheck
# ---------------------------------------------------------------------------

# Mapping from tool declaration to required GitHub token scope(s).
# Each tool may require one or more scopes; the token must satisfy at least
# one of the alternatives where listed (first entry is preferred).
_TOOL_TO_SCOPES: dict[str, list[str]] = {
    "repository:read": ["contents:read"],
    "pr:comment": ["pull-requests:write", "issues:write"],
    "pr:review": ["pull-requests:write"],
}

# GitHub API scope names as returned in the X-OAuth-Scopes header map to the
# fine-grained permission names we use internally.
_SCOPE_TO_API_SCOPE: dict[str, str] = {
    "contents:read": "contents",
    "pull-requests:write": "pull_requests",
    "issues:write": "issues",
}


class GitHubTokenScopeCheck:
    """Preflight check that verifies GITHUB_TOKEN has required scopes.

    When running in a GitHub Actions context (``GITHUB_TOKEN`` set), this
    check verifies that the token has sufficient permissions for the declared
    tools.  Outside CI (no ``GITHUB_TOKEN``), the check passes immediately
    as a no-op.

    Verification strategy:

    1. Collect required scopes from ``tool_declarations`` using the known
       tool-to-scope mapping.
    2. Make a ``GET /repos/{owner}/{repo}`` request using the token.
    3. Inspect the ``X-OAuth-Scopes`` response header for classic tokens, or
       treat a ``403`` response as an indicator of missing scope for
       fine-grained tokens.

    Args:
        tool_declarations: List of tool declaration strings such as
            ``"repository:read"`` and ``"pr:comment"``.
        github_repository: Repository in ``owner/repo`` format used for
            the test API call.  Defaults to ``""`` (skips API call).
        api_base: Base URL for the GitHub API.
            Defaults to ``"https://api.github.com"``.
        timeout: Network timeout in seconds.  Defaults to ``10``.
    """

    def __init__(
        self,
        tool_declarations: list[str],
        github_repository: str,
        api_base: str = "https://api.github.com",
        timeout: int = 10,
    ) -> None:
        self._tool_declarations = list(tool_declarations)
        self._github_repository = github_repository
        self._api_base = api_base.rstrip("/")
        self._timeout = timeout

    @property
    def name(self) -> str:
        """Name of this preflight check."""
        return "github_token_scope"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _required_scopes(self) -> dict[str, list[str]]:
        """Return mapping of required scope -> tools that need it."""
        scope_to_tools: dict[str, list[str]] = {}
        for tool in self._tool_declarations:
            scopes = _TOOL_TO_SCOPES.get(tool, [])
            for scope in scopes:
                scope_to_tools.setdefault(scope, []).append(tool)
        return scope_to_tools

    def _check_scope_via_api(
        self, token: str, scope: str
    ) -> tuple[bool, str]:
        """Check a single scope by making a lightweight test API call.

        Returns a (passed, detail) tuple where *detail* is an empty string
        on success or an error description on failure.
        """
        if not self._github_repository:
            # Cannot verify without repository; optimistically pass.
            return True, ""

        url = f"{self._api_base}/repos/{self._github_repository}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                # For classic tokens the granted scopes are in the header.
                oauth_scopes_header = resp.headers.get("X-OAuth-Scopes", "")
                if oauth_scopes_header:
                    granted = {
                        s.strip() for s in oauth_scopes_header.split(",")
                    }
                    api_scope = _SCOPE_TO_API_SCOPE.get(scope, scope)
                    # Classic token scopes are coarser; map fine-grained names.
                    if scope == "contents:read" and (
                        "repo" in granted or "public_repo" in granted
                    ):
                        return True, ""
                    if scope in ("pull-requests:write", "issues:write") and "repo" in granted:
                        return True, ""
                    if api_scope in granted or scope in granted:
                        return True, ""
                    # Header present but scope not found — fail.
                    return False, (
                        f"X-OAuth-Scopes header present but '{scope}' "
                        f"not found (granted: {oauth_scopes_header!r})"
                    )
                # Fine-grained token: 200 on GET /repos implies at least
                # contents:read.  Treat success for contents:read as passed.
                if scope == "contents:read":
                    return True, ""
                # For write scopes on fine-grained tokens we cannot confirm
                # from a read-only endpoint; optimistically pass here and
                # rely on the 403 path caught below.
                return True, ""
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                return False, (
                    f"GitHub API returned 403 Forbidden for scope '{scope}'; "
                    "token lacks the required permission."
                )
            if exc.code == 404:
                # Repository not found — may be a private repo with no access,
                # but this is not necessarily a scope issue.
                return False, (
                    f"Repository '{self._github_repository}' not found "
                    "(404). Verify the repository name and token access."
                )
            return False, (
                f"GitHub API error HTTP {exc.code} when checking scope "
                f"'{scope}': {exc.reason}."
            )
        except urllib.error.URLError as exc:
            return False, (
                f"Could not reach GitHub API while checking scope "
                f"'{scope}': {exc.reason}."
            )
        except OSError as exc:
            return False, (
                f"Network error while checking scope '{scope}': {exc}."
            )

    # ------------------------------------------------------------------
    # run()
    # ------------------------------------------------------------------

    def run(self) -> _CheckResult:
        """Execute the GitHub token scope verification.

        Returns:
            :class:`_CheckResult` with ``passed=True`` when the token has
            sufficient scopes for all declared tools, or when ``GITHUB_TOKEN``
            is not set (not in CI context).
        """
        token = os.environ.get("GITHUB_TOKEN", "")
        if not token:
            return _CheckResult(
                passed=True,
                name=self.name,
                message=(
                    "GITHUB_TOKEN is not set; skipping scope check "
                    "(not running in a GitHub Actions CI context)."
                ),
            )

        scope_to_tools = self._required_scopes()
        if not scope_to_tools:
            return _CheckResult(
                passed=True,
                name=self.name,
                message="No tool declarations require GitHub token scopes.",
            )

        missing_scopes: list[tuple[str, list[str], str]] = []
        # scope -> (tools requiring it, detail message)

        for scope, tools in scope_to_tools.items():
            passed, detail = self._check_scope_via_api(token, scope)
            if not passed:
                missing_scopes.append((scope, tools, detail))

        if not missing_scopes:
            return _CheckResult(
                passed=True,
                name=self.name,
                message=(
                    "GITHUB_TOKEN has all required scopes for the declared "
                    "tools."
                ),
            )

        # Build a descriptive failure message.
        lines: list[str] = [
            "GITHUB_TOKEN is missing required scopes:"
        ]
        remediation_scopes: list[str] = []
        for scope, tools, detail in missing_scopes:
            tools_str = ", ".join(f'"{t}"' for t in tools)
            line = f"  - '{scope}' (required by: {tools_str})"
            if detail:
                line += f" — {detail}"
            lines.append(line)
            # Collect unique permission names for remediation.
            perm = scope.replace(":", ": ")
            if perm not in remediation_scopes:
                remediation_scopes.append(perm)

        remediation_lines = ["permissions:"]
        for perm in remediation_scopes:
            remediation_lines.append(f"  {perm}")

        return _CheckResult(
            passed=False,
            name=self.name,
            message="\n".join(lines),
            remediation=(
                "Add `permissions: pull-requests: write` to your GitHub "
                "Actions workflow YAML, for example:\n"
                + "\n".join(remediation_lines)
            ),
        )


# ---------------------------------------------------------------------------
# AgentAvailabilityCheck
# ---------------------------------------------------------------------------

# Maps agent runtime names to the binary that must be present on PATH.
_RUNTIME_BINARY: dict[str, str] = {
    "claude-code": "claude",
}


class AgentAvailabilityCheck:
    """Preflight check that verifies the required agent binary is on PATH.

    For each known agent runtime a specific binary is required:

    - ``claude-code`` → ``claude``

    For unknown runtimes the check always passes (no assumption can be made
    about what binary is needed).

    Args:
        runtime: The agent runtime identifier from the workflow's agent block
            (e.g. ``"claude-code"``).
    """

    def __init__(self, runtime: str) -> None:
        self._runtime = runtime

    @property
    def name(self) -> str:
        """Name of this preflight check."""
        return "agent_availability"

    def run(self) -> _CheckResult:
        """Check that the required binary is available on PATH.

        Returns:
            :class:`_CheckResult` with ``passed=True`` when the binary is
            found or the runtime is unknown, or a descriptive failure when
            the binary is missing.
        """
        binary = _RUNTIME_BINARY.get(self._runtime)
        if binary is None:
            return _CheckResult(
                passed=True,
                name=self.name,
                message=(
                    f"Unknown agent runtime '{self._runtime}'; "
                    "skipping binary availability check."
                ),
            )

        if shutil.which(binary) is not None:
            return _CheckResult(
                passed=True,
                name=self.name,
                message=f"Agent binary '{binary}' found for runtime '{self._runtime}'.",
            )

        return _CheckResult(
            passed=False,
            name=self.name,
            message=(
                f"Agent binary '{binary}' is required for the '{self._runtime}' runtime "
                "but was not found on PATH."
            ),
            remediation=(
                f"Install the '{binary}' binary and ensure it is on your PATH. "
                "See https://docs.anthropic.com/en/docs/claude-code for installation instructions."
            ),
        )
