"""Unit tests for T03: Triage Output Formatting and Label Derivation.

Tests cover:
- _format_triage_comment() renders severity badge, category, components, assignee, reasoning
- _format_triage_comment() falls back gracefully when output.json is missing
- _format_triage_comment() falls back to raw JSON for unrecognised structure
- _format_triage_comment() includes token usage when present
- _format_triage_comment() includes raw_response fallback when no structured data
- map_outputs() on issues event: posts triage comment via GitHub API
- map_outputs() on issues event: attempts to apply severity and category labels
- map_outputs() on issues event: label failure is best-effort (no exception propagated)
- map_outputs() on issues event: skips label application when output has no severity/category
- _apply_triage_labels() logs warning and skips when output.json missing
- _apply_triage_labels() logs warning and skips when output.json is malformed JSON
- _post_issue_comment() posts to correct endpoint with auth header
- _post_issue_comment() 403 raises RuntimeError with issues:write remediation
- _post_issue_comment() 404 raises RuntimeError with issue number
- _post_issue_comment() timeout raises RuntimeError
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import httpx
import pytest

from agentry.binders.github_actions import GitHubActionsBinder

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


def _make_issues_env(
    tmp_path: Path,
    issue_number: int = 7,
    token: str = "ghp_testtoken",
    repository: str = "owner/repo",
) -> dict[str, str]:
    """Build an issues event environment dict."""
    payload = {"issue": {"number": issue_number, "title": "Sample issue"}}
    return _make_env(
        tmp_path,
        event_name="issues",
        payload=payload,
        token=token,
        repository=repository,
    )


def _make_success_mock(body: Any = None) -> MagicMock:
    """Return a mock httpx response representing a successful 201 Created."""
    if body is None:
        body = {"id": 1}
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.json.return_value = body
    mock_response.raise_for_status.return_value = None
    return mock_response


def _make_error_mock(status_code: int, text: str = "Error") -> MagicMock:
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


@pytest.fixture()
def issues_env(tmp_path: Path) -> dict[str, str]:
    """Environment for an issues event with issue #7."""
    return _make_issues_env(tmp_path)


@pytest.fixture()
def binder_issues(issues_env: dict[str, str]) -> GitHubActionsBinder:
    """GitHubActionsBinder instantiated for an issues event."""
    return GitHubActionsBinder(env=issues_env)


def _write_triage_output(runs_dir: Path, agent_output: dict[str, Any]) -> Path:
    """Write a triage output.json to the given runs directory."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    output_path = runs_dir / "output.json"
    output_path.write_text(
        json.dumps({"output": agent_output, "token_usage": {"input_tokens": 200, "output_tokens": 80}}),
        encoding="utf-8",
    )
    return output_path


# ---------------------------------------------------------------------------
# _format_triage_comment()
# ---------------------------------------------------------------------------


class TestFormatTriageComment:
    """Unit tests for _format_triage_comment()."""

    def test_includes_severity_badge_for_critical(
        self, binder_issues: GitHubActionsBinder, tmp_path: Path
    ) -> None:
        output_path = tmp_path / "output.json"
        output_path.write_text(
            json.dumps({"output": {"severity": "critical", "category": "bug", "reasoning": "bad"}}),
            encoding="utf-8",
        )
        result = binder_issues._format_triage_comment(output_path)
        assert "critical" in result.lower()
        assert "## Agentry Issue Triage" in result

    def test_includes_severity_badge_for_high(
        self, binder_issues: GitHubActionsBinder, tmp_path: Path
    ) -> None:
        output_path = tmp_path / "output.json"
        output_path.write_text(
            json.dumps({"output": {"severity": "high", "category": "performance"}}),
            encoding="utf-8",
        )
        result = binder_issues._format_triage_comment(output_path)
        assert "high" in result.lower()

    def test_includes_severity_badge_for_medium(
        self, binder_issues: GitHubActionsBinder, tmp_path: Path
    ) -> None:
        output_path = tmp_path / "output.json"
        output_path.write_text(
            json.dumps({"output": {"severity": "medium"}}),
            encoding="utf-8",
        )
        result = binder_issues._format_triage_comment(output_path)
        assert "medium" in result.lower()

    def test_includes_severity_badge_for_low(
        self, binder_issues: GitHubActionsBinder, tmp_path: Path
    ) -> None:
        output_path = tmp_path / "output.json"
        output_path.write_text(
            json.dumps({"output": {"severity": "low"}}),
            encoding="utf-8",
        )
        result = binder_issues._format_triage_comment(output_path)
        assert "low" in result.lower()

    def test_includes_category(
        self, binder_issues: GitHubActionsBinder, tmp_path: Path
    ) -> None:
        output_path = tmp_path / "output.json"
        output_path.write_text(
            json.dumps({"output": {"severity": "high", "category": "security"}}),
            encoding="utf-8",
        )
        result = binder_issues._format_triage_comment(output_path)
        assert "security" in result
        assert "Category" in result

    def test_includes_affected_components(
        self, binder_issues: GitHubActionsBinder, tmp_path: Path
    ) -> None:
        output_path = tmp_path / "output.json"
        output_path.write_text(
            json.dumps({"output": {
                "severity": "medium",
                "affected_components": ["auth-service", "api-gateway"],
            }}),
            encoding="utf-8",
        )
        result = binder_issues._format_triage_comment(output_path)
        assert "auth-service" in result
        assert "api-gateway" in result
        assert "Affected Components" in result

    def test_includes_recommended_assignee(
        self, binder_issues: GitHubActionsBinder, tmp_path: Path
    ) -> None:
        output_path = tmp_path / "output.json"
        output_path.write_text(
            json.dumps({"output": {"severity": "low", "recommended_assignee": "backend-team"}}),
            encoding="utf-8",
        )
        result = binder_issues._format_triage_comment(output_path)
        assert "backend-team" in result
        assert "Recommended Assignee" in result

    def test_includes_reasoning(
        self, binder_issues: GitHubActionsBinder, tmp_path: Path
    ) -> None:
        output_path = tmp_path / "output.json"
        output_path.write_text(
            json.dumps({"output": {
                "severity": "high",
                "reasoning": "This affects production users directly.",
            }}),
            encoding="utf-8",
        )
        result = binder_issues._format_triage_comment(output_path)
        assert "This affects production users directly." in result
        assert "Reasoning" in result

    def test_includes_token_usage_when_present(
        self, binder_issues: GitHubActionsBinder, tmp_path: Path
    ) -> None:
        output_path = tmp_path / "output.json"
        output_path.write_text(
            json.dumps({
                "output": {"severity": "low"},
                "token_usage": {"input_tokens": 1000, "output_tokens": 500},
            }),
            encoding="utf-8",
        )
        result = binder_issues._format_triage_comment(output_path)
        assert "1,000" in result
        assert "500" in result
        assert "Tokens" in result

    def test_fallback_when_output_json_missing(
        self, binder_issues: GitHubActionsBinder, tmp_path: Path
    ) -> None:
        output_path = tmp_path / "does_not_exist.json"
        result = binder_issues._format_triage_comment(output_path)
        assert result  # some non-empty message
        assert "not found" in result.lower() or "Triage output" in result

    def test_fallback_for_invalid_json(
        self, binder_issues: GitHubActionsBinder, tmp_path: Path
    ) -> None:
        output_path = tmp_path / "output.json"
        output_path.write_text("not valid json{{{", encoding="utf-8")
        result = binder_issues._format_triage_comment(output_path)
        assert "Triage Output" in result or "```" in result

    def test_fallback_to_raw_response_when_no_structured_data(
        self, binder_issues: GitHubActionsBinder, tmp_path: Path
    ) -> None:
        output_path = tmp_path / "output.json"
        output_path.write_text(
            json.dumps({"output": {"raw_response": "Looks like a bug to me."}}),
            encoding="utf-8",
        )
        result = binder_issues._format_triage_comment(output_path)
        assert "Looks like a bug to me." in result

    def test_full_triage_output_renders_all_sections(
        self, binder_issues: GitHubActionsBinder, tmp_path: Path
    ) -> None:
        output_path = tmp_path / "output.json"
        output_path.write_text(
            json.dumps({"output": {
                "severity": "critical",
                "category": "security",
                "affected_components": ["auth", "database"],
                "recommended_assignee": "security-team",
                "reasoning": "SQL injection vulnerability.",
            }}),
            encoding="utf-8",
        )
        result = binder_issues._format_triage_comment(output_path)
        assert "critical" in result.lower()
        assert "security" in result
        assert "auth" in result
        assert "database" in result
        assert "security-team" in result
        assert "SQL injection" in result


# ---------------------------------------------------------------------------
# _post_issue_comment()
# ---------------------------------------------------------------------------


class TestPostIssueComment:
    """Unit tests for _post_issue_comment()."""

    def test_posts_to_correct_endpoint(
        self, binder_issues: GitHubActionsBinder
    ) -> None:
        with patch("httpx.post", return_value=_make_success_mock()) as mock_post:
            binder_issues._post_issue_comment("hello")
        posted_url = (
            mock_post.call_args.args[0]
            if mock_post.call_args.args
            else mock_post.call_args[0][0]
        )
        assert "repos/owner/repo/issues/7/comments" in posted_url

    def test_includes_authorization_header(
        self, binder_issues: GitHubActionsBinder
    ) -> None:
        with patch("httpx.post", return_value=_make_success_mock()) as mock_post:
            binder_issues._post_issue_comment("body text")
        headers = mock_post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer ghp_testtoken"

    def test_includes_body_in_payload(
        self, binder_issues: GitHubActionsBinder
    ) -> None:
        with patch("httpx.post", return_value=_make_success_mock()) as mock_post:
            binder_issues._post_issue_comment("my comment body")
        assert mock_post.call_args.kwargs["json"]["body"] == "my comment body"

    def test_returns_response_json(
        self, binder_issues: GitHubActionsBinder
    ) -> None:
        api_body = {"id": 99, "body": "posted"}
        with patch("httpx.post", return_value=_make_success_mock(api_body)):
            result = binder_issues._post_issue_comment("test")
        assert result == api_body

    def test_403_raises_runtime_with_issues_write_remediation(
        self, binder_issues: GitHubActionsBinder
    ) -> None:
        with patch("httpx.post", return_value=_make_error_mock(403)):
            with pytest.raises(RuntimeError) as exc_info:
                binder_issues._post_issue_comment("test")
        assert "403" in str(exc_info.value)
        assert "issues:write" in str(exc_info.value)

    def test_404_raises_runtime_with_issue_number(
        self, binder_issues: GitHubActionsBinder
    ) -> None:
        with patch("httpx.post", return_value=_make_error_mock(404)):
            with pytest.raises(RuntimeError) as exc_info:
                binder_issues._post_issue_comment("test")
        assert "404" in str(exc_info.value)
        assert "7" in str(exc_info.value)

    def test_timeout_raises_runtime_error(
        self, binder_issues: GitHubActionsBinder
    ) -> None:
        with patch("httpx.post", side_effect=httpx.TimeoutException("timeout")):
            with pytest.raises(RuntimeError) as exc_info:
                binder_issues._post_issue_comment("test")
        assert "timeout" in str(exc_info.value).lower()

    def test_other_http_error_includes_status(
        self, binder_issues: GitHubActionsBinder
    ) -> None:
        with patch("httpx.post", return_value=_make_error_mock(500, "Server error")):
            with pytest.raises(RuntimeError) as exc_info:
                binder_issues._post_issue_comment("test")
        assert "500" in str(exc_info.value)


# ---------------------------------------------------------------------------
# _apply_triage_labels()
# ---------------------------------------------------------------------------


class TestApplyTriageLabels:
    """Unit tests for _apply_triage_labels()."""

    def test_applies_severity_and_category_labels(
        self, binder_issues: GitHubActionsBinder, tmp_path: Path
    ) -> None:
        output_path = tmp_path / "output.json"
        output_path.write_text(
            json.dumps({"output": {"severity": "high", "category": "bug"}}),
            encoding="utf-8",
        )
        with patch("httpx.post", return_value=_make_success_mock()) as mock_post:
            binder_issues._apply_triage_labels(output_path)
        mock_post.assert_called_once()
        posted_labels = mock_post.call_args.kwargs["json"]["labels"]
        assert "severity:high" in posted_labels
        assert "category:bug" in posted_labels

    def test_applies_only_severity_when_category_missing(
        self, binder_issues: GitHubActionsBinder, tmp_path: Path
    ) -> None:
        output_path = tmp_path / "output.json"
        output_path.write_text(
            json.dumps({"output": {"severity": "critical"}}),
            encoding="utf-8",
        )
        with patch("httpx.post", return_value=_make_success_mock()) as mock_post:
            binder_issues._apply_triage_labels(output_path)
        posted_labels = mock_post.call_args.kwargs["json"]["labels"]
        assert "severity:critical" in posted_labels
        assert not any(l.startswith("category:") for l in posted_labels)

    def test_posts_to_correct_labels_endpoint(
        self, binder_issues: GitHubActionsBinder, tmp_path: Path
    ) -> None:
        output_path = tmp_path / "output.json"
        output_path.write_text(
            json.dumps({"output": {"severity": "low", "category": "usability"}}),
            encoding="utf-8",
        )
        with patch("httpx.post", return_value=_make_success_mock()) as mock_post:
            binder_issues._apply_triage_labels(output_path)
        posted_url = (
            mock_post.call_args.args[0]
            if mock_post.call_args.args
            else mock_post.call_args[0][0]
        )
        assert "repos/owner/repo/issues/7/labels" in posted_url

    def test_label_api_error_does_not_propagate(
        self, binder_issues: GitHubActionsBinder, tmp_path: Path
    ) -> None:
        """Label failure is best-effort; no exception is raised."""
        output_path = tmp_path / "output.json"
        output_path.write_text(
            json.dumps({"output": {"severity": "medium", "category": "performance"}}),
            encoding="utf-8",
        )
        with patch("httpx.post", return_value=_make_error_mock(403)):
            # Should not raise
            binder_issues._apply_triage_labels(output_path)

    def test_label_timeout_does_not_propagate(
        self, binder_issues: GitHubActionsBinder, tmp_path: Path
    ) -> None:
        """Network timeout on label API is best-effort; no exception is raised."""
        output_path = tmp_path / "output.json"
        output_path.write_text(
            json.dumps({"output": {"severity": "high", "category": "bug"}}),
            encoding="utf-8",
        )
        with patch("httpx.post", side_effect=httpx.TimeoutException("timeout")):
            # Should not raise
            binder_issues._apply_triage_labels(output_path)

    def test_logs_warning_when_output_missing(
        self, binder_issues: GitHubActionsBinder, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        output_path = tmp_path / "nonexistent.json"
        with patch("httpx.post") as mock_post, caplog.at_level(logging.WARNING):
            binder_issues._apply_triage_labels(output_path)
        mock_post.assert_not_called()
        assert any("does not exist" in r.message for r in caplog.records)

    def test_logs_warning_when_output_malformed(
        self, binder_issues: GitHubActionsBinder, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        output_path = tmp_path / "output.json"
        output_path.write_text("{{invalid json", encoding="utf-8")
        with patch("httpx.post") as mock_post, caplog.at_level(logging.WARNING):
            binder_issues._apply_triage_labels(output_path)
        mock_post.assert_not_called()
        assert any("could not parse" in r.message for r in caplog.records)

    def test_logs_warning_when_no_severity_or_category(
        self, binder_issues: GitHubActionsBinder, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        output_path = tmp_path / "output.json"
        output_path.write_text(
            json.dumps({"output": {"reasoning": "no labels here"}}),
            encoding="utf-8",
        )
        with patch("httpx.post") as mock_post, caplog.at_level(logging.WARNING):
            binder_issues._apply_triage_labels(output_path)
        mock_post.assert_not_called()
        assert any("no severity or category" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# map_outputs() on issues event
# ---------------------------------------------------------------------------


class TestMapOutputsIssuesEvent:
    """Verify map_outputs() behaviour on issues events."""

    def test_issues_event_posts_triage_comment(
        self, binder_issues: GitHubActionsBinder, issues_env: dict[str, str]
    ) -> None:
        workspace = issues_env["GITHUB_WORKSPACE"]
        run_id = "run_triage_01"
        runs_dir = Path(workspace) / ".agentry" / "runs" / run_id
        _write_triage_output(runs_dir, {"severity": "high", "category": "bug"})

        with patch("httpx.post", return_value=_make_success_mock()) as mock_post:
            binder_issues.map_outputs({}, target_dir="/ignored", run_id=run_id)

        # Two POSTs: comment + labels
        assert mock_post.call_count >= 1
        # At least one call should be to the comments endpoint
        all_urls = [
            (c.args[0] if c.args else c[0][0])
            for c in mock_post.call_args_list
        ]
        assert any("comments" in url for url in all_urls)

    def test_issues_event_posts_to_correct_comment_url(
        self, binder_issues: GitHubActionsBinder, issues_env: dict[str, str]
    ) -> None:
        workspace = issues_env["GITHUB_WORKSPACE"]
        run_id = "run_triage_url"
        runs_dir = Path(workspace) / ".agentry" / "runs" / run_id
        _write_triage_output(runs_dir, {"severity": "medium"})

        with patch("httpx.post", return_value=_make_success_mock()) as mock_post:
            binder_issues.map_outputs({}, target_dir="/ignored", run_id=run_id)

        all_urls = [
            (c.args[0] if c.args else c[0][0])
            for c in mock_post.call_args_list
        ]
        assert any("repos/owner/repo/issues/7/comments" in url for url in all_urls)

    def test_issues_event_attempts_label_application(
        self, binder_issues: GitHubActionsBinder, issues_env: dict[str, str]
    ) -> None:
        workspace = issues_env["GITHUB_WORKSPACE"]
        run_id = "run_triage_labels"
        runs_dir = Path(workspace) / ".agentry" / "runs" / run_id
        _write_triage_output(runs_dir, {"severity": "critical", "category": "security"})

        with patch("httpx.post", return_value=_make_success_mock()) as mock_post:
            binder_issues.map_outputs({}, target_dir="/ignored", run_id=run_id)

        # Should have called both comments and labels endpoints
        all_urls = [
            (c.args[0] if c.args else c[0][0])
            for c in mock_post.call_args_list
        ]
        assert any("labels" in url for url in all_urls)
        label_calls = [url for url in all_urls if "labels" in url]
        # Verify the labels posted include severity and category
        label_call_args = [
            c for c in mock_post.call_args_list
            if "labels" in (c.args[0] if c.args else c[0][0])
        ]
        if label_call_args:
            posted_labels = label_call_args[0].kwargs["json"]["labels"]
            assert "severity:critical" in posted_labels
            assert "category:security" in posted_labels

    def test_issues_event_label_failure_does_not_fail_map_outputs(
        self, binder_issues: GitHubActionsBinder, issues_env: dict[str, str]
    ) -> None:
        workspace = issues_env["GITHUB_WORKSPACE"]
        run_id = "run_triage_labelerr"
        runs_dir = Path(workspace) / ".agentry" / "runs" / run_id
        _write_triage_output(runs_dir, {"severity": "low", "category": "enhancement"})

        comment_response = _make_success_mock()
        label_response = _make_error_mock(403, "Forbidden")

        def _side_effect(url: str, **kwargs: Any) -> MagicMock:
            if "comments" in url:
                return comment_response
            return label_response

        with patch("httpx.post", side_effect=_side_effect):
            # Should NOT raise even though labels fail
            paths = binder_issues.map_outputs({}, target_dir="/ignored", run_id=run_id)

        assert "output" in paths

    def test_issues_event_returns_correct_paths(
        self, binder_issues: GitHubActionsBinder
    ) -> None:
        with patch("httpx.post", return_value=_make_success_mock()):
            paths = binder_issues.map_outputs({}, target_dir="/ignored", run_id="run99")
        assert "output" in paths
        assert "execution_record" in paths

    def test_non_issues_event_does_not_post_triage_comment(
        self, tmp_path: Path
    ) -> None:
        env = _make_env(tmp_path, event_name="push", payload={})
        binder = GitHubActionsBinder(env=env)

        with patch("httpx.post") as mock_post:
            binder.map_outputs({}, target_dir="/ignored", run_id="run_push")

        mock_post.assert_not_called()
