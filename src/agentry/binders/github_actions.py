"""GitHubActionsBinder: environment binder for GitHub Actions CI execution.

Resolves workflow inputs from GitHub Actions environment variables and event
payloads, binds tool capabilities to CI-aware implementations, and maps outputs
to workflow step outputs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class GitHubActionsBinder:
    """Environment binder for GitHub Actions CI execution.

    Implements the :class:`~agentry.binders.protocol.EnvironmentBinder` protocol
    for running agents inside a GitHub Actions workflow.

    Reads configuration from the standard GitHub Actions environment variables
    that are automatically injected by the runner. Raises :exc:`ValueError` with
    actionable messages when required variables are absent.

    Attributes:
        name: Human-readable name used for logging and error messages.

    Args:
        env: Optional environment mapping override (defaults to :data:`os.environ`).
            Useful for testing without setting real environment variables.

    Raises:
        ValueError: If any required GitHub Actions environment variable is missing.
        ValueError: If ``GITHUB_EVENT_PATH`` points to a file that cannot be parsed
            as JSON.
    """

    name: str = "github-actions"

    # Required environment variables injected by the GitHub Actions runner.
    _REQUIRED_ENV_VARS: tuple[str, ...] = (
        "GITHUB_EVENT_NAME",
        "GITHUB_EVENT_PATH",
        "GITHUB_WORKSPACE",
        "GITHUB_REPOSITORY",
        "GITHUB_TOKEN",
    )

    def __init__(self, env: dict[str, str] | None = None) -> None:
        _env = env if env is not None else os.environ

        # Validate and read each required variable.
        self._event_name: str = self._require_env(
            "GITHUB_EVENT_NAME",
            "GITHUB_EVENT_NAME is not set. This variable is required when running "
            "in GitHub Actions and should be set automatically by the runner.",
            _env,
        )
        self._event_path: str = self._require_env(
            "GITHUB_EVENT_PATH",
            "GITHUB_EVENT_PATH is not set. This variable is required when running "
            "in GitHub Actions and should be set automatically by the runner.",
            _env,
        )
        self._workspace: str = self._require_env(
            "GITHUB_WORKSPACE",
            "GITHUB_WORKSPACE is not set. This variable is required when running "
            "in GitHub Actions and should be set automatically by the runner.",
            _env,
        )
        self._repository: str = self._require_env(
            "GITHUB_REPOSITORY",
            "GITHUB_REPOSITORY is not set. This variable is required when running "
            "in GitHub Actions and should be set automatically by the runner.",
            _env,
        )
        self._token: str = self._require_env(
            "GITHUB_TOKEN",
            "GITHUB_TOKEN is not set. This variable is required when running "
            "in GitHub Actions and should be set automatically by the runner.",
            _env,
        )

        # Parse the event payload JSON on construction.
        self._event_payload: dict[str, Any] = self._load_event_payload(
            self._event_path
        )

        # Extract PR number when the event is a pull_request event.
        self._pr_number: int | None = self._extract_pr_number(
            self._event_name, self._event_payload
        )

    # ------------------------------------------------------------------
    # EnvironmentBinder protocol — stubs (filled in by T01.2 and T02.x)
    # ------------------------------------------------------------------

    def resolve_inputs(
        self,
        input_declarations: dict[str, Any],
        provided_values: dict[str, str],
    ) -> dict[str, Any]:
        """Resolve abstract input declarations to concrete values.

        Not yet implemented; will be filled in by T01.2.

        Args:
            input_declarations: The workflow's input block (name -> input spec dict).
            provided_values: User-supplied values from ``--input key=value`` CLI args.

        Raises:
            NotImplementedError: Always. Implementation is deferred to T01.2.
        """
        raise NotImplementedError(
            "resolve_inputs() is not yet implemented for the GitHub Actions binder. "
            "This method will be implemented in T01.2."
        )

    def bind_tools(
        self,
        tool_declarations: list[str],
    ) -> dict[str, Any]:
        """Bind declared tool names to their GitHub Actions implementations.

        Not yet implemented; will be filled in by T02.1.

        Args:
            tool_declarations: Tool identifiers declared in the workflow.

        Raises:
            NotImplementedError: Always. Implementation is deferred to T02.1.
        """
        raise NotImplementedError(
            "bind_tools() is not yet implemented for the GitHub Actions binder. "
            "This method will be implemented in T02.1."
        )

    def map_outputs(
        self,
        output_declarations: dict[str, Any],
        target_dir: str,
        run_id: str,
    ) -> dict[str, str]:
        """Map output declarations to GitHub Actions step output paths.

        Not yet implemented; will be filled in by T02.2.

        Args:
            output_declarations: The workflow's output block.
            target_dir: Absolute path to the target directory.
            run_id: Timestamp-based identifier, e.g. ``"20260101T120000"``.

        Raises:
            NotImplementedError: Always. Implementation is deferred to T02.2.
        """
        raise NotImplementedError(
            "map_outputs() is not yet implemented for the GitHub Actions binder. "
            "This method will be implemented in T02.2."
        )

    def generate_pipeline_config(self) -> dict[str, Any]:
        """Generate CI pipeline configuration for GitHub Actions.

        Not yet implemented; will be filled in by T05.2.

        Raises:
            NotImplementedError: Always. Implementation is deferred to T05.2.
        """
        raise NotImplementedError(
            "generate_pipeline_config() is not yet implemented for the GitHub "
            "Actions binder. This method will be implemented in T05.2."
        )

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def event_name(self) -> str:
        """The name of the GitHub Actions event (e.g. ``"pull_request"``)."""
        return self._event_name

    @property
    def event_payload(self) -> dict[str, Any]:
        """The parsed JSON payload from ``GITHUB_EVENT_PATH``."""
        return self._event_payload

    @property
    def workspace(self) -> str:
        """The ``GITHUB_WORKSPACE`` path (repository root in the runner)."""
        return self._workspace

    @property
    def repository(self) -> str:
        """The ``GITHUB_REPOSITORY`` value (``owner/repo`` format)."""
        return self._repository

    @property
    def pr_number(self) -> int | None:
        """The pull request number extracted from the event payload.

        Returns:
            The PR number when :attr:`event_name` is ``"pull_request"``,
            or ``None`` for all other events.
        """
        return self._pr_number

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _require_env(name: str, message: str, env: dict[str, str]) -> str:
        """Read *name* from *env* or raise :exc:`ValueError` with *message*.

        Args:
            name: Environment variable name to look up.
            message: Actionable error message shown when the variable is absent.
            env: Environment mapping to read from.

        Returns:
            The environment variable value (guaranteed non-empty string).

        Raises:
            ValueError: If *name* is absent or has an empty value in *env*.
        """
        value = env.get(name, "")
        if not value:
            raise ValueError(message)
        return value

    @staticmethod
    def _load_event_payload(event_path: str) -> dict[str, Any]:
        """Read and parse the GitHub Actions event JSON payload.

        Args:
            event_path: Path to the event JSON file (from ``GITHUB_EVENT_PATH``).

        Returns:
            Parsed event payload as a dictionary.

        Raises:
            ValueError: If the file cannot be read or does not contain valid JSON.
        """
        path = Path(event_path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(
                f"Could not read GitHub Actions event file at {event_path!r}: {exc}. "
                "Ensure GITHUB_EVENT_PATH points to a valid, readable file."
            ) from exc
        try:
            payload: dict[str, Any] = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"GitHub Actions event file at {event_path!r} is not valid JSON: {exc}. "
                "This file should be provided automatically by the GitHub Actions runner."
            ) from exc
        return payload

    @staticmethod
    def _extract_pr_number(
        event_name: str, payload: dict[str, Any]
    ) -> int | None:
        """Extract the pull request number from a ``pull_request`` event payload.

        Args:
            event_name: The GitHub Actions event name.
            payload: The parsed event payload dictionary.

        Returns:
            The integer PR number when *event_name* is ``"pull_request"`` and
            the payload contains the expected fields, or ``None`` otherwise.
        """
        if event_name != "pull_request":
            return None
        pr_info = payload.get("pull_request", {})
        number = pr_info.get("number")
        if number is not None:
            return int(number)
        return None
