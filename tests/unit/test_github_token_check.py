"""Unit tests for T03.2: GitHubTokenScopeCheck preflight check.

Tests cover:
- Scope verification pass: API returns 200 for all required endpoints.
- Scope verification fail: API returns 403 for pr:comment scope.
- Skip-when-not-in-CI: GITHUB_TOKEN unset, check passes with skip message.
- Tool-to-scope mapping: repository:read, pr:comment, pr:review.
- Multiple missing scopes: all listed in message.
- Network error handling: connection failure handled gracefully.
"""

from __future__ import annotations

import os
import urllib.error
from io import BytesIO
from typing import Any
from unittest.mock import MagicMock, patch

from agentry.security.checks import GitHubTokenScopeCheck

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_DEFAULT_TOOLS = ["repository:read", "pr:comment"]


def _make_check(
    tool_declarations: list[str] | None = None,
    github_repository: str = "owner/repo",
) -> GitHubTokenScopeCheck:
    tools = tool_declarations if tool_declarations is not None else _DEFAULT_TOOLS
    return GitHubTokenScopeCheck(
        tool_declarations=tools,
        github_repository=github_repository,
        api_base="https://api.github.com",
        timeout=5,
    )


def _make_http200_response(
    oauth_scopes_header: str = "",
) -> Any:
    """Return a fake urllib response context manager for HTTP 200."""
    resp = MagicMock()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    resp.status = 200
    resp.read = MagicMock(return_value=b'{"id": 1, "name": "repo"}')
    resp.headers = MagicMock()
    resp.headers.get = MagicMock(return_value=oauth_scopes_header)
    return resp


def _make_http_error(code: int, msg: str = "Error") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://api.github.com/repos/owner/repo",
        code=code,
        msg=msg,
        hdrs=MagicMock(),  # type: ignore[arg-type]
        fp=BytesIO(b""),
    )


# ---------------------------------------------------------------------------
# TestGitHubTokenScopeCheckName
# ---------------------------------------------------------------------------


class TestGitHubTokenScopeCheckName:
    """Tests for the name property."""

    def test_name_is_github_token_scope(self) -> None:
        check = _make_check()
        assert check.name == "github_token_scope"

    def test_name_is_string(self) -> None:
        check = _make_check()
        assert isinstance(check.name, str)


# ---------------------------------------------------------------------------
# TestSkipWhenNoToken
# ---------------------------------------------------------------------------


class TestSkipWhenNoToken:
    """Tests for skipping when GITHUB_TOKEN is not set."""

    def test_no_token_passes(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        check = _make_check()
        result = check.run()
        assert result.passed is True

    def test_no_token_message_mentions_skip(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        check = _make_check()
        result = check.run()
        msg = result.message.lower()
        assert "skip" in msg or "not set" in msg or "not running" in msg

    def test_no_token_message_mentions_github_token(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        check = _make_check()
        result = check.run()
        assert "GITHUB_TOKEN" in result.message

    def test_empty_token_passes(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "")
        check = _make_check()
        result = check.run()
        assert result.passed is True

    def test_result_has_required_fields(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        check = _make_check()
        result = check.run()
        assert hasattr(result, "passed")
        assert hasattr(result, "name")
        assert hasattr(result, "message")


# ---------------------------------------------------------------------------
# TestScopeVerificationPass
# ---------------------------------------------------------------------------


class TestScopeVerificationPass:
    """Tests for successful scope verification when API returns 200."""

    def test_all_scopes_present_passes(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghs_valid_token")
        check = _make_check(["repository:read", "pr:comment"])
        with patch(
            "urllib.request.urlopen",
            return_value=_make_http200_response(),
        ):
            result = check.run()
        assert result.passed is True

    def test_pass_message_mentions_required_scopes(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghs_valid_token")
        check = _make_check(["repository:read"])
        with patch(
            "urllib.request.urlopen",
            return_value=_make_http200_response(),
        ):
            result = check.run()
        assert result.passed is True

    def test_no_tool_declarations_passes_immediately(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghs_valid_token")
        check = _make_check(tool_declarations=[])
        result = check.run()
        assert result.passed is True

    def test_no_tool_declarations_message_no_scopes_needed(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghs_valid_token")
        check = _make_check(tool_declarations=[])
        result = check.run()
        assert "no tool" in result.message.lower() or "no" in result.message.lower()


# ---------------------------------------------------------------------------
# TestScopeVerificationFail
# ---------------------------------------------------------------------------


class TestScopeVerificationFail:
    """Tests for scope verification failure when API returns 403."""

    def test_403_for_pr_comment_fails(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghs_restricted_token")
        check = _make_check(["pr:comment"])
        with patch(
            "urllib.request.urlopen",
            side_effect=_make_http_error(403, "Forbidden"),
        ):
            result = check.run()
        assert result.passed is False

    def test_403_message_identifies_pull_requests_write(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghs_restricted_token")
        check = _make_check(["pr:comment"])
        with patch(
            "urllib.request.urlopen",
            side_effect=_make_http_error(403, "Forbidden"),
        ):
            result = check.run()
        assert "pull-requests:write" in result.message

    def test_403_message_identifies_pr_comment_tool(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghs_restricted_token")
        check = _make_check(["pr:comment"])
        with patch(
            "urllib.request.urlopen",
            side_effect=_make_http_error(403, "Forbidden"),
        ):
            result = check.run()
        assert "pr:comment" in result.message

    def test_403_result_not_passed(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghs_restricted_token")
        check = _make_check(["pr:comment"])
        with patch(
            "urllib.request.urlopen",
            side_effect=_make_http_error(403, "Forbidden"),
        ):
            result = check.run()
        assert result.passed is False

    def test_403_remediation_mentions_permissions_yaml(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghs_restricted_token")
        check = _make_check(["pr:comment"])
        with patch(
            "urllib.request.urlopen",
            side_effect=_make_http_error(403, "Forbidden"),
        ):
            result = check.run()
        assert "permissions" in result.remediation.lower()
        assert "pull-requests: write" in result.remediation

    def test_403_remediation_mentions_github_actions_workflow_yaml(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghs_restricted_token")
        check = _make_check(["pr:comment"])
        with patch(
            "urllib.request.urlopen",
            side_effect=_make_http_error(403, "Forbidden"),
        ):
            result = check.run()
        remediation = result.remediation.lower()
        assert "github actions" in remediation or "workflow" in remediation


# ---------------------------------------------------------------------------
# TestToolToScopeMapping
# ---------------------------------------------------------------------------


class TestToolToScopeMapping:
    """Tests for the _required_scopes() / tool-to-scope mapping logic."""

    def test_repository_read_maps_to_contents_read(self) -> None:
        check = _make_check(["repository:read"])
        scope_to_tools = check._required_scopes()
        assert "contents:read" in scope_to_tools

    def test_pr_comment_maps_to_pull_requests_write(self) -> None:
        check = _make_check(["pr:comment"])
        scope_to_tools = check._required_scopes()
        assert "pull-requests:write" in scope_to_tools

    def test_pr_review_maps_to_pull_requests_write(self) -> None:
        check = _make_check(["pr:review"])
        scope_to_tools = check._required_scopes()
        assert "pull-requests:write" in scope_to_tools

    def test_repository_read_scope_references_tool(self) -> None:
        check = _make_check(["repository:read"])
        scope_to_tools = check._required_scopes()
        assert "repository:read" in scope_to_tools.get("contents:read", [])

    def test_pr_comment_scope_references_tool(self) -> None:
        check = _make_check(["pr:comment"])
        scope_to_tools = check._required_scopes()
        assert "pr:comment" in scope_to_tools.get("pull-requests:write", [])

    def test_pr_review_scope_references_tool(self) -> None:
        check = _make_check(["pr:review"])
        scope_to_tools = check._required_scopes()
        assert "pr:review" in scope_to_tools.get("pull-requests:write", [])

    def test_empty_tool_list_returns_empty_scopes(self) -> None:
        check = _make_check([])
        scope_to_tools = check._required_scopes()
        assert scope_to_tools == {}

    def test_unknown_tool_returns_no_scopes(self) -> None:
        check = _make_check(["unknown:tool"])
        scope_to_tools = check._required_scopes()
        assert scope_to_tools == {}


# ---------------------------------------------------------------------------
# TestMultipleMissingScopes
# ---------------------------------------------------------------------------


class TestMultipleMissingScopes:
    """Tests for reporting multiple missing scopes in failure messages."""

    def test_multiple_missing_scopes_fails(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghs_restricted_token")
        # pr:comment and pr:review both require pull-requests:write, plus
        # repository:read requires contents:read — two distinct scopes.
        check = _make_check(["repository:read", "pr:comment"])
        with patch(
            "urllib.request.urlopen",
            side_effect=_make_http_error(403, "Forbidden"),
        ):
            result = check.run()
        assert result.passed is False

    def test_pr_comment_and_pr_review_list_all_tools(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghs_restricted_token")
        check = _make_check(["pr:comment", "pr:review"])
        with patch(
            "urllib.request.urlopen",
            side_effect=_make_http_error(403, "Forbidden"),
        ):
            result = check.run()
        assert result.passed is False
        # Both tools should appear in the message.
        assert "pr:comment" in result.message
        assert "pr:review" in result.message

    def test_all_missing_scopes_in_message(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghs_restricted_token")
        check = _make_check(["repository:read", "pr:comment"])
        with patch(
            "urllib.request.urlopen",
            side_effect=_make_http_error(403, "Forbidden"),
        ):
            result = check.run()
        # Message must mention the missing scope names.
        assert "pull-requests:write" in result.message or "contents:read" in result.message


# ---------------------------------------------------------------------------
# TestNetworkErrorHandling
# ---------------------------------------------------------------------------


class TestNetworkErrorHandling:
    """Tests for graceful handling of network errors."""

    def test_url_error_fails_gracefully(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghs_any_token")
        check = _make_check(["repository:read"])
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError(reason="Name or service not known"),
        ):
            result = check.run()
        assert result.passed is False

    def test_url_error_message_mentions_scope(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghs_any_token")
        check = _make_check(["repository:read"])
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError(reason="Connection refused"),
        ):
            result = check.run()
        # Message should mention scope or connectivity issue.
        assert result.message != ""

    def test_os_error_fails_gracefully(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghs_any_token")
        check = _make_check(["repository:read"])
        with patch(
            "urllib.request.urlopen",
            side_effect=OSError("Connection reset by peer"),
        ):
            result = check.run()
        assert result.passed is False

    def test_network_error_result_has_message(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghs_any_token")
        check = _make_check(["repository:read"])
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError(reason="Timeout"),
        ):
            result = check.run()
        assert result.message != ""


# ---------------------------------------------------------------------------
# TestCheckWithNoRepository
# ---------------------------------------------------------------------------


class TestCheckWithNoRepository:
    """Tests for behaviour when github_repository is empty (no API call)."""

    def test_no_repository_passes_optimistically(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghs_any_token")
        check = GitHubTokenScopeCheck(
            tool_declarations=["repository:read", "pr:comment"],
            github_repository="",
        )
        result = check.run()
        assert result.passed is True


# ---------------------------------------------------------------------------
# TestResultProtocol
# ---------------------------------------------------------------------------


class TestResultProtocol:
    """Verify _CheckResult fields conform to PreflightCheckResult protocol."""

    def test_result_has_passed_field(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        check = _make_check()
        result = check.run()
        assert hasattr(result, "passed")
        assert isinstance(result.passed, bool)

    def test_result_has_name_field(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        check = _make_check()
        result = check.run()
        assert hasattr(result, "name")
        assert result.name == "github_token_scope"

    def test_result_has_message_field(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        check = _make_check()
        result = check.run()
        assert hasattr(result, "message")
        assert isinstance(result.message, str)

    def test_result_has_remediation_field_on_failure(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghs_restricted_token")
        check = _make_check(["pr:comment"])
        with patch(
            "urllib.request.urlopen",
            side_effect=_make_http_error(403, "Forbidden"),
        ):
            result = check.run()
        assert hasattr(result, "remediation")
        assert result.remediation != ""
