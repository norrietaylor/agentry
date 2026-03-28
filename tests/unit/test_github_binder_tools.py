"""Unit tests for T02.3: GitHubActionsBinder bind_tools() and map_outputs().

Tests cover:
- bind_tools for repository:read: path traversal protection with GITHUB_WORKSPACE root
- bind_tools for shell:execute: allowlist enforcement
- bind_tools for pr:comment: mock httpx, correct API URL and payload
- bind_tools for pr:review: mock httpx, correct API URL and payload
- UnsupportedToolError for unknown tool names
- API error handling: 403 scope error message, 404 not found, network timeout
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from agentry.binders.exceptions import (
    CommandNotAllowedError,
    PathTraversalError,
    UnsupportedToolError,
)
from agentry.binders.github_actions import SUPPORTED_TOOLS, GitHubActionsBinder

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_env(
    tmp_path: Path,
    event_name: str = "push",
    payload: dict[str, Any] | None = None,
    token: str = "ghp_testtoken",
    workspace: str | None = None,
    repository: str = "owner/repo",
) -> dict[str, str]:
    """Build a minimal GitHub Actions environment dict for testing."""
    if payload is None:
        payload = {}
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(payload), encoding="utf-8")
    ws = workspace or str(tmp_path / "workspace")
    Path(ws).mkdir(parents=True, exist_ok=True)
    return {
        "GITHUB_EVENT_NAME": event_name,
        "GITHUB_EVENT_PATH": str(event_file),
        "GITHUB_WORKSPACE": ws,
        "GITHUB_REPOSITORY": repository,
        "GITHUB_TOKEN": token,
    }


def _make_pr_env(
    tmp_path: Path,
    pr_number: int = 42,
    token: str = "ghp_testtoken",
    repository: str = "owner/repo",
) -> dict[str, str]:
    """Build a pull_request environment dict."""
    payload = {"pull_request": {"number": pr_number}}
    return _make_env(
        tmp_path,
        event_name="pull_request",
        payload=payload,
        token=token,
        repository=repository,
    )


@pytest.fixture()
def push_env(tmp_path: Path) -> dict[str, str]:
    """Environment for a push event."""
    return _make_env(tmp_path, event_name="push")


@pytest.fixture()
def pr_env(tmp_path: Path) -> dict[str, str]:
    """Environment for a pull_request event with PR #42."""
    return _make_pr_env(tmp_path)


@pytest.fixture()
def binder_push(push_env: dict[str, str]) -> GitHubActionsBinder:
    """GitHubActionsBinder instantiated for a push event."""
    return GitHubActionsBinder(env=push_env)


@pytest.fixture()
def binder_pr(pr_env: dict[str, str]) -> GitHubActionsBinder:
    """GitHubActionsBinder instantiated for a pull_request event."""
    return GitHubActionsBinder(env=pr_env)


def _mock_httpx_post_success(body: dict[str, Any] | None = None) -> MagicMock:
    """Return a mock httpx response representing a successful 201 Created."""
    if body is None:
        body = {"id": 99, "body": "posted comment"}
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.json.return_value = body
    mock_response.raise_for_status.return_value = None
    return mock_response


def _mock_httpx_error_response(status_code: int, text: str) -> MagicMock:
    """Return a mock that raises HTTPStatusError with the given status."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.text = text

    http_error = httpx.HTTPStatusError(
        message=f"{status_code} error",
        request=MagicMock(),
        response=mock_response,
    )
    mock_response.raise_for_status.side_effect = http_error
    return mock_response


# ---------------------------------------------------------------------------
# bind_tools: supported tools
# ---------------------------------------------------------------------------


class TestBindToolsSupported:
    """Verify that all supported tools can be bound successfully."""

    def test_all_supported_tools_can_be_bound(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        bindings = binder_push.bind_tools(list(SUPPORTED_TOOLS))
        for tool_name in SUPPORTED_TOOLS:
            assert tool_name in bindings

    def test_bound_tools_are_callable(self, binder_push: GitHubActionsBinder) -> None:
        bindings = binder_push.bind_tools(list(SUPPORTED_TOOLS))
        for tool_name, impl in bindings.items():
            assert callable(impl), f"{tool_name!r} binding is not callable"

    def test_empty_tool_list_returns_empty_dict(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        bindings = binder_push.bind_tools([])
        assert bindings == {}

    def test_bind_single_tool_repository_read(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        bindings = binder_push.bind_tools(["repository:read"])
        assert "repository:read" in bindings
        assert len(bindings) == 1

    def test_bind_single_tool_shell_execute(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        bindings = binder_push.bind_tools(["shell:execute"])
        assert "shell:execute" in bindings
        assert len(bindings) == 1


# ---------------------------------------------------------------------------
# bind_tools: repository:read – path traversal protection
# ---------------------------------------------------------------------------


class TestBindToolsRepositoryRead:
    """Verify repository:read uses GITHUB_WORKSPACE as root with traversal protection."""

    def test_reads_file_from_workspace(
        self, binder_push: GitHubActionsBinder, push_env: dict[str, str]
    ) -> None:
        workspace = push_env["GITHUB_WORKSPACE"]
        (Path(workspace) / "hello.txt").write_text("hello world\n", encoding="utf-8")

        bindings = binder_push.bind_tools(["repository:read"])
        result = bindings["repository:read"](path="hello.txt")
        assert "hello world" in result

    def test_reads_nested_file_from_workspace(
        self, binder_push: GitHubActionsBinder, push_env: dict[str, str]
    ) -> None:
        workspace = push_env["GITHUB_WORKSPACE"]
        src_dir = Path(workspace) / "src"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("print('main')\n", encoding="utf-8")

        bindings = binder_push.bind_tools(["repository:read"])
        result = bindings["repository:read"](path="src/main.py")
        assert "main" in result

    def test_path_traversal_raises_error(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        bindings = binder_push.bind_tools(["repository:read"])
        with pytest.raises(PathTraversalError):
            bindings["repository:read"](path="../../etc/passwd")

    def test_path_traversal_error_message_contains_path(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        bindings = binder_push.bind_tools(["repository:read"])
        with pytest.raises(PathTraversalError, match="etc/passwd"):
            bindings["repository:read"](path="../../etc/passwd")

    def test_workspace_root_used_not_cwd(
        self, binder_push: GitHubActionsBinder, push_env: dict[str, str]
    ) -> None:
        """repository:read must root at GITHUB_WORKSPACE, not the current directory."""
        workspace = push_env["GITHUB_WORKSPACE"]
        (Path(workspace) / "marker.txt").write_text("workspace marker", encoding="utf-8")

        bindings = binder_push.bind_tools(["repository:read"])
        result = bindings["repository:read"](path="marker.txt")
        assert "workspace marker" in result

    def test_missing_file_raises_file_not_found(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        bindings = binder_push.bind_tools(["repository:read"])
        with pytest.raises(FileNotFoundError):
            bindings["repository:read"](path="nonexistent_file.txt")

    def test_requires_path_kwarg(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        bindings = binder_push.bind_tools(["repository:read"])
        with pytest.raises(TypeError):
            bindings["repository:read"]()


# ---------------------------------------------------------------------------
# bind_tools: shell:execute – allowlist enforcement
# ---------------------------------------------------------------------------


class TestBindToolsShellExecute:
    """Verify shell:execute enforces the read-only command allowlist."""

    def test_allowed_command_git_log_executes(
        self, binder_push: GitHubActionsBinder, push_env: dict[str, str], tmp_path: Path
    ) -> None:
        import subprocess
        workspace = push_env["GITHUB_WORKSPACE"]
        # Initialize git repo in workspace
        subprocess.run(["git", "init", workspace], check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=workspace, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=workspace, check=True, capture_output=True,
        )
        (Path(workspace) / "readme.txt").write_text("hello")
        subprocess.run(
            ["git", "add", "readme.txt"], cwd=workspace, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=workspace, check=True, capture_output=True
        )

        bindings = binder_push.bind_tools(["shell:execute"])
        result = bindings["shell:execute"](command="git log --oneline", cwd=workspace)
        assert isinstance(result, str)

    def test_disallowed_command_rm_raises_error(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        bindings = binder_push.bind_tools(["shell:execute"])
        with pytest.raises(CommandNotAllowedError):
            bindings["shell:execute"](command="rm -rf /tmp/test")

    def test_disallowed_command_curl_raises_error(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        bindings = binder_push.bind_tools(["shell:execute"])
        with pytest.raises(CommandNotAllowedError):
            bindings["shell:execute"](command="curl https://example.com")

    def test_disallowed_command_error_message_contains_command(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        bindings = binder_push.bind_tools(["shell:execute"])
        with pytest.raises(CommandNotAllowedError, match="rm"):
            bindings["shell:execute"](command="rm -rf /")

    def test_disallowed_git_subcommand_raises_error(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        """git push is not in the allowed git subcommands."""
        bindings = binder_push.bind_tools(["shell:execute"])
        with pytest.raises(CommandNotAllowedError):
            bindings["shell:execute"](command="git push origin main")

    def test_allowed_ls_command(
        self, binder_push: GitHubActionsBinder, push_env: dict[str, str]
    ) -> None:
        workspace = push_env["GITHUB_WORKSPACE"]
        bindings = binder_push.bind_tools(["shell:execute"])
        result = bindings["shell:execute"](command="ls", cwd=workspace)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# bind_tools: pr:comment – API URL and payload
# ---------------------------------------------------------------------------


class TestBindToolsPRComment:
    """Verify pr:comment posts to correct GitHub API URL with correct payload."""

    def test_pr_comment_posts_to_correct_url(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        bindings = binder_pr.bind_tools(["pr:comment"])
        with patch("httpx.post", return_value=_mock_httpx_post_success()) as mock_post:
            bindings["pr:comment"](body="Review complete")
        posted_url = mock_post.call_args.args[0] if mock_post.call_args.args else mock_post.call_args[0][0]
        assert "repos/owner/repo/issues/42/comments" in posted_url

    def test_pr_comment_uses_github_api_base_url(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        bindings = binder_pr.bind_tools(["pr:comment"])
        with patch("httpx.post", return_value=_mock_httpx_post_success()) as mock_post:
            bindings["pr:comment"](body="hello")
        posted_url = mock_post.call_args.args[0] if mock_post.call_args.args else mock_post.call_args[0][0]
        assert "api.github.com" in posted_url

    def test_pr_comment_posts_body_in_payload(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        bindings = binder_pr.bind_tools(["pr:comment"])
        with patch("httpx.post", return_value=_mock_httpx_post_success()) as mock_post:
            bindings["pr:comment"](body="Review complete: no issues found")
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs.get("json", {}).get("body") == "Review complete: no issues found"

    def test_pr_comment_uses_auth_token(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        bindings = binder_pr.bind_tools(["pr:comment"])
        with patch("httpx.post", return_value=_mock_httpx_post_success()) as mock_post:
            bindings["pr:comment"](body="test body")
        call_kwargs = mock_post.call_args.kwargs
        headers = call_kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer ghp_testtoken"

    def test_pr_comment_returns_api_response(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        api_response = {"id": 123, "body": "Review complete"}
        bindings = binder_pr.bind_tools(["pr:comment"])
        with patch("httpx.post", return_value=_mock_httpx_post_success(api_response)):
            result = bindings["pr:comment"](body="Review complete")
        assert result == api_response

    def test_pr_comment_on_non_pr_event_raises_value_error(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        """pr:comment on a push event (no PR number) should raise ValueError."""
        bindings = binder_push.bind_tools(["pr:comment"])
        with pytest.raises(ValueError, match="pull_request"):
            bindings["pr:comment"](body="some comment")

    def test_pr_comment_requires_body_kwarg(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        bindings = binder_pr.bind_tools(["pr:comment"])
        with pytest.raises(TypeError):
            bindings["pr:comment"]()


# ---------------------------------------------------------------------------
# bind_tools: pr:review – API URL and payload
# ---------------------------------------------------------------------------


class TestBindToolsPRReview:
    """Verify pr:review posts to correct GitHub API URL with correct payload."""

    def test_pr_review_posts_to_correct_url(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        bindings = binder_pr.bind_tools(["pr:review"])
        with patch("httpx.post", return_value=_mock_httpx_post_success()) as mock_post:
            bindings["pr:review"](body="LGTM")
        posted_url = mock_post.call_args.args[0] if mock_post.call_args.args else mock_post.call_args[0][0]
        assert "repos/owner/repo/pulls/42/reviews" in posted_url

    def test_pr_review_uses_github_api_base_url(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        bindings = binder_pr.bind_tools(["pr:review"])
        with patch("httpx.post", return_value=_mock_httpx_post_success()) as mock_post:
            bindings["pr:review"](body="LGTM")
        posted_url = mock_post.call_args.args[0] if mock_post.call_args.args else mock_post.call_args[0][0]
        assert "api.github.com" in posted_url

    def test_pr_review_posts_body_in_payload(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        bindings = binder_pr.bind_tools(["pr:review"])
        with patch("httpx.post", return_value=_mock_httpx_post_success()) as mock_post:
            bindings["pr:review"](body="Approved changes")
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs.get("json", {}).get("body") == "Approved changes"

    def test_pr_review_includes_event_in_payload(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        bindings = binder_pr.bind_tools(["pr:review"])
        with patch("httpx.post", return_value=_mock_httpx_post_success()) as mock_post:
            bindings["pr:review"](body="LGTM", event="APPROVE")
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs.get("json", {}).get("event") == "APPROVE"

    def test_pr_review_default_event_is_comment(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        bindings = binder_pr.bind_tools(["pr:review"])
        with patch("httpx.post", return_value=_mock_httpx_post_success()) as mock_post:
            bindings["pr:review"](body="review body")
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs.get("json", {}).get("event") == "COMMENT"

    def test_pr_review_uses_auth_token(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        bindings = binder_pr.bind_tools(["pr:review"])
        with patch("httpx.post", return_value=_mock_httpx_post_success()) as mock_post:
            bindings["pr:review"](body="review body")
        call_kwargs = mock_post.call_args.kwargs
        headers = call_kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer ghp_testtoken"

    def test_pr_review_on_non_pr_event_raises_value_error(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        """pr:review on a push event (no PR number) should raise ValueError."""
        bindings = binder_push.bind_tools(["pr:review"])
        with pytest.raises(ValueError, match="pull_request"):
            bindings["pr:review"](body="some review")

    def test_pr_review_returns_api_response(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        api_response = {"id": 456, "state": "APPROVED"}
        bindings = binder_pr.bind_tools(["pr:review"])
        with patch("httpx.post", return_value=_mock_httpx_post_success(api_response)):
            result = bindings["pr:review"](body="LGTM")
        assert result == api_response

    def test_pr_review_accepts_inline_comments(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        inline_comments = [{"path": "src/main.py", "position": 5, "body": "typo here"}]
        bindings = binder_pr.bind_tools(["pr:review"])
        with patch("httpx.post", return_value=_mock_httpx_post_success()) as mock_post:
            bindings["pr:review"](body="inline review", comments=inline_comments)
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs.get("json", {}).get("comments") == inline_comments


# ---------------------------------------------------------------------------
# bind_tools: UnsupportedToolError
# ---------------------------------------------------------------------------


class TestBindToolsUnsupportedTools:
    """Verify UnsupportedToolError is raised for unknown tool names."""

    def test_unknown_tool_raises_unsupported_tool_error(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        with pytest.raises(UnsupportedToolError):
            binder_push.bind_tools(["database:query"])

    def test_error_message_contains_tool_name(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        with pytest.raises(UnsupportedToolError, match="database:query"):
            binder_push.bind_tools(["database:query"])

    def test_unknown_tool_in_mixed_list_raises(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        with pytest.raises(UnsupportedToolError):
            binder_push.bind_tools(["repository:read", "unknown:tool"])

    def test_error_mentions_unsupported_tool_name(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        with pytest.raises(UnsupportedToolError, match="unknown:tool"):
            binder_push.bind_tools(["repository:read", "unknown:tool"])

    def test_arbitrary_unsupported_tool_name(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        with pytest.raises(UnsupportedToolError):
            binder_push.bind_tools(["filesystem:write"])


# ---------------------------------------------------------------------------
# API error handling for pr:comment
# ---------------------------------------------------------------------------


class TestPRCommentAPIErrors:
    """Verify structured error messages on GitHub API failures for pr:comment."""

    def test_403_error_mentions_status_code(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        error_response = _mock_httpx_error_response(403, "Forbidden")
        bindings = binder_pr.bind_tools(["pr:comment"])
        with patch("httpx.post", return_value=error_response):
            with pytest.raises(RuntimeError) as exc_info:
                bindings["pr:comment"](body="test")
        assert "403" in str(exc_info.value)

    def test_403_error_includes_scope_remediation(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        error_response = _mock_httpx_error_response(403, "Forbidden")
        bindings = binder_pr.bind_tools(["pr:comment"])
        with patch("httpx.post", return_value=error_response):
            with pytest.raises(RuntimeError) as exc_info:
                bindings["pr:comment"](body="test")
        assert "pull_requests:write" in str(exc_info.value)

    def test_404_error_mentions_status_code(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        error_response = _mock_httpx_error_response(404, "Not Found")
        bindings = binder_pr.bind_tools(["pr:comment"])
        with patch("httpx.post", return_value=error_response):
            with pytest.raises(RuntimeError) as exc_info:
                bindings["pr:comment"](body="test")
        assert "404" in str(exc_info.value)

    def test_404_error_includes_pr_number(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        error_response = _mock_httpx_error_response(404, "Not Found")
        bindings = binder_pr.bind_tools(["pr:comment"])
        with patch("httpx.post", return_value=error_response):
            with pytest.raises(RuntimeError) as exc_info:
                bindings["pr:comment"](body="test")
        assert "42" in str(exc_info.value)

    def test_network_timeout_raises_runtime_error(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        bindings = binder_pr.bind_tools(["pr:comment"])
        with patch("httpx.post", side_effect=httpx.TimeoutException("timed out")):
            with pytest.raises(RuntimeError) as exc_info:
                bindings["pr:comment"](body="test")
        assert "timeout" in str(exc_info.value).lower()

    def test_500_error_raises_runtime_error_with_status(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        error_response = _mock_httpx_error_response(500, "Internal Server Error")
        bindings = binder_pr.bind_tools(["pr:comment"])
        with patch("httpx.post", return_value=error_response):
            with pytest.raises(RuntimeError) as exc_info:
                bindings["pr:comment"](body="test")
        assert "500" in str(exc_info.value)


# ---------------------------------------------------------------------------
# API error handling for pr:review
# ---------------------------------------------------------------------------


class TestPRReviewAPIErrors:
    """Verify structured error messages on GitHub API failures for pr:review."""

    def test_403_error_includes_scope_remediation(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        error_response = _mock_httpx_error_response(403, "Forbidden")
        bindings = binder_pr.bind_tools(["pr:review"])
        with patch("httpx.post", return_value=error_response):
            with pytest.raises(RuntimeError) as exc_info:
                bindings["pr:review"](body="test")
        assert "pull_requests:write" in str(exc_info.value)

    def test_404_error_includes_pr_not_found_info(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        error_response = _mock_httpx_error_response(404, "Not Found")
        bindings = binder_pr.bind_tools(["pr:review"])
        with patch("httpx.post", return_value=error_response):
            with pytest.raises(RuntimeError) as exc_info:
                bindings["pr:review"](body="test")
        assert "42" in str(exc_info.value)

    def test_network_timeout_raises_runtime_error(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        bindings = binder_pr.bind_tools(["pr:review"])
        with patch("httpx.post", side_effect=httpx.TimeoutException("timed out")):
            with pytest.raises(RuntimeError) as exc_info:
                bindings["pr:review"](body="test")
        assert "timeout" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Helpers / fixtures for issue event
# ---------------------------------------------------------------------------


def _make_issue_env(
    tmp_path: Path,
    issue_number: int = 7,
    token: str = "ghp_testtoken",
    repository: str = "owner/repo",
) -> dict[str, str]:
    """Build an issues event environment dict."""
    payload = {"issue": {"number": issue_number}}
    return _make_env(
        tmp_path,
        event_name="issues",
        payload=payload,
        token=token,
        repository=repository,
    )


@pytest.fixture()
def issue_env(tmp_path: Path) -> dict[str, str]:
    """Environment for an issues event with issue #7."""
    return _make_issue_env(tmp_path)


@pytest.fixture()
def binder_issue(issue_env: dict[str, str]) -> GitHubActionsBinder:
    """GitHubActionsBinder instantiated for an issues event."""
    return GitHubActionsBinder(env=issue_env)


# ---------------------------------------------------------------------------
# _extract_issue_number: static method
# ---------------------------------------------------------------------------


class TestExtractIssueNumber:
    """Verify _extract_issue_number correctly handles event payloads."""

    def test_returns_none_for_non_issues_event(self) -> None:
        result = GitHubActionsBinder._extract_issue_number("push", {})
        assert result is None

    def test_returns_none_for_pull_request_event(self) -> None:
        payload = {"issue": {"number": 10}}
        result = GitHubActionsBinder._extract_issue_number("pull_request", payload)
        assert result is None

    def test_returns_issue_number_for_issues_event(self) -> None:
        payload = {"issue": {"number": 7}}
        result = GitHubActionsBinder._extract_issue_number("issues", payload)
        assert result == 7

    def test_returns_none_when_issue_key_absent(self) -> None:
        result = GitHubActionsBinder._extract_issue_number("issues", {})
        assert result is None

    def test_returns_none_when_number_key_absent(self) -> None:
        result = GitHubActionsBinder._extract_issue_number("issues", {"issue": {}})
        assert result is None

    def test_converts_number_to_int(self) -> None:
        payload = {"issue": {"number": "42"}}
        result = GitHubActionsBinder._extract_issue_number("issues", payload)
        assert result == 42
        assert isinstance(result, int)


# ---------------------------------------------------------------------------
# bind_tools: issue:comment
# ---------------------------------------------------------------------------


class TestBindToolsIssueComment:
    """Verify issue:comment posts to correct GitHub API URL with correct payload."""

    def test_issue_comment_posts_to_correct_url(
        self, binder_issue: GitHubActionsBinder
    ) -> None:
        bindings = binder_issue.bind_tools(["issue:comment"])
        with patch("httpx.post", return_value=_mock_httpx_post_success()) as mock_post:
            bindings["issue:comment"](body="Triaged this issue")
        posted_url = (
            mock_post.call_args.args[0]
            if mock_post.call_args.args
            else mock_post.call_args[0][0]
        )
        assert "repos/owner/repo/issues/7/comments" in posted_url

    def test_issue_comment_uses_github_api_base_url(
        self, binder_issue: GitHubActionsBinder
    ) -> None:
        bindings = binder_issue.bind_tools(["issue:comment"])
        with patch("httpx.post", return_value=_mock_httpx_post_success()) as mock_post:
            bindings["issue:comment"](body="hello")
        posted_url = (
            mock_post.call_args.args[0]
            if mock_post.call_args.args
            else mock_post.call_args[0][0]
        )
        assert "api.github.com" in posted_url

    def test_issue_comment_posts_body_in_payload(
        self, binder_issue: GitHubActionsBinder
    ) -> None:
        bindings = binder_issue.bind_tools(["issue:comment"])
        with patch("httpx.post", return_value=_mock_httpx_post_success()) as mock_post:
            bindings["issue:comment"](body="Triage complete: needs label")
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs.get("json", {}).get("body") == "Triage complete: needs label"

    def test_issue_comment_uses_auth_token(
        self, binder_issue: GitHubActionsBinder
    ) -> None:
        bindings = binder_issue.bind_tools(["issue:comment"])
        with patch("httpx.post", return_value=_mock_httpx_post_success()) as mock_post:
            bindings["issue:comment"](body="test body")
        call_kwargs = mock_post.call_args.kwargs
        headers = call_kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer ghp_testtoken"

    def test_issue_comment_returns_api_response(
        self, binder_issue: GitHubActionsBinder
    ) -> None:
        api_response = {"id": 999, "body": "Triaged this issue"}
        bindings = binder_issue.bind_tools(["issue:comment"])
        with patch("httpx.post", return_value=_mock_httpx_post_success(api_response)):
            result = bindings["issue:comment"](body="Triaged this issue")
        assert result == api_response

    def test_issue_comment_on_non_issues_event_raises_value_error(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        """issue:comment on a push event (no issue number) should raise ValueError."""
        bindings = binder_push.bind_tools(["issue:comment"])
        with pytest.raises(ValueError, match="issues event"):
            bindings["issue:comment"](body="some comment")

    def test_issue_comment_on_pr_event_raises_value_error(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        """issue:comment on a pull_request event should raise ValueError."""
        bindings = binder_pr.bind_tools(["issue:comment"])
        with pytest.raises(ValueError, match="issues event"):
            bindings["issue:comment"](body="some comment")

    def test_issue_comment_requires_body_kwarg(
        self, binder_issue: GitHubActionsBinder
    ) -> None:
        bindings = binder_issue.bind_tools(["issue:comment"])
        with pytest.raises(TypeError):
            bindings["issue:comment"]()  # type: ignore[call-arg]

    def test_issue_comment_403_error_mentions_issues_scope(
        self, binder_issue: GitHubActionsBinder
    ) -> None:
        error_response = _mock_httpx_error_response(403, "Forbidden")
        bindings = binder_issue.bind_tools(["issue:comment"])
        with patch("httpx.post", return_value=error_response):
            with pytest.raises(RuntimeError) as exc_info:
                bindings["issue:comment"](body="test")
        assert "issues:write" in str(exc_info.value)

    def test_issue_comment_404_error_mentions_issue_number(
        self, binder_issue: GitHubActionsBinder
    ) -> None:
        error_response = _mock_httpx_error_response(404, "Not Found")
        bindings = binder_issue.bind_tools(["issue:comment"])
        with patch("httpx.post", return_value=error_response):
            with pytest.raises(RuntimeError) as exc_info:
                bindings["issue:comment"](body="test")
        assert "7" in str(exc_info.value)

    def test_issue_comment_timeout_raises_runtime_error(
        self, binder_issue: GitHubActionsBinder
    ) -> None:
        bindings = binder_issue.bind_tools(["issue:comment"])
        with patch("httpx.post", side_effect=httpx.TimeoutException("timed out")):
            with pytest.raises(RuntimeError) as exc_info:
                bindings["issue:comment"](body="test")
        assert "timeout" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# bind_tools: issue:label
# ---------------------------------------------------------------------------


class TestBindToolsIssueLabel:
    """Verify issue:label posts to correct GitHub API URL with correct payload."""

    def test_issue_label_posts_to_correct_url(
        self, binder_issue: GitHubActionsBinder
    ) -> None:
        bindings = binder_issue.bind_tools(["issue:label"])
        with patch("httpx.post", return_value=_mock_httpx_post_success()) as mock_post:
            bindings["issue:label"](labels=["bug"])
        posted_url = (
            mock_post.call_args.args[0]
            if mock_post.call_args.args
            else mock_post.call_args[0][0]
        )
        assert "repos/owner/repo/issues/7/labels" in posted_url

    def test_issue_label_uses_github_api_base_url(
        self, binder_issue: GitHubActionsBinder
    ) -> None:
        bindings = binder_issue.bind_tools(["issue:label"])
        with patch("httpx.post", return_value=_mock_httpx_post_success()) as mock_post:
            bindings["issue:label"](labels=["bug"])
        posted_url = (
            mock_post.call_args.args[0]
            if mock_post.call_args.args
            else mock_post.call_args[0][0]
        )
        assert "api.github.com" in posted_url

    def test_issue_label_posts_labels_in_payload(
        self, binder_issue: GitHubActionsBinder
    ) -> None:
        bindings = binder_issue.bind_tools(["issue:label"])
        with patch("httpx.post", return_value=_mock_httpx_post_success()) as mock_post:
            bindings["issue:label"](labels=["bug", "triage"])
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs.get("json", {}).get("labels") == ["bug", "triage"]

    def test_issue_label_uses_auth_token(
        self, binder_issue: GitHubActionsBinder
    ) -> None:
        bindings = binder_issue.bind_tools(["issue:label"])
        with patch("httpx.post", return_value=_mock_httpx_post_success()) as mock_post:
            bindings["issue:label"](labels=["enhancement"])
        call_kwargs = mock_post.call_args.kwargs
        headers = call_kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer ghp_testtoken"

    def test_issue_label_returns_api_response(
        self, binder_issue: GitHubActionsBinder
    ) -> None:
        api_response = [{"id": 1, "name": "bug"}]
        bindings = binder_issue.bind_tools(["issue:label"])
        with patch("httpx.post", return_value=_mock_httpx_post_success(api_response)):
            result = bindings["issue:label"](labels=["bug"])
        assert result == api_response

    def test_issue_label_on_non_issues_event_raises_value_error(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        """issue:label on a push event (no issue number) should raise ValueError."""
        bindings = binder_push.bind_tools(["issue:label"])
        with pytest.raises(ValueError, match="issues event"):
            bindings["issue:label"](labels=["bug"])

    def test_issue_label_on_pr_event_raises_value_error(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        """issue:label on a pull_request event should raise ValueError."""
        bindings = binder_pr.bind_tools(["issue:label"])
        with pytest.raises(ValueError, match="issues event"):
            bindings["issue:label"](labels=["bug"])

    def test_issue_label_requires_labels_kwarg(
        self, binder_issue: GitHubActionsBinder
    ) -> None:
        bindings = binder_issue.bind_tools(["issue:label"])
        with pytest.raises(TypeError):
            bindings["issue:label"]()  # type: ignore[call-arg]

    def test_issue_label_403_error_mentions_issues_scope(
        self, binder_issue: GitHubActionsBinder
    ) -> None:
        error_response = _mock_httpx_error_response(403, "Forbidden")
        bindings = binder_issue.bind_tools(["issue:label"])
        with patch("httpx.post", return_value=error_response):
            with pytest.raises(RuntimeError) as exc_info:
                bindings["issue:label"](labels=["bug"])
        assert "issues:write" in str(exc_info.value)

    def test_issue_label_404_error_mentions_issue_number(
        self, binder_issue: GitHubActionsBinder
    ) -> None:
        error_response = _mock_httpx_error_response(404, "Not Found")
        bindings = binder_issue.bind_tools(["issue:label"])
        with patch("httpx.post", return_value=error_response):
            with pytest.raises(RuntimeError) as exc_info:
                bindings["issue:label"](labels=["bug"])
        assert "7" in str(exc_info.value)

    def test_issue_label_422_error_mentions_validation(
        self, binder_issue: GitHubActionsBinder
    ) -> None:
        error_response = _mock_httpx_error_response(422, "Unprocessable Entity")
        bindings = binder_issue.bind_tools(["issue:label"])
        with patch("httpx.post", return_value=error_response):
            with pytest.raises(RuntimeError) as exc_info:
                bindings["issue:label"](labels=["nonexistent-label"])
        assert "422" in str(exc_info.value)

    def test_issue_label_timeout_raises_runtime_error(
        self, binder_issue: GitHubActionsBinder
    ) -> None:
        bindings = binder_issue.bind_tools(["issue:label"])
        with patch("httpx.post", side_effect=httpx.TimeoutException("timed out")):
            with pytest.raises(RuntimeError) as exc_info:
                bindings["issue:label"](labels=["bug"])
        assert "timeout" in str(exc_info.value).lower()
