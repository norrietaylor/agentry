"""Unit tests for T01.3: GitHubActionsBinder input resolution.

Tests cover:
- GitHubActionsBinder satisfies EnvironmentBinder protocol (isinstance check).
- resolve_inputs for repository-ref: returns GITHUB_WORKSPACE value.
- resolve_inputs for git-diff: mocks httpx response, verifies API URL and Accept header.
- resolve_inputs for string type with workflow_dispatch inputs.
- resolve_inputs for string type with event payload field mapping (e.g. issue.title).
- Error: missing GITHUB_TOKEN raises clear error.
- Error: git-diff on non-PR event (push) raises ValueError with helpful message.
- Error: missing required input raises ValueError.
- Error: missing GITHUB_EVENT_PATH raises clear error.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentry.binders.github_actions import GitHubActionsBinder
from agentry.binders.protocol import EnvironmentBinder

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
    return {
        "GITHUB_EVENT_NAME": event_name,
        "GITHUB_EVENT_PATH": str(event_file),
        "GITHUB_WORKSPACE": workspace or str(tmp_path / "workspace"),
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


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    """GitHubActionsBinder satisfies the EnvironmentBinder protocol."""

    def test_is_environment_binder(self, binder_push: GitHubActionsBinder) -> None:
        assert isinstance(binder_push, EnvironmentBinder)

    def test_has_resolve_inputs(self, binder_push: GitHubActionsBinder) -> None:
        assert callable(binder_push.resolve_inputs)

    def test_has_bind_tools(self, binder_push: GitHubActionsBinder) -> None:
        assert callable(binder_push.bind_tools)

    def test_has_map_outputs(self, binder_push: GitHubActionsBinder) -> None:
        assert callable(binder_push.map_outputs)

    def test_has_generate_pipeline_config(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        assert callable(binder_push.generate_pipeline_config)

    def test_name_is_github_actions(self, binder_push: GitHubActionsBinder) -> None:
        assert binder_push.name == "github-actions"


# ---------------------------------------------------------------------------
# Construction errors: missing required environment variables
# ---------------------------------------------------------------------------


class TestConstructionErrors:
    """Missing required env vars raise ValueError at construction time."""

    def test_missing_github_token_raises_value_error(
        self, tmp_path: Path
    ) -> None:
        env = _make_env(tmp_path)
        del env["GITHUB_TOKEN"]
        with pytest.raises(ValueError, match="GITHUB_TOKEN"):
            GitHubActionsBinder(env=env)

    def test_missing_github_token_message_is_actionable(
        self, tmp_path: Path
    ) -> None:
        env = _make_env(tmp_path)
        del env["GITHUB_TOKEN"]
        with pytest.raises(ValueError, match="required"):
            GitHubActionsBinder(env=env)

    def test_missing_github_event_path_raises_value_error(
        self, tmp_path: Path
    ) -> None:
        env = _make_env(tmp_path)
        del env["GITHUB_EVENT_PATH"]
        with pytest.raises(ValueError, match="GITHUB_EVENT_PATH"):
            GitHubActionsBinder(env=env)

    def test_missing_github_event_path_message_is_actionable(
        self, tmp_path: Path
    ) -> None:
        env = _make_env(tmp_path)
        del env["GITHUB_EVENT_PATH"]
        with pytest.raises(ValueError, match="required"):
            GitHubActionsBinder(env=env)

    def test_missing_github_workspace_raises_value_error(
        self, tmp_path: Path
    ) -> None:
        env = _make_env(tmp_path)
        del env["GITHUB_WORKSPACE"]
        with pytest.raises(ValueError, match="GITHUB_WORKSPACE"):
            GitHubActionsBinder(env=env)

    def test_missing_github_event_name_raises_value_error(
        self, tmp_path: Path
    ) -> None:
        env = _make_env(tmp_path)
        del env["GITHUB_EVENT_NAME"]
        with pytest.raises(ValueError, match="GITHUB_EVENT_NAME"):
            GitHubActionsBinder(env=env)

    def test_missing_github_repository_raises_value_error(
        self, tmp_path: Path
    ) -> None:
        env = _make_env(tmp_path)
        del env["GITHUB_REPOSITORY"]
        with pytest.raises(ValueError, match="GITHUB_REPOSITORY"):
            GitHubActionsBinder(env=env)

    def test_invalid_event_json_raises_value_error(self, tmp_path: Path) -> None:
        env = _make_env(tmp_path)
        # Overwrite the event file with invalid JSON.
        Path(env["GITHUB_EVENT_PATH"]).write_text("not-json{{{", encoding="utf-8")
        with pytest.raises(ValueError):
            GitHubActionsBinder(env=env)

    def test_missing_event_file_raises_value_error(self, tmp_path: Path) -> None:
        env = _make_env(tmp_path)
        env["GITHUB_EVENT_PATH"] = str(tmp_path / "does_not_exist.json")
        with pytest.raises(ValueError):
            GitHubActionsBinder(env=env)


# ---------------------------------------------------------------------------
# resolve_inputs: repository-ref
# ---------------------------------------------------------------------------


class TestResolveInputsRepositoryRef:
    """repository-ref inputs resolve to GITHUB_WORKSPACE."""

    def test_returns_github_workspace_value(
        self, binder_push: GitHubActionsBinder, push_env: dict[str, str]
    ) -> None:
        declarations = {"repo": {"type": "repository-ref", "required": True}}
        result = binder_push.resolve_inputs(declarations, {})
        assert result["repo"] == push_env["GITHUB_WORKSPACE"]

    def test_returns_string(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        declarations = {"repo": {"type": "repository-ref", "required": True}}
        result = binder_push.resolve_inputs(declarations, {})
        assert isinstance(result["repo"], str)

    def test_ignores_provided_values(
        self, binder_push: GitHubActionsBinder, push_env: dict[str, str]
    ) -> None:
        """repository-ref always uses GITHUB_WORKSPACE regardless of provided values."""
        declarations = {"repo": {"type": "repository-ref", "required": True}}
        result = binder_push.resolve_inputs(declarations, {"repo": "/some/other/path"})
        assert result["repo"] == push_env["GITHUB_WORKSPACE"]


# ---------------------------------------------------------------------------
# resolve_inputs: git-diff
# ---------------------------------------------------------------------------


class TestResolveInputsGitDiff:
    """git-diff inputs fetch the PR diff from the GitHub API."""

    def test_fetches_diff_for_pr_event(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        fake_diff = "diff --git a/foo.py b/foo.py\n+added line\n"
        mock_response = MagicMock()
        mock_response.text = fake_diff
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_response) as mock_get:
            declarations = {"diff": {"type": "git-diff", "required": True}}
            result = binder_pr.resolve_inputs(declarations, {})

        assert result["diff"] == fake_diff
        mock_get.assert_called_once()

    def test_api_url_contains_pr_number(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        mock_response = MagicMock()
        mock_response.text = "diff content"
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_response) as mock_get:
            declarations = {"diff": {"type": "git-diff", "required": True}}
            binder_pr.resolve_inputs(declarations, {})

        call_url = mock_get.call_args[0][0]
        assert "42" in call_url  # PR #42 from pr_env fixture

    def test_api_url_contains_owner_repo(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        mock_response = MagicMock()
        mock_response.text = "diff content"
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_response) as mock_get:
            declarations = {"diff": {"type": "git-diff", "required": True}}
            binder_pr.resolve_inputs(declarations, {})

        call_url = mock_get.call_args[0][0]
        assert "owner/repo" in call_url

    def test_accept_header_is_diff_mime_type(
        self, binder_pr: GitHubActionsBinder
    ) -> None:
        mock_response = MagicMock()
        mock_response.text = "diff content"
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.get", return_value=mock_response) as mock_get:
            declarations = {"diff": {"type": "git-diff", "required": True}}
            binder_pr.resolve_inputs(declarations, {})

        call_headers = mock_get.call_args[1]["headers"]
        assert call_headers.get("Accept") == "application/vnd.github.diff"

    def test_raises_value_error_for_push_event(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        declarations = {"diff": {"type": "git-diff", "required": True}}
        with pytest.raises(ValueError):
            binder_push.resolve_inputs(declarations, {})

    def test_error_message_mentions_event_type_for_push(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        declarations = {"diff": {"type": "git-diff", "required": True}}
        with pytest.raises(ValueError, match="push"):
            binder_push.resolve_inputs(declarations, {})

    def test_error_message_mentions_pull_request(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        """The error message should mention pull_request as the required event type."""
        declarations = {"diff": {"type": "git-diff", "required": True}}
        with pytest.raises(ValueError, match="pull_request"):
            binder_push.resolve_inputs(declarations, {})

    def test_raises_value_error_for_non_pr_event(
        self, tmp_path: Path
    ) -> None:
        """workflow_dispatch event also cannot use git-diff."""
        env = _make_env(tmp_path, event_name="workflow_dispatch")
        binder = GitHubActionsBinder(env=env)
        declarations = {"diff": {"type": "git-diff", "required": True}}
        with pytest.raises(ValueError):
            binder.resolve_inputs(declarations, {})


# ---------------------------------------------------------------------------
# resolve_inputs: string with workflow_dispatch inputs
# ---------------------------------------------------------------------------


class TestResolveInputsStringWorkflowDispatch:
    """String inputs for workflow_dispatch events come from event payload inputs."""

    def test_resolves_from_dispatch_inputs(self, tmp_path: Path) -> None:
        payload = {"inputs": {"target_branch": "main", "dry_run": "true"}}
        env = _make_env(tmp_path, event_name="workflow_dispatch", payload=payload)
        binder = GitHubActionsBinder(env=env)
        declarations = {"target_branch": {"type": "string", "required": True}}
        result = binder.resolve_inputs(declarations, {})
        assert result["target_branch"] == "main"

    def test_resolves_multiple_dispatch_inputs(self, tmp_path: Path) -> None:
        payload = {"inputs": {"name": "alice", "mode": "fast"}}
        env = _make_env(tmp_path, event_name="workflow_dispatch", payload=payload)
        binder = GitHubActionsBinder(env=env)
        declarations = {
            "name": {"type": "string", "required": True},
            "mode": {"type": "string", "required": True},
        }
        result = binder.resolve_inputs(declarations, {})
        assert result["name"] == "alice"
        assert result["mode"] == "fast"

    def test_provided_value_overrides_dispatch_input(self, tmp_path: Path) -> None:
        """Explicitly provided values take precedence over dispatch payload."""
        payload = {"inputs": {"branch": "main"}}
        env = _make_env(tmp_path, event_name="workflow_dispatch", payload=payload)
        binder = GitHubActionsBinder(env=env)
        declarations = {"branch": {"type": "string", "required": True}}
        result = binder.resolve_inputs(declarations, {"branch": "feature-x"})
        assert result["branch"] == "feature-x"

    def test_dispatch_input_not_used_for_push_event(self, tmp_path: Path) -> None:
        """workflow_dispatch inputs are ignored for non-dispatch events."""
        payload = {"inputs": {"x": "y"}}
        env = _make_env(tmp_path, event_name="push", payload=payload)
        binder = GitHubActionsBinder(env=env)
        declarations = {"x": {"type": "string", "required": False}}
        result = binder.resolve_inputs(declarations, {})
        # For push events, dispatch inputs block is not consulted.
        assert result["x"] is None


# ---------------------------------------------------------------------------
# resolve_inputs: string with source (event payload field mapping)
# ---------------------------------------------------------------------------


class TestResolveInputsStringSourceMapping:
    """String inputs with 'source' resolve via dot-notation from the event payload."""

    def test_resolves_issue_title(self, tmp_path: Path) -> None:
        payload = {"issue": {"title": "Bug report", "number": 7}}
        env = _make_env(tmp_path, event_name="issues", payload=payload)
        binder = GitHubActionsBinder(env=env)
        declarations = {
            "title": {"type": "string", "required": True, "source": "issue.title"}
        }
        result = binder.resolve_inputs(declarations, {})
        assert result["title"] == "Bug report"

    def test_resolves_nested_field(self, tmp_path: Path) -> None:
        payload = {"pull_request": {"head": {"ref": "feature-branch"}}}
        env = _make_env(tmp_path, event_name="pull_request", payload=payload)
        binder = GitHubActionsBinder(env=env)
        declarations = {
            "branch": {
                "type": "string",
                "required": True,
                "source": "pull_request.head.ref",
            }
        }
        result = binder.resolve_inputs(declarations, {})
        assert result["branch"] == "feature-branch"

    def test_provided_value_overrides_source_mapping(self, tmp_path: Path) -> None:
        payload = {"issue": {"title": "Original title"}}
        env = _make_env(tmp_path, event_name="issues", payload=payload)
        binder = GitHubActionsBinder(env=env)
        declarations = {
            "title": {"type": "string", "required": True, "source": "issue.title"}
        }
        result = binder.resolve_inputs(declarations, {"title": "Override title"})
        assert result["title"] == "Override title"

    def test_missing_source_path_returns_none_for_optional(
        self, tmp_path: Path
    ) -> None:
        payload = {"issue": {}}  # missing 'title'
        env = _make_env(tmp_path, event_name="issues", payload=payload)
        binder = GitHubActionsBinder(env=env)
        declarations = {
            "title": {"type": "string", "required": False, "source": "issue.title"}
        }
        result = binder.resolve_inputs(declarations, {})
        assert result["title"] is None

    def test_missing_source_path_raises_for_required(self, tmp_path: Path) -> None:
        payload = {"issue": {}}  # missing 'title'
        env = _make_env(tmp_path, event_name="issues", payload=payload)
        binder = GitHubActionsBinder(env=env)
        declarations = {
            "title": {"type": "string", "required": True, "source": "issue.title"}
        }
        with pytest.raises(ValueError):
            binder.resolve_inputs(declarations, {})

    def test_non_string_value_is_coerced_to_string(self, tmp_path: Path) -> None:
        payload = {"issue": {"number": 99}}
        env = _make_env(tmp_path, event_name="issues", payload=payload)
        binder = GitHubActionsBinder(env=env)
        declarations = {
            "issue_number": {
                "type": "string",
                "required": True,
                "source": "issue.number",
            }
        }
        result = binder.resolve_inputs(declarations, {})
        assert result["issue_number"] == "99"


# ---------------------------------------------------------------------------
# resolve_inputs: string error cases
# ---------------------------------------------------------------------------


class TestResolveInputsStringErrors:
    """Error cases for string input resolution."""

    def test_missing_required_input_raises_value_error(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        declarations = {"target": {"type": "string", "required": True}}
        with pytest.raises(ValueError):
            binder_push.resolve_inputs(declarations, {})

    def test_error_message_contains_input_name(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        declarations = {"my_special_input": {"type": "string", "required": True}}
        with pytest.raises(ValueError, match="my_special_input"):
            binder_push.resolve_inputs(declarations, {})

    def test_missing_optional_input_returns_none(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        declarations = {"optional_field": {"type": "string", "required": False}}
        result = binder_push.resolve_inputs(declarations, {})
        assert result["optional_field"] is None

    def test_missing_optional_input_without_required_key_returns_none(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        """Inputs without 'required' key default to optional."""
        declarations = {"field": {"type": "string"}}
        result = binder_push.resolve_inputs(declarations, {})
        assert result["field"] is None

    def test_error_mentions_event_context(
        self, binder_push: GitHubActionsBinder
    ) -> None:
        """The error message should mention the current event name."""
        declarations = {"required_field": {"type": "string", "required": True}}
        with pytest.raises(ValueError, match="push"):
            binder_push.resolve_inputs(declarations, {})


# ---------------------------------------------------------------------------
# resolve_inputs: multiple declarations at once
# ---------------------------------------------------------------------------


class TestResolveInputsMixed:
    """Mixed input declarations are all resolved correctly in one call."""

    def test_resolves_repository_ref_and_string_together(
        self, tmp_path: Path
    ) -> None:
        payload = {"inputs": {"mode": "fast"}}
        workspace = str(tmp_path / "workspace")
        env = _make_env(
            tmp_path,
            event_name="workflow_dispatch",
            payload=payload,
            workspace=workspace,
        )
        binder = GitHubActionsBinder(env=env)
        declarations = {
            "repo": {"type": "repository-ref", "required": True},
            "mode": {"type": "string", "required": True},
        }
        result = binder.resolve_inputs(declarations, {})
        assert result["repo"] == workspace
        assert result["mode"] == "fast"

    def test_resolves_all_string_types(self, tmp_path: Path) -> None:
        payload = {
            "inputs": {"dispatch_input": "from_dispatch"},
            "issue": {"title": "from_source"},
        }
        env = _make_env(tmp_path, event_name="workflow_dispatch", payload=payload)
        binder = GitHubActionsBinder(env=env)
        declarations = {
            "explicit": {"type": "string", "required": True},
            "dispatch_input": {"type": "string", "required": True},
            "from_source": {
                "type": "string",
                "required": True,
                "source": "issue.title",
            },
            "optional_missing": {"type": "string", "required": False},
        }
        result = binder.resolve_inputs(
            declarations, {"explicit": "provided_value"}
        )
        assert result["explicit"] == "provided_value"
        assert result["dispatch_input"] == "from_dispatch"
        assert result["from_source"] == "from_source"
        assert result["optional_missing"] is None
