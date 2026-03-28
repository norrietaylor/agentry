"""Integration tests for T01: GitHubActionsBinder issue:comment and issue:label.

Tests cover:
- issue:comment posts correctly to the GitHub API (mocked httpx).
- issue:label posts correctly to the GitHub API (mocked httpx).
- Both tools work when bound alongside other tools (composite bind_tools call).
- Error handling paths for 403, 404, and timeouts match documented remediation.
- issue_number is None when event is not issues; both tools raise ValueError.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from agentry.binders.github_actions import SUPPORTED_TOOLS, GitHubActionsBinder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_env(
    tmp_path: Path,
    event_name: str = "issues",
    payload: dict[str, Any] | None = None,
    token: str = "ghp_integration_token",
    workspace: str | None = None,
    repository: str = "org/myrepo",
) -> dict[str, str]:
    """Build a GitHub Actions environment dict for integration testing."""
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


def _make_issues_env(
    tmp_path: Path,
    issue_number: int = 15,
    token: str = "ghp_integration_token",
    repository: str = "org/myrepo",
) -> dict[str, str]:
    """Build an issues event environment dict."""
    payload = {"issue": {"number": issue_number, "title": "Test issue"}}
    return _make_env(
        tmp_path,
        event_name="issues",
        payload=payload,
        token=token,
        repository=repository,
    )


def _mock_success(body: Any = None) -> MagicMock:
    """Return a mock httpx response representing a successful 201 Created."""
    if body is None:
        body = {"id": 1, "node_id": "abc"}
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.json.return_value = body
    mock_response.raise_for_status.return_value = None
    return mock_response


def _mock_error(status_code: int, text: str = "Error") -> MagicMock:
    """Return a mock that raises HTTPStatusError."""
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
# issue:comment integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestIssueCommentIntegration:
    """Integration tests for the issue:comment tool binding."""

    def test_issue_comment_sends_correct_api_request(self, tmp_path: Path) -> None:
        """issue:comment sends a POST to the correct GitHub API endpoint."""
        env = _make_issues_env(tmp_path, issue_number=15, repository="org/myrepo")
        binder = GitHubActionsBinder(env=env)
        bindings = binder.bind_tools(["issue:comment"])

        with patch("httpx.post", return_value=_mock_success()) as mock_post:
            bindings["issue:comment"](body="This issue has been triaged.")

        # Verify endpoint
        posted_url = (
            mock_post.call_args.args[0]
            if mock_post.call_args.args
            else mock_post.call_args[0][0]
        )
        assert "api.github.com" in posted_url
        assert "repos/org/myrepo/issues/15/comments" in posted_url

    def test_issue_comment_sends_correct_body(self, tmp_path: Path) -> None:
        """issue:comment includes the body text in the POST payload."""
        env = _make_issues_env(tmp_path)
        binder = GitHubActionsBinder(env=env)
        bindings = binder.bind_tools(["issue:comment"])
        comment_body = "## Triage Summary\n\nThis is a bug."

        with patch("httpx.post", return_value=_mock_success()) as mock_post:
            bindings["issue:comment"](body=comment_body)

        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["json"]["body"] == comment_body

    def test_issue_comment_includes_authorization_header(self, tmp_path: Path) -> None:
        """issue:comment attaches the Bearer token in the Authorization header."""
        env = _make_issues_env(tmp_path, token="ghp_secret_token")
        binder = GitHubActionsBinder(env=env)
        bindings = binder.bind_tools(["issue:comment"])

        with patch("httpx.post", return_value=_mock_success()) as mock_post:
            bindings["issue:comment"](body="test")

        headers = mock_post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer ghp_secret_token"

    def test_issue_comment_returns_response_json(self, tmp_path: Path) -> None:
        """issue:comment returns the parsed API response."""
        env = _make_issues_env(tmp_path)
        binder = GitHubActionsBinder(env=env)
        bindings = binder.bind_tools(["issue:comment"])
        api_body = {"id": 777, "body": "comment posted"}

        with patch("httpx.post", return_value=_mock_success(api_body)):
            result = bindings["issue:comment"](body="test")

        assert result == api_body

    def test_issue_comment_403_raises_runtime_with_remediation(
        self, tmp_path: Path
    ) -> None:
        """403 from GitHub API includes issues:write scope remediation."""
        env = _make_issues_env(tmp_path)
        binder = GitHubActionsBinder(env=env)
        bindings = binder.bind_tools(["issue:comment"])

        with patch("httpx.post", return_value=_mock_error(403, "Forbidden")):
            with pytest.raises(RuntimeError) as exc_info:
                bindings["issue:comment"](body="test")

        assert "403" in str(exc_info.value)
        assert "issues:write" in str(exc_info.value)

    def test_issue_comment_404_raises_runtime_with_issue_number(
        self, tmp_path: Path
    ) -> None:
        """404 from GitHub API mentions the issue number in the error."""
        env = _make_issues_env(tmp_path, issue_number=999)
        binder = GitHubActionsBinder(env=env)
        bindings = binder.bind_tools(["issue:comment"])

        with patch("httpx.post", return_value=_mock_error(404, "Not Found")):
            with pytest.raises(RuntimeError) as exc_info:
                bindings["issue:comment"](body="test")

        assert "999" in str(exc_info.value)

    def test_issue_comment_timeout_raises_runtime_error(self, tmp_path: Path) -> None:
        """Network timeout raises RuntimeError with descriptive message."""
        env = _make_issues_env(tmp_path)
        binder = GitHubActionsBinder(env=env)
        bindings = binder.bind_tools(["issue:comment"])

        with patch("httpx.post", side_effect=httpx.TimeoutException("timed out")):
            with pytest.raises(RuntimeError) as exc_info:
                bindings["issue:comment"](body="test")

        assert "timeout" in str(exc_info.value).lower()

    def test_issue_comment_raises_on_non_issues_event(self, tmp_path: Path) -> None:
        """issue:comment raises ValueError when not in an issues event context."""
        env = _make_env(tmp_path, event_name="push", payload={})
        binder = GitHubActionsBinder(env=env)
        bindings = binder.bind_tools(["issue:comment"])

        with pytest.raises(ValueError, match="issues event"):
            bindings["issue:comment"](body="test")


# ---------------------------------------------------------------------------
# issue:label integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestIssueLabelIntegration:
    """Integration tests for the issue:label tool binding."""

    def test_issue_label_sends_correct_api_request(self, tmp_path: Path) -> None:
        """issue:label sends a POST to the correct GitHub API endpoint."""
        env = _make_issues_env(tmp_path, issue_number=15, repository="org/myrepo")
        binder = GitHubActionsBinder(env=env)
        bindings = binder.bind_tools(["issue:label"])

        with patch("httpx.post", return_value=_mock_success()) as mock_post:
            bindings["issue:label"](labels=["bug"])

        posted_url = (
            mock_post.call_args.args[0]
            if mock_post.call_args.args
            else mock_post.call_args[0][0]
        )
        assert "api.github.com" in posted_url
        assert "repos/org/myrepo/issues/15/labels" in posted_url

    def test_issue_label_sends_labels_in_payload(self, tmp_path: Path) -> None:
        """issue:label includes the labels list in the POST payload."""
        env = _make_issues_env(tmp_path)
        binder = GitHubActionsBinder(env=env)
        bindings = binder.bind_tools(["issue:label"])
        labels = ["bug", "triage", "priority:high"]

        with patch("httpx.post", return_value=_mock_success()) as mock_post:
            bindings["issue:label"](labels=labels)

        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["json"]["labels"] == labels

    def test_issue_label_includes_authorization_header(self, tmp_path: Path) -> None:
        """issue:label attaches the Bearer token in the Authorization header."""
        env = _make_issues_env(tmp_path, token="ghp_label_token")
        binder = GitHubActionsBinder(env=env)
        bindings = binder.bind_tools(["issue:label"])

        with patch("httpx.post", return_value=_mock_success()) as mock_post:
            bindings["issue:label"](labels=["enhancement"])

        headers = mock_post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer ghp_label_token"

    def test_issue_label_returns_response_json(self, tmp_path: Path) -> None:
        """issue:label returns the parsed API response."""
        env = _make_issues_env(tmp_path)
        binder = GitHubActionsBinder(env=env)
        bindings = binder.bind_tools(["issue:label"])
        api_body = [{"id": 1, "name": "bug"}]

        with patch("httpx.post", return_value=_mock_success(api_body)):
            result = bindings["issue:label"](labels=["bug"])

        assert result == api_body

    def test_issue_label_403_raises_runtime_with_remediation(
        self, tmp_path: Path
    ) -> None:
        """403 from GitHub API includes issues:write scope remediation."""
        env = _make_issues_env(tmp_path)
        binder = GitHubActionsBinder(env=env)
        bindings = binder.bind_tools(["issue:label"])

        with patch("httpx.post", return_value=_mock_error(403, "Forbidden")):
            with pytest.raises(RuntimeError) as exc_info:
                bindings["issue:label"](labels=["bug"])

        assert "403" in str(exc_info.value)
        assert "issues:write" in str(exc_info.value)

    def test_issue_label_404_raises_runtime_with_issue_number(
        self, tmp_path: Path
    ) -> None:
        """404 from GitHub API mentions the issue number in the error."""
        env = _make_issues_env(tmp_path, issue_number=888)
        binder = GitHubActionsBinder(env=env)
        bindings = binder.bind_tools(["issue:label"])

        with patch("httpx.post", return_value=_mock_error(404, "Not Found")):
            with pytest.raises(RuntimeError) as exc_info:
                bindings["issue:label"](labels=["bug"])

        assert "888" in str(exc_info.value)

    def test_issue_label_422_raises_runtime_with_validation_hint(
        self, tmp_path: Path
    ) -> None:
        """422 from GitHub API includes validation failure hint."""
        env = _make_issues_env(tmp_path)
        binder = GitHubActionsBinder(env=env)
        bindings = binder.bind_tools(["issue:label"])

        with patch("httpx.post", return_value=_mock_error(422, "Unprocessable Entity")):
            with pytest.raises(RuntimeError) as exc_info:
                bindings["issue:label"](labels=["nonexistent"])

        assert "422" in str(exc_info.value)

    def test_issue_label_timeout_raises_runtime_error(self, tmp_path: Path) -> None:
        """Network timeout raises RuntimeError with descriptive message."""
        env = _make_issues_env(tmp_path)
        binder = GitHubActionsBinder(env=env)
        bindings = binder.bind_tools(["issue:label"])

        with patch("httpx.post", side_effect=httpx.TimeoutException("timed out")):
            with pytest.raises(RuntimeError) as exc_info:
                bindings["issue:label"](labels=["bug"])

        assert "timeout" in str(exc_info.value).lower()

    def test_issue_label_raises_on_non_issues_event(self, tmp_path: Path) -> None:
        """issue:label raises ValueError when not in an issues event context."""
        env = _make_env(tmp_path, event_name="push", payload={})
        binder = GitHubActionsBinder(env=env)
        bindings = binder.bind_tools(["issue:label"])

        with pytest.raises(ValueError, match="issues event"):
            bindings["issue:label"](labels=["bug"])


# ---------------------------------------------------------------------------
# SUPPORTED_TOOLS includes issue tools
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSupportedToolsContainsIssueTools:
    """Verify the SUPPORTED_TOOLS frozenset includes both issue tools."""

    def test_issue_comment_in_supported_tools(self) -> None:
        assert "issue:comment" in SUPPORTED_TOOLS

    def test_issue_label_in_supported_tools(self) -> None:
        assert "issue:label" in SUPPORTED_TOOLS

    def test_both_issue_tools_can_be_bound_together(self, tmp_path: Path) -> None:
        """Both issue tools can be bound in a single bind_tools call."""
        env = _make_issues_env(tmp_path)
        binder = GitHubActionsBinder(env=env)
        bindings = binder.bind_tools(["issue:comment", "issue:label"])
        assert "issue:comment" in bindings
        assert "issue:label" in bindings
        assert callable(bindings["issue:comment"])
        assert callable(bindings["issue:label"])
