"""GitHubActionsBinder: environment binder for GitHub Actions CI execution.

Resolves workflow inputs from GitHub Actions environment variables and event
payloads, binds tool capabilities to CI-aware implementations, and maps outputs
to workflow step outputs.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import httpx

from agentry.binders.exceptions import UnsupportedToolError
from agentry.binders.local import _make_repository_read, _make_shell_execute

logger = logging.getLogger(__name__)

# Tools supported by the GitHub Actions binder.
SUPPORTED_TOOLS: frozenset[str] = frozenset(
    {
        "repository:read",
        "shell:execute",
        "pr:comment",
        "pr:review",
        "pr:create",
        "issue:comment",
        "issue:label",
    }
)


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
        self._event_payload: dict[str, Any] = self._load_event_payload(self._event_path)

        # Extract PR number when the event is a pull_request event.
        self._pr_number: int | None = self._extract_pr_number(self._event_name, self._event_payload)

        # Extract issue number when the event is an issues event.
        self._issue_number: int | None = self._extract_issue_number(
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

        Handles input types:

        - ``repository-ref``: Resolves to :attr:`workspace` (the
          ``GITHUB_WORKSPACE`` runner path).
        - ``git-diff``: Fetches the pull-request unified diff from the GitHub
          API using ``GET /repos/{owner}/{repo}/pulls/{number}`` with
          ``Accept: application/vnd.github.diff``.  The PR number comes from
          the event payload parsed on construction.
        - ``string``: Resolves in order of priority:
          1. ``provided_values[name]`` (explicit ``--input`` override).
          2. ``event_payload["inputs"][name]`` when the event is
             ``workflow_dispatch``.
          3. Dot-notation traversal of the event payload using the
             ``source`` key in the input spec (e.g. ``"issue.title"``).
          4. Optional inputs without a resolvable value return ``None``.

        Args:
            input_declarations: The workflow's input block (name -> input spec
                dict).  Each spec may contain ``type``, ``required``, and
                ``source`` keys.
            provided_values: User-supplied values from ``--input key=value``
                CLI args.

        Returns:
            A mapping of input name to resolved concrete value.

        Raises:
            ValueError: If a ``git-diff`` input is requested when the current
                event is not ``pull_request``.
            ValueError: If a required input cannot be resolved from the
                available event context.
        """
        resolved: dict[str, Any] = {}

        for name, spec in input_declarations.items():
            required = spec.get("required", False)
            input_type = spec.get("type", "string")

            if input_type == "repository-ref":
                resolved[name] = self._workspace
                continue

            if input_type == "git-diff":
                resolved[name] = self._resolve_git_diff(name)
                continue

            # ``string`` (and unknown types): multi-source resolution.
            value = self._resolve_string(name, spec, provided_values)
            if value is None and required:
                raise ValueError(
                    f"Required input {name!r} could not be resolved from the "
                    f"current GitHub Actions event context (event: "
                    f"{self._event_name!r}). "
                    "Provide a value via --input, a workflow_dispatch input, "
                    "or add a 'source' mapping to the input declaration."
                )
            resolved[name] = value

        return resolved

    # ------------------------------------------------------------------
    # Private helpers for resolve_inputs
    # ------------------------------------------------------------------

    def _resolve_git_diff(self, input_name: str) -> str:
        """Fetch the pull-request diff from the GitHub API.

        Args:
            input_name: The logical name of the input (used in error messages).

        Returns:
            The unified diff string for the pull request.

        Raises:
            ValueError: If the current event is not ``pull_request``.
            httpx.HTTPStatusError: If the GitHub API returns an error status.
        """
        if self._event_name != "pull_request" or self._pr_number is None:
            raise ValueError(
                f"Input {input_name!r} has type 'git-diff' but the current "
                f"event is {self._event_name!r}. "
                "git-diff inputs are only available for pull_request events."
            )

        owner_repo = self._repository  # "owner/repo" format
        url = f"https://api.github.com/repos/{owner_repo}/pulls/{self._pr_number}"
        response = httpx.get(
            url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github.diff",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        response.raise_for_status()
        return response.text

    def _resolve_string(
        self,
        name: str,
        spec: dict[str, Any],
        provided_values: dict[str, str],
    ) -> str | None:
        """Resolve a string input using the multi-source strategy.

        Resolution order:
        1. Explicit ``--input`` override via *provided_values*.
        2. ``workflow_dispatch`` event inputs payload field.
        3. Dot-notation ``source`` mapping against the event payload.
        4. Dot-notation ``fallback`` mapping when source resolves to null/empty.

        Args:
            name: Input name.
            spec: Input declaration spec dict.
            provided_values: Caller-supplied explicit values.

        Returns:
            The resolved string, or ``None`` if no source applies.
        """
        # 1. Explicit provided value wins.
        if name in provided_values:
            return provided_values[name]

        # 2. workflow_dispatch: check event payload inputs.
        if self._event_name == "workflow_dispatch":
            dispatch_inputs: dict[str, Any] = self._event_payload.get("inputs", {})
            if name in dispatch_inputs:
                return str(dispatch_inputs[name])

        # 3. Dot-notation source mapping.
        source: str | None = spec.get("source")
        if source is not None:
            value = self._traverse_payload(source)
            if value is not None and str(value):
                return str(value)

            # 4. Fallback when source resolves to null or empty string.
            fallback: str | None = spec.get("fallback")
            if fallback is not None:
                fallback_value = self._traverse_payload(fallback)
                if fallback_value is not None and str(fallback_value):
                    logger.warning(
                        "Input %r: source %r resolved to null/empty; "
                        "falling back to %r (value: %r).",
                        name,
                        source,
                        fallback,
                        fallback_value,
                    )
                    return str(fallback_value)

        return None

    def _traverse_payload(self, dotpath: str) -> Any:
        """Traverse the event payload using dot-notation.

        Args:
            dotpath: Dot-separated key path, e.g. ``"issue.title"``.

        Returns:
            The value at the given path, or ``None`` if any key is missing.
        """
        current: Any = self._event_payload
        for key in dotpath.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(key)
            if current is None:
                return None
        return current

    def bind_tools(
        self,
        tool_declarations: list[str],
    ) -> dict[str, Any]:
        """Bind declared tool names to their GitHub Actions implementations.

        Wires the following tools to CI-aware implementations:

        - ``repository:read``: Reads files from :attr:`workspace`
          (``$GITHUB_WORKSPACE``) with path traversal protection.
        - ``shell:execute``: Enforces the same read-only command allowlist as
          :class:`~agentry.binders.local.LocalBinder`.
        - ``pr:comment``: Posts a comment to the current pull request via the
          GitHub REST API (``POST /repos/{owner}/{repo}/issues/{number}/comments``).
        - ``pr:review``: Creates a review on the current pull request via the
          GitHub REST API (``POST /repos/{owner}/{repo}/pulls/{number}/reviews``).
        - ``pr:create``: Creates a branch, commits files, and opens a pull request
          via the GitHub REST API.  Enforces safety guardrails: no force-push,
          no push to protected branches, and no auto-merge.
        - ``issue:comment``: Posts a comment to the current issue via the GitHub
          REST API (``POST /repos/{owner}/{repo}/issues/{number}/comments``).
          Requires an ``issues`` event context.
        - ``issue:label``: Adds labels to the current issue via the GitHub REST
          API (``POST /repos/{owner}/{repo}/issues/{number}/labels``).  Requires
          an ``issues`` event context.

        Args:
            tool_declarations: Tool identifiers declared in the workflow
                (e.g. ``["repository:read", "issue:comment"]``).

        Returns:
            Mapping of tool name to a concrete callable implementation.

        Raises:
            UnsupportedToolError: If any declared tool is not in
                :data:`SUPPORTED_TOOLS`.
        """
        bindings: dict[str, Any] = {}
        for tool_name in tool_declarations:
            if tool_name not in SUPPORTED_TOOLS:
                raise UnsupportedToolError(tool_name, self.name)
            if tool_name == "repository:read":
                # Root the repository read at GITHUB_WORKSPACE.
                workspace = self._workspace
                _reader = _make_repository_read()

                def _repository_read_ci(
                    *, path: str, _workspace: str = workspace, _reader: Any = _reader
                ) -> str:
                    return cast(str, _reader(repo_root=_workspace, path=path))

                _repository_read_ci.__name__ = "repository_read"
                bindings[tool_name] = _repository_read_ci
            elif tool_name == "shell:execute":
                bindings[tool_name] = _make_shell_execute()
            elif tool_name == "pr:comment":
                bindings[tool_name] = self._make_pr_comment()
            elif tool_name == "pr:review":
                bindings[tool_name] = self._make_pr_review()
            elif tool_name == "pr:create":
                bindings[tool_name] = self._make_pr_create()
            elif tool_name == "issue:comment":
                bindings[tool_name] = self._make_issue_comment()
            elif tool_name == "issue:label":
                bindings[tool_name] = self._make_issue_label()
        return bindings

    # ------------------------------------------------------------------
    # Private helpers for bind_tools
    # ------------------------------------------------------------------

    def _make_pr_comment(self) -> Any:
        """Return a callable that posts a PR comment via the GitHub API.

        The callable signature is::

            def pr_comment(*, body: str) -> dict[str, Any]: ...

        Args:
            body: The comment body text (Markdown supported).

        Returns:
            The parsed JSON response from the GitHub API.

        Raises:
            ValueError: If the current event is not a pull_request (no PR number).
            RuntimeError: On GitHub API errors with HTTP status, body snippet, and
                remediation hint.
        """
        repository = self._repository
        token = self._token
        pr_number = self._pr_number

        def pr_comment(*, body: str) -> dict[str, Any]:
            if pr_number is None:
                raise ValueError(
                    "pr:comment requires a pull_request event, but the current "
                    "event does not have a PR number."
                )
            url = f"https://api.github.com/repos/{repository}/issues/{pr_number}/comments"
            try:
                response = httpx.post(
                    url,
                    json={"body": body},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
                response.raise_for_status()
            except httpx.TimeoutException as exc:
                raise RuntimeError(
                    "Network timeout while posting PR comment to GitHub API. "
                    "Check your network connection or increase the timeout."
                ) from exc
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                body_snippet = exc.response.text[:200]
                if status == 403:
                    remediation = (
                        "GITHUB_TOKEN may lack `pull_requests:write` scope. "
                        "Ensure the workflow has `pull-requests: write` permissions."
                    )
                elif status == 404:
                    remediation = (
                        f"PR #{pr_number} not found in repository {repository!r}. "
                        "Verify the repository name and PR number are correct."
                    )
                else:
                    remediation = "Check GitHub API status and token permissions."
                raise RuntimeError(
                    f"{status} error posting PR comment: {body_snippet}. {remediation}"
                ) from exc
            return cast(dict[str, Any], response.json())

        pr_comment.__name__ = "pr_comment"
        return pr_comment

    def _make_pr_review(self) -> Any:
        """Return a callable that creates a PR review via the GitHub API.

        The callable signature is::

            def pr_review(*, body: str, event: str = "COMMENT",
                          comments: list[dict[str, Any]] | None = None) -> dict[str, Any]: ...

        Args:
            body: The review body text.
            event: One of ``"APPROVE"``, ``"REQUEST_CHANGES"``, ``"COMMENT"``.
            comments: Optional list of inline review comments.

        Returns:
            The parsed JSON response from the GitHub API.

        Raises:
            ValueError: If the current event is not a pull_request (no PR number).
            RuntimeError: On GitHub API errors with HTTP status, body snippet, and
                remediation hint.
        """
        repository = self._repository
        token = self._token
        pr_number = self._pr_number

        def pr_review(
            *,
            body: str,
            event: str = "COMMENT",
            comments: list[dict[str, Any]] | None = None,
        ) -> dict[str, Any]:
            if pr_number is None:
                raise ValueError(
                    "pr:review requires a pull_request event, but the current "
                    "event does not have a PR number."
                )
            url = f"https://api.github.com/repos/{repository}/pulls/{pr_number}/reviews"
            payload: dict[str, Any] = {"body": body, "event": event}
            if comments is not None:
                payload["comments"] = comments
            try:
                response = httpx.post(
                    url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
                response.raise_for_status()
            except httpx.TimeoutException as exc:
                raise RuntimeError(
                    "Network timeout while creating PR review via GitHub API. "
                    "Check your network connection or increase the timeout."
                ) from exc
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                body_snippet = exc.response.text[:200]
                if status == 403:
                    remediation = (
                        "GITHUB_TOKEN may lack `pull_requests:write` scope. "
                        "Ensure the workflow has `pull-requests: write` permissions."
                    )
                elif status == 404:
                    remediation = (
                        f"PR #{pr_number} not found in repository {repository!r}. "
                        "Verify the repository name and PR number are correct."
                    )
                else:
                    remediation = "Check GitHub API status and token permissions."
                raise RuntimeError(
                    f"{status} error creating PR review: {body_snippet}. {remediation}"
                ) from exc
            return cast(dict[str, Any], response.json())

        pr_review.__name__ = "pr_review"
        return pr_review

    def _make_pr_create(self) -> Any:
        """Return a callable that creates a branch and opens a PR via the GitHub API.

        The callable creates a new branch ref, commits files using the Git data API,
        opens a pull request, and adds the ``agent-proposed`` label.  It enforces
        safety guardrails: no force-push, no push to protected branches, and no
        auto-merge.

        The callable signature is::

            def pr_create(
                *,
                branch_name: str,
                commit_message: str,
                base_branch: str = "main",
                title: str,
                body: str,
                label: str = "agent-proposed",
                files: list[str] | None = None,
            ) -> dict[str, Any]: ...

        Returns:
            A dict with ``branch``, ``pr_url``, and ``status`` keys on success,
            or ``branch``, ``error``, and ``status`` keys on failure.

        Raises:
            ValueError: If *branch_name* matches a protected branch name.
            RuntimeError: On GitHub API errors with HTTP status, body snippet, and
                remediation hint.
        """
        repository = self._repository
        token = self._token
        workspace = self._workspace

        # Branch names that must never be pushed to directly.
        protected_branches = frozenset({"main", "master"})

        def pr_create(
            *,
            branch_name: str,
            commit_message: str,
            base_branch: str = "main",
            title: str,
            body: str,
            label: str = "agent-proposed",
            files: list[str] | None = None,
        ) -> dict[str, Any]:
            # Guard: never push to a protected branch.
            if branch_name in protected_branches:
                raise ValueError(
                    f"Cannot create a PR from protected branch {branch_name!r}. "
                    "Use a feature branch name instead."
                )

            api_base = f"https://api.github.com/repos/{repository}"
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }

            try:
                # 1. Get the SHA of the base branch.
                ref_url = f"{api_base}/git/ref/heads/{base_branch}"
                ref_resp = httpx.get(ref_url, headers=headers)
                ref_resp.raise_for_status()
                base_sha: str = ref_resp.json()["object"]["sha"]

                # 2. If files are specified, create blobs and a tree for the commit.
                tree_sha = base_sha
                if files is not None:
                    tree_items: list[dict[str, str]] = []
                    for file_path in files:
                        full_path = Path(workspace) / file_path
                        content = full_path.read_text(encoding="utf-8")
                        blob_resp = httpx.post(
                            f"{api_base}/git/blobs",
                            json={"content": content, "encoding": "utf-8"},
                            headers=headers,
                        )
                        blob_resp.raise_for_status()
                        blob_sha: str = blob_resp.json()["sha"]
                        tree_items.append(
                            {
                                "path": file_path,
                                "mode": "100644",
                                "type": "blob",
                                "sha": blob_sha,
                            }
                        )
                    tree_resp = httpx.post(
                        f"{api_base}/git/trees",
                        json={"base_tree": base_sha, "tree": tree_items},
                        headers=headers,
                    )
                    tree_resp.raise_for_status()
                    tree_sha = tree_resp.json()["sha"]

                # 3. Create a commit.
                commit_payload: dict[str, Any] = {
                    "message": commit_message,
                    "tree": tree_sha,
                    "parents": [base_sha],
                }
                commit_resp = httpx.post(
                    f"{api_base}/git/commits",
                    json=commit_payload,
                    headers=headers,
                )
                commit_resp.raise_for_status()
                commit_sha: str = commit_resp.json()["sha"]

                # 4. Create the branch ref (never force-push).
                create_ref_resp = httpx.post(
                    f"{api_base}/git/refs",
                    json={"ref": f"refs/heads/{branch_name}", "sha": commit_sha},
                    headers=headers,
                )
                create_ref_resp.raise_for_status()

                # 5. Open a pull request (never auto-merge).
                pr_resp = httpx.post(
                    f"{api_base}/pulls",
                    json={
                        "title": title,
                        "body": body,
                        "head": branch_name,
                        "base": base_branch,
                    },
                    headers=headers,
                )
                pr_resp.raise_for_status()
                pr_data = pr_resp.json()
                pr_url: str = pr_data.get("html_url", "")
                pr_number: int = pr_data["number"]

                # 6. Add label.
                httpx.post(
                    f"{api_base}/issues/{pr_number}/labels",
                    json={"labels": [label]},
                    headers=headers,
                )

                return {
                    "branch": branch_name,
                    "pr_url": pr_url,
                    "status": "created",
                }

            except httpx.TimeoutException as exc:
                raise RuntimeError(
                    "Network timeout while creating PR via GitHub API. "
                    "Check your network connection or increase the timeout."
                ) from exc
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                body_snippet = exc.response.text[:200]
                if status == 403:
                    remediation = (
                        "GITHUB_TOKEN may lack required scopes. "
                        "Ensure the workflow has `contents: write` and "
                        "`pull-requests: write` permissions."
                    )
                elif status == 404:
                    remediation = (
                        f"Resource not found in repository {repository!r}. "
                        "Verify the repository name and branch names are correct."
                    )
                elif status == 422:
                    remediation = (
                        "Validation failed. The branch may already exist or "
                        "the PR title/body may be invalid."
                    )
                else:
                    remediation = "Check GitHub API status and token permissions."
                raise RuntimeError(
                    f"{status} error creating PR: {body_snippet}. {remediation}"
                ) from exc

        pr_create.__name__ = "pr_create"
        return pr_create

    def _make_issue_comment(self) -> Any:
        """Return a callable that posts a comment to a GitHub issue via the API.

        The callable signature is::

            def issue_comment(*, body: str) -> dict[str, Any]: ...

        Args:
            body: The comment body text (Markdown supported).

        Returns:
            The parsed JSON response from the GitHub API.

        Raises:
            ValueError: If the current event is not an ``issues`` event (no issue
                number available).
            RuntimeError: On GitHub API errors with HTTP status, body snippet, and
                remediation hint.
        """
        repository = self._repository
        token = self._token
        issue_number = self._issue_number

        def issue_comment(*, body: str) -> dict[str, Any]:
            if issue_number is None:
                raise ValueError(
                    "issue:comment requires an issues event, but the current "
                    "event does not have an issue number."
                )
            url = f"https://api.github.com/repos/{repository}/issues/{issue_number}/comments"
            try:
                response = httpx.post(
                    url,
                    json={"body": body},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
                response.raise_for_status()
            except httpx.TimeoutException as exc:
                raise RuntimeError(
                    "Network timeout while posting issue comment to GitHub API. "
                    "Check your network connection or increase the timeout."
                ) from exc
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                body_snippet = exc.response.text[:200]
                if status == 403:
                    remediation = (
                        "GITHUB_TOKEN may lack `issues:write` scope. "
                        "Ensure the workflow has `issues: write` permissions."
                    )
                elif status == 404:
                    remediation = (
                        f"Issue #{issue_number} not found in repository "
                        f"{repository!r}. "
                        "Verify the repository name and issue number are correct."
                    )
                else:
                    remediation = "Check GitHub API status and token permissions."
                raise RuntimeError(
                    f"{status} error posting issue comment: {body_snippet}. {remediation}"
                ) from exc
            return cast(dict[str, Any], response.json())

        issue_comment.__name__ = "issue_comment"
        return issue_comment

    def _make_issue_label(self) -> Any:
        """Return a callable that adds labels to a GitHub issue via the API.

        The callable signature is::

            def issue_label(*, labels: list[str]) -> dict[str, Any]: ...

        Args:
            labels: List of label names to add to the issue.

        Returns:
            The parsed JSON response from the GitHub API.

        Raises:
            ValueError: If the current event is not an ``issues`` event (no issue
                number available).
            RuntimeError: On GitHub API errors with HTTP status, body snippet, and
                remediation hint.
        """
        repository = self._repository
        token = self._token
        issue_number = self._issue_number

        def issue_label(*, labels: list[str]) -> dict[str, Any]:
            if issue_number is None:
                raise ValueError(
                    "issue:label requires an issues event, but the current "
                    "event does not have an issue number."
                )
            url = f"https://api.github.com/repos/{repository}/issues/{issue_number}/labels"
            try:
                response = httpx.post(
                    url,
                    json={"labels": labels},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
                response.raise_for_status()
            except httpx.TimeoutException as exc:
                raise RuntimeError(
                    "Network timeout while adding labels to issue via GitHub API. "
                    "Check your network connection or increase the timeout."
                ) from exc
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                body_snippet = exc.response.text[:200]
                if status == 403:
                    remediation = (
                        "GITHUB_TOKEN may lack `issues:write` scope. "
                        "Ensure the workflow has `issues: write` permissions."
                    )
                elif status == 404:
                    remediation = (
                        f"Issue #{issue_number} not found in repository "
                        f"{repository!r}. "
                        "Verify the repository name and issue number are correct."
                    )
                elif status == 422:
                    remediation = (
                        "Validation failed. One or more label names may be invalid "
                        "or not exist in the repository."
                    )
                else:
                    remediation = "Check GitHub API status and token permissions."
                raise RuntimeError(
                    f"{status} error adding issue labels: {body_snippet}. {remediation}"
                ) from exc
            return cast(dict[str, Any], response.json())

        issue_label.__name__ = "issue_label"
        return issue_label

    def map_outputs(
        self,
        output_declarations: dict[str, Any],
        target_dir: str,
        run_id: str,
    ) -> dict[str, str]:
        """Map output declarations to GitHub Actions step output paths.

        Writes the agent output to ``$GITHUB_WORKSPACE/.agentry/runs/<run_id>/output.json``
        and, when the current event is a pull request, also posts the output as a
        PR comment via the GitHub REST API.

        The ``target_dir`` parameter is accepted for protocol compatibility but the
        actual output is always rooted at ``$GITHUB_WORKSPACE`` in CI.

        Args:
            output_declarations: The workflow's output block.  May contain an
                ``output_paths`` list of additional file names to include in the
                mapping.
            target_dir: Accepted for protocol compatibility (ignored in CI; the
                GitHub Actions binder always uses ``$GITHUB_WORKSPACE``).
            run_id: Timestamp-based identifier, e.g. ``"20260101T120000"``.

        Returns:
            Mapping of logical output name to absolute path string, always
            including ``"output"`` and ``"execution_record"`` keys.

        Raises:
            RuntimeError: On GitHub API errors when attempting to post the PR
                comment.  Error messages include the HTTP status, a body snippet,
                and a suggested remediation.
        """
        # Always root output at GITHUB_WORKSPACE so CI paths are deterministic.
        runs_dir = Path(self._workspace) / ".agentry" / "runs" / run_id
        runs_dir.mkdir(parents=True, exist_ok=True)

        output_path = runs_dir / "output.json"
        execution_record_path = runs_dir / "execution-record.json"

        paths: dict[str, str] = {
            "output": str(output_path),
            "execution_record": str(execution_record_path),
        }

        # Preserve any extra declared output paths.
        for declared_path in output_declarations.get("output_paths", []):
            name = Path(declared_path).stem
            paths[name] = str(runs_dir / Path(declared_path).name)

        # When the event is a pull request, post the output as a PR comment.
        if self._pr_number is not None:
            comment_body = self._format_output_comment(output_path)
            self._post_output_comment(comment_body)

        # When the event is an issue, post the triage output as an issue comment
        # and apply severity/category labels (best-effort).
        if self._issue_number is not None:
            comment_body = self._format_triage_comment(output_path)
            self._post_issue_comment(comment_body)
            self._apply_triage_labels(output_path)

        return paths

    def _format_output_comment(self, output_path: Path) -> str:
        """Format the agent output as a readable PR comment.

        Reads the output JSON file and formats findings, summary, and
        metadata into a Markdown comment. Falls back to raw JSON if the
        output structure is unrecognised.
        """
        if not output_path.exists():
            return f"Agent run output: {output_path} (file not found)"

        try:
            raw = output_path.read_text(encoding="utf-8")
        except OSError:
            return f"Agent run output: {output_path} (read error)"

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return f"**Agent Output**\n\n```\n{raw[:3000]}\n```"

        agent_output = data.get("output") or {}
        parts: list[str] = ["## Agentry Code Review\n"]

        # Summary
        summary = agent_output.get("summary", "")
        if summary:
            parts.append(f"{summary}\n")

        # Findings
        findings = agent_output.get("findings", [])
        if findings:
            parts.append(f"### Findings ({len(findings)})\n")
            for f in findings:
                severity = f.get("severity", "info")
                emoji = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(severity, "⚪")
                file_ = f.get("file", "")
                line = f.get("line", "")
                desc = f.get("description", "")
                suggestion = f.get("suggestion", "")
                category = f.get("category", "")
                loc = f"`{file_}:{line}`" if file_ else ""
                parts.append(f"{emoji} **{severity}** ({category}) {loc}")
                parts.append(f"  {desc}")
                if suggestion:
                    parts.append(f"  > 💡 {suggestion}")
                parts.append("")

        # Confidence
        confidence = agent_output.get("confidence")
        if confidence is not None:
            parts.append(f"**Confidence:** {confidence}")

        # Raw response fallback
        raw_response = agent_output.get("raw_response", "")
        if raw_response and not findings and not summary:
            parts.append(f"```\n{raw_response[:3000]}\n```")

        # Token usage
        usage = data.get("token_usage", {})
        if usage:
            _in = usage.get("input_tokens", 0)
            _out = usage.get("output_tokens", 0)
            parts.append(f"\n---\n*Tokens: {_in:,} in / {_out:,} out*")

        return "\n".join(parts)

    def _post_output_comment(self, body: str) -> dict[str, Any]:
        """Post agent output as a PR comment via the GitHub REST API.

        Args:
            body: The comment body text (Markdown supported).

        Returns:
            The parsed JSON response from the GitHub API.

        Raises:
            RuntimeError: On GitHub API errors with HTTP status, response body
                snippet, and remediation hint.
        """
        url = f"https://api.github.com/repos/{self._repository}/issues/{self._pr_number}/comments"
        try:
            response = httpx.post(
                url,
                json={"body": body},
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise RuntimeError(
                "Network timeout while posting output PR comment to GitHub API. "
                "Check your network connection or increase the timeout."
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            body_snippet = exc.response.text[:200]
            if status == 403:
                remediation = (
                    "GITHUB_TOKEN may lack `pull_requests:write` scope. "
                    "Ensure the workflow has `pull-requests: write` permissions."
                )
            elif status == 404:
                remediation = (
                    f"PR #{self._pr_number} not found in repository "
                    f"{self._repository!r}. "
                    "Verify the repository name and PR number are correct."
                )
            else:
                remediation = "Check GitHub API status and token permissions."
            raise RuntimeError(
                f"{status} error posting output PR comment: {body_snippet}. {remediation}"
            ) from exc
        return cast(dict[str, Any], response.json())

    def _format_triage_comment(self, output_path: Path) -> str:
        """Format triage output as a readable issue comment.

        Reads the output JSON file and formats severity, category, affected
        components, recommended assignee, and reasoning into a Markdown comment.
        Falls back to raw JSON if the output structure is unrecognised.

        Args:
            output_path: Path to the ``output.json`` file written by the agent.

        Returns:
            Formatted Markdown string suitable for posting as an issue comment.
        """
        if not output_path.exists():
            return f"Triage output: {output_path} (file not found)"

        try:
            raw = output_path.read_text(encoding="utf-8")
        except OSError:
            return f"Triage output: {output_path} (read error)"

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return f"**Triage Output**\n\n```\n{raw[:3000]}\n```"

        agent_output = data.get("output") or {}
        parts: list[str] = ["## Agentry Issue Triage\n"]

        # Severity badge
        severity: str = agent_output.get("severity", "")
        if severity:
            severity_badge = {
                "critical": "![critical](https://img.shields.io/badge/severity-critical-red)",
                "high": "![high](https://img.shields.io/badge/severity-high-orange)",
                "medium": "![medium](https://img.shields.io/badge/severity-medium-yellow)",
                "low": "![low](https://img.shields.io/badge/severity-low-green)",
            }.get(severity, f"**Severity:** {severity}")
            parts.append(f"{severity_badge}\n")

        # Category
        category: str = agent_output.get("category", "")
        if category:
            parts.append(f"**Category:** {category}\n")

        # Affected components
        components: list[str] = agent_output.get("affected_components", [])
        if components:
            parts.append("**Affected Components:**")
            for component in components:
                parts.append(f"- {component}")
            parts.append("")

        # Recommended assignee
        assignee: str = agent_output.get("recommended_assignee", "")
        if assignee:
            parts.append(f"**Recommended Assignee:** {assignee}\n")

        # Reasoning
        reasoning: str = agent_output.get("reasoning", "")
        if reasoning:
            parts.append("**Reasoning:**")
            parts.append(f"{reasoning}\n")

        # Raw response fallback when no structured data is present.
        if not severity and not category and not reasoning:
            raw_response: str = agent_output.get("raw_response", "")
            if raw_response:
                parts.append(f"```\n{raw_response[:3000]}\n```")

        # Token usage
        usage = data.get("token_usage", {})
        if usage:
            _in = usage.get("input_tokens", 0)
            _out = usage.get("output_tokens", 0)
            parts.append(f"\n---\n*Tokens: {_in:,} in / {_out:,} out*")

        return "\n".join(parts)

    def _post_issue_comment(self, body: str) -> dict[str, Any]:
        """Post triage output as an issue comment via the GitHub REST API.

        Args:
            body: The comment body text (Markdown supported).

        Returns:
            The parsed JSON response from the GitHub API.

        Raises:
            RuntimeError: On GitHub API errors with HTTP status, response body
                snippet, and remediation hint.
        """
        url = (
            f"https://api.github.com/repos/{self._repository}/issues/{self._issue_number}/comments"
        )
        try:
            response = httpx.post(
                url,
                json={"body": body},
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise RuntimeError(
                "Network timeout while posting triage issue comment to GitHub API. "
                "Check your network connection or increase the timeout."
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            body_snippet = exc.response.text[:200]
            if status == 403:
                remediation = (
                    "GITHUB_TOKEN may lack `issues:write` scope. "
                    "Ensure the workflow has `issues: write` permissions."
                )
            elif status == 404:
                remediation = (
                    f"Issue #{self._issue_number} not found in repository "
                    f"{self._repository!r}. "
                    "Verify the repository name and issue number are correct."
                )
            else:
                remediation = "Check GitHub API status and token permissions."
            raise RuntimeError(
                f"{status} error posting triage issue comment: {body_snippet}. {remediation}"
            ) from exc
        return cast(dict[str, Any], response.json())

    def _apply_triage_labels(self, output_path: Path) -> None:
        """Apply severity and category labels to the issue (best-effort).

        Reads the agent output JSON to extract ``severity`` and ``category``
        fields, then calls the GitHub REST API to add labels in the form
        ``severity:{value}`` and ``category:{value}``.

        Label application is best-effort: any errors are logged as warnings
        and do not propagate to the caller.

        Args:
            output_path: Path to the ``output.json`` file written by the agent.
        """
        if not output_path.exists():
            logger.warning(
                "Skipping label application: output file %s does not exist.",
                output_path,
            )
            return

        try:
            raw = output_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "Skipping label application: could not parse output file %s: %s",
                output_path,
                exc,
            )
            return

        agent_output = data.get("output") or {}
        labels: list[str] = []

        severity = agent_output.get("severity", "")
        if severity:
            labels.append(f"severity:{severity}")

        category = agent_output.get("category", "")
        if category:
            labels.append(f"category:{category}")

        if not labels:
            logger.warning(
                "Skipping label application: no severity or category found in triage output."
            )
            return

        url = f"https://api.github.com/repos/{self._repository}/issues/{self._issue_number}/labels"
        try:
            response = httpx.post(
                url,
                json={"labels": labels},
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            response.raise_for_status()
            logger.info("Applied triage labels %r to issue #%s.", labels, self._issue_number)
        except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning(
                "Failed to apply triage labels %r to issue #%s: %s. "
                "Label application is best-effort; workflow continues.",
                labels,
                self._issue_number,
                exc,
            )

    def generate_pipeline_config(
        self,
        workflow_name: str = "agentry-workflow",
        triggers: list[str] | None = None,
        schedule: str | None = None,
        tool_declarations: list[str] | None = None,
        workflow_path: str | None = None,
    ) -> dict[str, Any]:
        """Generate CI pipeline configuration for GitHub Actions.

        Builds a structured dict representing a GitHub Actions workflow that
        runs an Agentry workflow in CI.  The returned dict can be serialized
        directly to YAML (e.g. via :func:`yaml.dump`) to produce a
        committable ``.github/workflows/`` file.

        Args:
            workflow_name: Human-readable name for the workflow, embedded in
                the generated ``name`` field as ``"Agentry: <workflow_name>"``.
                Defaults to ``"agentry-workflow"``.
            triggers: List of GitHub Actions event names that should trigger the
                workflow.  Supported values: ``"pull_request"``, ``"push"``,
                ``"schedule"``, ``"issues"``.  Defaults to
                ``["pull_request"]`` when not provided.
            schedule: Cron expression used when ``"schedule"`` is in *triggers*
                (e.g. ``"0 2 * * 1"``).  Ignored when ``"schedule"`` is not
                present in *triggers*.
            tool_declarations: List of tool capability strings used to derive
                the minimal GitHub Actions ``permissions`` block.  Maps tool
                names to required permission scopes — ``pr:comment`` and
                ``pr:review`` require ``pull-requests: write``; all configs
                always include ``contents: read``.  Defaults to ``[]``.
            workflow_path: Optional path to the workflow YAML file, included in
                the ``agentry run`` command in the generated ``run`` step.
                Defaults to ``"workflow.yaml"`` when not provided.

        Returns:
            A dict with the following top-level keys:

            - ``name`` (str): ``"Agentry: <workflow_name>"``
            - ``on`` (dict): GitHub Actions trigger configuration.
            - ``permissions`` (dict): Minimal permission scope mapping.
            - ``env`` (dict): Environment variables for the ``agentry`` job,
              including ``ANTHROPIC_API_KEY`` and ``GITHUB_TOKEN`` from secrets.
            - ``jobs`` (dict): Single ``agentry`` job with ``runs-on`` and
              ``steps`` (checkout, setup-python, install, run).
        """
        if triggers is None:
            triggers = ["pull_request"]
        if tool_declarations is None:
            tool_declarations = []
        if workflow_path is None:
            workflow_path = "workflow.yaml"

        # Build the on: trigger block.
        on_block: dict[str, Any] = {}
        for trigger in triggers:
            if trigger == "schedule":
                on_block["schedule"] = [{"cron": schedule}]
            elif trigger == "pull_request":
                on_block["pull_request"] = {}
            elif trigger == "push":
                on_block["push"] = {}
            elif trigger == "issues":
                on_block["issues"] = {"types": ["opened", "edited"]}
            else:
                on_block[trigger] = {}

        # Derive minimal permissions from tool declarations.
        permissions: dict[str, str] = {"contents": "read"}
        tool_perm_map: dict[str, dict[str, str]] = {
            "pr:comment": {"pull-requests": "write"},
            "pr:review": {"pull-requests": "write"},
            "pr:": {"pull-requests": "write"},
            "issue:": {"issues": "write"},
            "repository:read": {"contents": "read"},
            "repository:write": {"contents": "write"},
            "repository:": {"contents": "read"},
        }
        for capability in tool_declarations:
            for prefix, perm in tool_perm_map.items():
                if capability == prefix or capability.startswith(prefix):
                    for scope, level in perm.items():
                        existing = permissions.get(scope)
                        if existing is None or (existing == "read" and level == "write"):
                            permissions[scope] = level

        # Build the steps list.
        steps: list[dict[str, Any]] = [
            {
                "name": "Checkout repository",
                "uses": "actions/checkout@v4",
            },
            {
                "name": "Set up Python",
                "uses": "actions/setup-python@v5",
                "with": {"python-version": "3.12"},
            },
            {
                "name": "Install agentry",
                "run": "pip install agentry",
            },
            {
                "name": "Run agentry",
                "run": f"agentry run {workflow_path}",
                "env": {
                    "ANTHROPIC_API_KEY": "${{ secrets.ANTHROPIC_API_KEY }}",
                    "GITHUB_TOKEN": "${{ secrets.GITHUB_TOKEN }}",
                },
            },
        ]

        return {
            "name": f"Agentry: {workflow_name}",
            "on": on_block,
            "permissions": permissions,
            "env": {
                "ANTHROPIC_API_KEY": "${{ secrets.ANTHROPIC_API_KEY }}",
                "GITHUB_TOKEN": "${{ secrets.GITHUB_TOKEN }}",
            },
            "jobs": {
                "agentry": {
                    "runs-on": "ubuntu-latest",
                    "steps": steps,
                },
            },
        }

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
    def _require_env(name: str, message: str, env: Mapping[str, str]) -> str:
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
    def _extract_pr_number(event_name: str, payload: dict[str, Any]) -> int | None:
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

    @staticmethod
    def _extract_issue_number(event_name: str, payload: dict[str, Any]) -> int | None:
        """Extract the issue number from an ``issues`` event payload.

        Args:
            event_name: The GitHub Actions event name.
            payload: The parsed event payload dictionary.

        Returns:
            The integer issue number when *event_name* is ``"issues"`` and
            the payload contains the expected fields, or ``None`` otherwise.
        """
        if event_name != "issues":
            return None
        issue_info = payload.get("issue", {})
        number = issue_info.get("number")
        if number is not None:
            return int(number)
        return None
