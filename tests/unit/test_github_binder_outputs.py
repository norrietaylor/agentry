"""Unit tests for T02.2: GitHubActionsBinder map_outputs().

Tests cover:
- map_outputs() writes output paths rooted at GITHUB_WORKSPACE/.agentry/runs/<run_id>/
- map_outputs() always includes 'output' and 'execution_record' keys
- map_outputs() creates run directory if it does not exist
- map_outputs() on non-PR event: returns paths without posting a PR comment
- map_outputs() on PR event: posts output as PR comment via GitHub API
- map_outputs() on PR event with existing output.json: reads file content for comment body
- Extra output_paths in output_declarations are included in mapping
- 403 error from GitHub API raises RuntimeError with scope remediation guidance
- 404 error from GitHub API raises RuntimeError with PR not found message
- Network timeout from GitHub API raises RuntimeError with timeout message
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from agentry.binders.github_actions import (
    GitHubActionsBinder,
)

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
    # Ensure workspace exists for directory creation tests.
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
        body = {"id": 1, "body": "test"}
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
# Path structure tests
# ---------------------------------------------------------------------------


class TestMapOutputsPathStructure:
    """Verify that map_outputs() returns correct path structure."""

    def test_returns_output_and_execution_record_keys(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        paths = binder_push.map_outputs({}, target_dir="/ignored", run_id="20260321T120000")
        assert "output" in paths
        assert "execution_record" in paths

    def test_paths_rooted_at_github_workspace(
        self, binder_push: GitHubActionsBinder, push_env: dict[str, str]
    ) -> None:
        workspace = push_env["GITHUB_WORKSPACE"]
        run_id = "20260321T120000"
        paths = binder_push.map_outputs({}, target_dir="/ignored", run_id=run_id)
        expected_output = str(Path(workspace) / ".agentry" / "runs" / run_id / "output.json")
        expected_record = str(Path(workspace) / ".agentry" / "runs" / run_id / "execution-record.json")
        assert paths["output"] == expected_output
        assert paths["execution_record"] == expected_record

    def test_target_dir_param_ignored_uses_workspace(
        self, binder_push: GitHubActionsBinder, push_env: dict[str, str]
    ) -> None:
        workspace = push_env["GITHUB_WORKSPACE"]
        paths = binder_push.map_outputs(
            {}, target_dir="/some/other/dir", run_id="run1"
        )
        assert paths["output"].startswith(workspace)

    def test_creates_run_directory(
        self, binder_push: GitHubActionsBinder, push_env: dict[str, str]
    ) -> None:
        run_id = "20260321T090000"
        workspace = push_env["GITHUB_WORKSPACE"]
        expected_dir = Path(workspace) / ".agentry" / "runs" / run_id
        assert not expected_dir.exists()
        binder_push.map_outputs({}, target_dir="/ignored", run_id=run_id)
        assert expected_dir.exists()

    def test_extra_output_paths_included(
        self, binder_push: GitHubActionsBinder, push_env: dict[str, str]
    ) -> None:
        workspace = push_env["GITHUB_WORKSPACE"]
        run_id = "run2"
        output_declarations = {"output_paths": ["report.html", "summary.txt"]}
        paths = binder_push.map_outputs(
            output_declarations, target_dir="/ignored", run_id=run_id
        )
        assert "report" in paths
        assert "summary" in paths
        assert paths["report"].endswith("report.html")
        assert paths["summary"].endswith("summary.txt")
        runs_dir = str(Path(workspace) / ".agentry" / "runs" / run_id)
        assert paths["report"].startswith(runs_dir)


# ---------------------------------------------------------------------------
# Non-PR event: no comment posted
# ---------------------------------------------------------------------------


class TestMapOutputsNonPR:
    """Verify map_outputs() on non-PR events does not post a comment."""

    def test_push_event_does_not_call_httpx_post(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        with patch("httpx.post") as mock_post:
            binder_push.map_outputs({}, target_dir="/ignored", run_id="runX")
        mock_post.assert_not_called()

    def test_push_event_returns_paths(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        paths = binder_push.map_outputs({}, target_dir="/ignored", run_id="runX")
        assert isinstance(paths, dict)
        assert len(paths) >= 2


# ---------------------------------------------------------------------------
# PR event: comment is posted
# ---------------------------------------------------------------------------


class TestMapOutputsPR:
    """Verify map_outputs() on PR events posts a comment via GitHub API."""

    def test_pr_event_posts_comment(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        with patch("httpx.post", return_value=_mock_httpx_post_success()) as mock_post:
            binder_pr.map_outputs({}, target_dir="/ignored", run_id="run42")
        mock_post.assert_called_once()

    def test_pr_comment_posts_to_correct_url(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        with patch("httpx.post", return_value=_mock_httpx_post_success()) as mock_post:
            binder_pr.map_outputs({}, target_dir="/ignored", run_id="run42")
        posted_url = mock_post.call_args.args[0] if mock_post.call_args.args else mock_post.call_args[0][0]
        assert "repos/owner/repo/issues/42/comments" in posted_url

    def test_pr_comment_uses_correct_auth_header(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        with patch("httpx.post", return_value=_mock_httpx_post_success()) as mock_post:
            binder_pr.map_outputs({}, target_dir="/ignored", run_id="run42")
        call_kwargs = mock_post.call_args.kwargs
        headers = call_kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer ghp_testtoken"

    def test_pr_comment_reads_output_json_when_exists(
        self, binder_pr: GitHubActionsBinder, pr_env: dict[str, str]
    ) -> None:
        workspace = pr_env["GITHUB_WORKSPACE"]
        run_id = "runWithOutput"
        runs_dir = Path(workspace) / ".agentry" / "runs" / run_id
        runs_dir.mkdir(parents=True, exist_ok=True)
        output_content = '{"result": "code review complete"}'
        (runs_dir / "output.json").write_text(output_content, encoding="utf-8")

        with patch("httpx.post", return_value=_mock_httpx_post_success()) as mock_post:
            binder_pr.map_outputs({}, target_dir="/ignored", run_id=run_id)

        call_kwargs = mock_post.call_args.kwargs
        posted_body = call_kwargs.get("json", {}).get("body", "")
        assert posted_body == output_content

    def test_pr_comment_fallback_when_output_json_missing(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        with patch("httpx.post", return_value=_mock_httpx_post_success()) as mock_post:
            binder_pr.map_outputs({}, target_dir="/ignored", run_id="runNoOutput")

        call_kwargs = mock_post.call_args.kwargs
        posted_body = call_kwargs.get("json", {}).get("body", "")
        # When output.json does not exist, a fallback message is used.
        assert posted_body  # some non-empty body was posted

    def test_pr_event_still_returns_paths(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        with patch("httpx.post", return_value=_mock_httpx_post_success()):
            paths = binder_pr.map_outputs({}, target_dir="/ignored", run_id="run42")
        assert "output" in paths
        assert "execution_record" in paths


# ---------------------------------------------------------------------------
# GitHub API error handling
# ---------------------------------------------------------------------------


class TestMapOutputsAPIErrors:
    """Verify structured error messages on GitHub API failures."""

    def test_403_error_includes_scope_remediation(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        error_response = _mock_httpx_error_response(403, "Forbidden")
        with patch("httpx.post", return_value=error_response), pytest.raises(RuntimeError) as exc_info:
            binder_pr.map_outputs({}, target_dir="/ignored", run_id="runErr")
        msg = str(exc_info.value)
        assert "403" in msg
        assert "pull_requests:write" in msg

    def test_404_error_includes_pr_not_found_info(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        error_response = _mock_httpx_error_response(404, "Not Found")
        with patch("httpx.post", return_value=error_response), pytest.raises(RuntimeError) as exc_info:
            binder_pr.map_outputs({}, target_dir="/ignored", run_id="runErr")
        msg = str(exc_info.value)
        assert "404" in msg
        assert "42" in msg  # PR number

    def test_network_timeout_raises_runtime_error(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        with patch("httpx.post", side_effect=httpx.TimeoutException("timed out")), pytest.raises(RuntimeError) as exc_info:
            binder_pr.map_outputs({}, target_dir="/ignored", run_id="runTimeout")
        msg = str(exc_info.value)
        assert "timeout" in msg.lower()

    def test_other_http_error_includes_generic_remediation(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        error_response = _mock_httpx_error_response(500, "Internal Server Error")
        with patch("httpx.post", return_value=error_response), pytest.raises(RuntimeError) as exc_info:
            binder_pr.map_outputs({}, target_dir="/ignored", run_id="runErr")
        msg = str(exc_info.value)
        assert "500" in msg
