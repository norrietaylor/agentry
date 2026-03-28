"""Integration tests for T03: Issue Triage Pipeline Output.

Tests the end-to-end flow of:
- GitHubActionsBinder.map_outputs() on an issues event
- Triage comment formatting (_format_triage_comment) renders all triage fields
- _post_issue_comment() is called with the formatted Markdown
- _apply_triage_labels() applies severity:{value} and category:{value} labels
- Label application failure does not propagate
- The execution record and output paths are returned correctly
- Complete triage output (all fields) produces a well-structured comment
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from agentry.binders.github_actions import GitHubActionsBinder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_issues_env(
    tmp_path: Path,
    issue_number: int = 42,
    token: str = "ghp_pipeline_token",
    repository: str = "myorg/myrepo",
) -> dict[str, str]:
    """Build a full issues event environment for integration testing."""
    payload = {
        "issue": {
            "number": issue_number,
            "title": "Database connection pool exhausted under load",
            "body": (
                "When running 500+ concurrent requests the connection pool runs out "
                "and new requests time out."
            ),
        }
    }
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(payload), encoding="utf-8")
    ws = str(tmp_path / "workspace")
    Path(ws).mkdir(parents=True, exist_ok=True)
    return {
        "GITHUB_EVENT_NAME": "issues",
        "GITHUB_EVENT_PATH": str(event_file),
        "GITHUB_WORKSPACE": ws,
        "GITHUB_REPOSITORY": repository,
        "GITHUB_TOKEN": token,
    }


def _write_output_json(runs_dir: Path, agent_output: dict[str, Any]) -> Path:
    """Write a complete triage output.json file."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    output_path = runs_dir / "output.json"
    data = {
        "output": agent_output,
        "token_usage": {"input_tokens": 1200, "output_tokens": 350},
    }
    output_path.write_text(json.dumps(data), encoding="utf-8")
    return output_path


def _mock_success(body: Any = None) -> MagicMock:
    """Return a mock httpx response for a 201 Created."""
    if body is None:
        body = {"id": 1}
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
# Complete triage pipeline flow
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestIssueTriagePipelineFlow:
    """Integration tests for the full triage pipeline output flow."""

    def test_complete_triage_output_posts_comment_and_labels(
        self, tmp_path: Path
    ) -> None:
        """Full triage output triggers both comment and label API calls."""
        env = _make_issues_env(tmp_path, issue_number=42)
        binder = GitHubActionsBinder(env=env)
        workspace = env["GITHUB_WORKSPACE"]
        run_id = "20260327T120000"
        runs_dir = Path(workspace) / ".agentry" / "runs" / run_id
        _write_output_json(
            runs_dir,
            {
                "severity": "high",
                "category": "performance",
                "affected_components": ["database", "connection-pool"],
                "recommended_assignee": "platform-team",
                "reasoning": "Connection pool exhaustion causes cascading timeouts.",
            },
        )

        with patch("httpx.post", return_value=_mock_success()) as mock_post:
            paths = binder.map_outputs({}, target_dir="/ignored", run_id=run_id)

        assert "output" in paths
        assert "execution_record" in paths
        assert mock_post.call_count == 2

    def test_comment_body_contains_all_triage_fields(self, tmp_path: Path) -> None:
        """The posted comment body contains severity, category, components, assignee, reasoning."""
        env = _make_issues_env(tmp_path, issue_number=10, repository="myorg/myrepo")
        binder = GitHubActionsBinder(env=env)
        workspace = env["GITHUB_WORKSPACE"]
        run_id = "run_fields"
        runs_dir = Path(workspace) / ".agentry" / "runs" / run_id
        _write_output_json(
            runs_dir,
            {
                "severity": "critical",
                "category": "security",
                "affected_components": ["auth-service"],
                "recommended_assignee": "security-team",
                "reasoning": "Remote code execution vulnerability.",
            },
        )
        captured_body: list[str] = []

        def _capture(url: str, **kwargs: Any) -> MagicMock:
            if "comments" in url:
                captured_body.append(kwargs["json"]["body"])
            return _mock_success()

        with patch("httpx.post", side_effect=_capture):
            binder.map_outputs({}, target_dir="/ignored", run_id=run_id)

        assert captured_body, "No comment was posted"
        comment = captured_body[0]
        assert "critical" in comment.lower()
        assert "security" in comment
        assert "auth-service" in comment
        assert "security-team" in comment
        assert "Remote code execution" in comment
        assert "Agentry Issue Triage" in comment

    def test_labels_posted_with_correct_format(self, tmp_path: Path) -> None:
        """Labels are posted as severity:{value} and category:{value}."""
        env = _make_issues_env(tmp_path, issue_number=55)
        binder = GitHubActionsBinder(env=env)
        workspace = env["GITHUB_WORKSPACE"]
        run_id = "run_label_format"
        runs_dir = Path(workspace) / ".agentry" / "runs" / run_id
        _write_output_json(
            runs_dir,
            {"severity": "medium", "category": "usability"},
        )
        captured_labels: list[list[str]] = []

        def _capture(url: str, **kwargs: Any) -> MagicMock:
            if "labels" in url:
                captured_labels.append(kwargs["json"]["labels"])
            return _mock_success()

        with patch("httpx.post", side_effect=_capture):
            binder.map_outputs({}, target_dir="/ignored", run_id=run_id)

        assert captured_labels, "No labels were posted"
        labels = captured_labels[0]
        assert "severity:medium" in labels
        assert "category:usability" in labels

    def test_label_api_failure_does_not_abort_run(self, tmp_path: Path) -> None:
        """When label API returns 422, map_outputs() still succeeds."""
        env = _make_issues_env(tmp_path, issue_number=7)
        binder = GitHubActionsBinder(env=env)
        workspace = env["GITHUB_WORKSPACE"]
        run_id = "run_label_failure"
        runs_dir = Path(workspace) / ".agentry" / "runs" / run_id
        _write_output_json(
            runs_dir,
            {"severity": "low", "category": "documentation"},
        )

        def _side_effect(url: str, **kwargs: Any) -> MagicMock:
            if "labels" in url:
                return _mock_error(422, "Unprocessable Entity")
            return _mock_success()

        with patch("httpx.post", side_effect=_side_effect):
            paths = binder.map_outputs({}, target_dir="/ignored", run_id=run_id)

        assert "output" in paths

    def test_label_timeout_does_not_abort_run(self, tmp_path: Path) -> None:
        """When label API times out, map_outputs() still succeeds."""
        env = _make_issues_env(tmp_path, issue_number=8)
        binder = GitHubActionsBinder(env=env)
        workspace = env["GITHUB_WORKSPACE"]
        run_id = "run_label_timeout"
        runs_dir = Path(workspace) / ".agentry" / "runs" / run_id
        _write_output_json(
            runs_dir,
            {"severity": "high", "category": "bug"},
        )

        def _side_effect(url: str, **kwargs: Any) -> MagicMock:
            if "labels" in url:
                raise httpx.TimeoutException("timeout")
            return _mock_success()

        with patch("httpx.post", side_effect=_side_effect):
            paths = binder.map_outputs({}, target_dir="/ignored", run_id=run_id)

        assert "output" in paths

    def test_comment_uses_correct_endpoint_for_issue(self, tmp_path: Path) -> None:
        """Comment is posted to issues/{number}/comments, not PR endpoint."""
        env = _make_issues_env(tmp_path, issue_number=99, repository="myorg/myrepo")
        binder = GitHubActionsBinder(env=env)
        workspace = env["GITHUB_WORKSPACE"]
        run_id = "run_endpoint"
        runs_dir = Path(workspace) / ".agentry" / "runs" / run_id
        _write_output_json(runs_dir, {"severity": "low"})

        with patch("httpx.post", return_value=_mock_success()) as mock_post:
            binder.map_outputs({}, target_dir="/ignored", run_id=run_id)

        all_urls = [
            (c.args[0] if c.args else c[0][0])
            for c in mock_post.call_args_list
        ]
        comment_urls = [u for u in all_urls if "comments" in u]
        assert comment_urls
        assert all("repos/myorg/myrepo/issues/99/comments" in u for u in comment_urls)

    def test_output_missing_posts_fallback_comment(self, tmp_path: Path) -> None:
        """When output.json doesn't exist, a fallback comment is still posted."""
        env = _make_issues_env(tmp_path, issue_number=5)
        binder = GitHubActionsBinder(env=env)
        run_id = "run_no_output"
        captured_body: list[str] = []

        def _capture(url: str, **kwargs: Any) -> MagicMock:
            if "comments" in url:
                captured_body.append(kwargs["json"]["body"])
            return _mock_success()

        with patch("httpx.post", side_effect=_capture):
            binder.map_outputs({}, target_dir="/ignored", run_id=run_id)

        assert captured_body, "No comment was posted"
        assert captured_body[0]  # some non-empty body

    def test_runs_dir_created_for_issues_event(self, tmp_path: Path) -> None:
        """map_outputs() creates the .agentry/runs/<run_id>/ directory."""
        env = _make_issues_env(tmp_path, issue_number=3)
        binder = GitHubActionsBinder(env=env)
        workspace = env["GITHUB_WORKSPACE"]
        run_id = "run_dir_create"
        expected_dir = Path(workspace) / ".agentry" / "runs" / run_id

        assert not expected_dir.exists()

        with patch("httpx.post", return_value=_mock_success()):
            binder.map_outputs({}, target_dir="/ignored", run_id=run_id)

        assert expected_dir.exists()

    def test_token_usage_included_in_comment(self, tmp_path: Path) -> None:
        """Token usage is included in the formatted triage comment."""
        env = _make_issues_env(tmp_path, issue_number=20)
        binder = GitHubActionsBinder(env=env)
        workspace = env["GITHUB_WORKSPACE"]
        run_id = "run_tokens"
        runs_dir = Path(workspace) / ".agentry" / "runs" / run_id
        runs_dir.mkdir(parents=True, exist_ok=True)
        (runs_dir / "output.json").write_text(
            json.dumps({
                "output": {"severity": "medium", "category": "bug"},
                "token_usage": {"input_tokens": 2500, "output_tokens": 750},
            }),
            encoding="utf-8",
        )
        captured_body: list[str] = []

        def _capture(url: str, **kwargs: Any) -> MagicMock:
            if "comments" in url:
                captured_body.append(kwargs["json"]["body"])
            return _mock_success()

        with patch("httpx.post", side_effect=_capture):
            binder.map_outputs({}, target_dir="/ignored", run_id=run_id)

        comment = captured_body[0]
        assert "2,500" in comment
        assert "750" in comment
