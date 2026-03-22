"""Integration tests for T04.3: pr:create tool binding.

Tests cover:
- LocalBinder pr:create creates a new branch in a temp git repo.
- LocalBinder pr:create commits specified files to the branch.
- LocalBinder pr:create returns branch name in result dict.
- LocalBinder pr:create with gh CLI mocked returns pr_url.
- LocalBinder pr:create refuses force-push (no --force in commands).
- LocalBinder pr:create uses configurable base branch.
- LocalBinder pr:create applies agent-proposed label.
- GitHubActionsBinder pr:create (with mocked httpx) creates PR via API.
- GitHubActionsBinder pr:create handles 403 and 404 errors gracefully.

For LocalBinder tests: uses real git operations in tmp_path, mocks git push and
the gh CLI (the only operations that require a real remote).
For GitHubActionsBinder tests: mocks httpx responses.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentry.binders.local import _make_pr_create

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git_repo(directory: Path) -> Path:
    """Initialise a minimal git repository with one commit in *directory*."""
    subprocess.run(["git", "init", str(directory)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(directory),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=str(directory),
        check=True,
        capture_output=True,
    )
    # Need at least one commit so we have a valid base branch.
    init_file = directory / "README.md"
    init_file.write_text("# Test repo\n")
    subprocess.run(
        ["git", "add", "README.md"],
        cwd=str(directory),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "initial commit"],
        cwd=str(directory),
        check=True,
        capture_output=True,
    )
    return directory


def _current_branch(repo: Path) -> str:
    """Return the current branch name of *repo*."""
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _make_fake_run(
    pr_url: str = "https://github.com/owner/repo/pull/1",
    recorded_commands: list[list[str]] | None = None,
) -> Any:
    """Return a fake subprocess.run that mocks git-push and gh commands.

    Real git commands (checkout, add, commit) are executed normally.
    Only ``git push`` and ``gh`` are intercepted so tests work without a remote.

    Args:
        pr_url: URL to return from the mocked ``gh pr create`` call.
        recorded_commands: Optional list to which every invoked command is appended.
    """
    original_run = subprocess.run

    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        if recorded_commands is not None:
            recorded_commands.append(list(cmd))
        # Mock git push — no remote available in temp repo.
        if cmd[:3] == ["git", "push", "-u"]:
            mock = MagicMock()
            mock.stdout = ""
            mock.returncode = 0
            return mock
        # Mock gh CLI — no real GitHub available.
        if cmd[0] == "gh":
            mock = MagicMock()
            mock.stdout = pr_url + "\n"
            mock.returncode = 0
            return mock
        return original_run(cmd, **kwargs)

    return fake_run


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """A temporary git repository with one initial commit."""
    return _git_repo(tmp_path)


def _make_github_binder(tmp_workspace: Path) -> Any:
    """Create a GitHubActionsBinder with minimal env and a temporary event file."""
    from agentry.binders.github_actions import GitHubActionsBinder

    event_data = {"action": "opened", "pull_request": {"number": 1}}
    event_file = tmp_workspace / "event.json"
    event_file.write_text(json.dumps(event_data))

    env = {
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_EVENT_PATH": str(event_file),
        "GITHUB_WORKSPACE": str(tmp_workspace),
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_TOKEN": "fake-token-for-tests",
    }
    return GitHubActionsBinder(env=env)


# ---------------------------------------------------------------------------
# LocalBinder pr:create tests
# ---------------------------------------------------------------------------


class TestLocalBinderPrCreate:
    """Integration tests for LocalBinder pr:create using real git operations."""

    def test_pr_create_creates_new_branch(self, git_repo: Path) -> None:
        """pr:create creates a new branch in the temp git repo."""
        pr_create = _make_pr_create()
        (git_repo / "feature.txt").write_text("feature content\n")
        base_branch = _current_branch(git_repo)

        with patch(
            "agentry.binders.local.subprocess.run",
            side_effect=_make_fake_run(),
        ):
            pr_create(
                branch_name="feature/test-branch",
                commit_message="Add feature",
                base_branch=base_branch,
                title="Test PR",
                body="Test body",
                files=["feature.txt"],
                cwd=str(git_repo),
            )

        # Verify the branch was created in the repo (real git shows it).
        result = subprocess.run(
            ["git", "branch", "--list", "feature/test-branch"],
            cwd=str(git_repo),
            capture_output=True,
            text=True,
        )
        assert "feature/test-branch" in result.stdout

    def test_pr_create_commits_specified_files(self, git_repo: Path) -> None:
        """pr:create commits the specified files to the new branch."""
        pr_create = _make_pr_create()
        (git_repo / "changes.txt").write_text("some changes\n")
        base_branch = _current_branch(git_repo)

        with patch(
            "agentry.binders.local.subprocess.run",
            side_effect=_make_fake_run(),
        ):
            pr_create(
                branch_name="feature/file-commit",
                commit_message="Add changes.txt",
                base_branch=base_branch,
                title="File commit PR",
                body="Body",
                files=["changes.txt"],
                cwd=str(git_repo),
            )

        # Real git should have the new branch with the expected commit.
        log_result = subprocess.run(
            ["git", "log", "--oneline", "feature/file-commit"],
            cwd=str(git_repo),
            capture_output=True,
            text=True,
        )
        assert "Add changes.txt" in log_result.stdout

        show_result = subprocess.run(
            ["git", "show", "--name-only", "feature/file-commit"],
            cwd=str(git_repo),
            capture_output=True,
            text=True,
        )
        assert "changes.txt" in show_result.stdout

    def test_pr_create_returns_branch_name(self, git_repo: Path) -> None:
        """pr:create returns the branch name in the result dict."""
        pr_create = _make_pr_create()
        (git_repo / "file.txt").write_text("content\n")
        base_branch = _current_branch(git_repo)

        with patch(
            "agentry.binders.local.subprocess.run",
            side_effect=_make_fake_run(),
        ):
            result = pr_create(
                branch_name="feature/returns-branch",
                commit_message="chore: add file",
                base_branch=base_branch,
                title="Branch Name Test",
                body="Body",
                files=["file.txt"],
                cwd=str(git_repo),
            )

        assert result["branch"] == "feature/returns-branch"

    def test_pr_create_returns_pr_url_from_gh(self, git_repo: Path) -> None:
        """pr:create with gh CLI mocked returns pr_url in result dict."""
        pr_create = _make_pr_create()
        (git_repo / "url_test.txt").write_text("url test\n")
        base_branch = _current_branch(git_repo)

        expected_url = "https://github.com/owner/repo/pull/42"

        with patch(
            "agentry.binders.local.subprocess.run",
            side_effect=_make_fake_run(pr_url=expected_url),
        ):
            result = pr_create(
                branch_name="feature/url-test",
                commit_message="add url_test.txt",
                base_branch=base_branch,
                title="URL test",
                body="Body",
                files=["url_test.txt"],
                cwd=str(git_repo),
            )

        assert result["pr_url"] == expected_url

    def test_pr_create_refuses_force_push(self, git_repo: Path) -> None:
        """pr:create never uses --force in any subprocess commands."""
        pr_create = _make_pr_create()
        (git_repo / "safe.txt").write_text("safe\n")
        base_branch = _current_branch(git_repo)

        recorded: list[list[str]] = []

        with patch(
            "agentry.binders.local.subprocess.run",
            side_effect=_make_fake_run(recorded_commands=recorded),
        ):
            pr_create(
                branch_name="feature/no-force",
                commit_message="safe push",
                base_branch=base_branch,
                title="No force push",
                body="Body",
                files=["safe.txt"],
                cwd=str(git_repo),
            )

        # Commands should have been recorded (checkout, add, commit, push, gh).
        assert recorded, "No commands were recorded — patch may not have fired."

        # None of the issued commands should include --force.
        for cmd in recorded:
            assert "--force" not in cmd, (
                f"Found --force in command: {cmd}. "
                "pr:create must never force-push."
            )

    def test_pr_create_raises_for_protected_branch_main(
        self, git_repo: Path
    ) -> None:
        """pr:create raises ValueError when branch_name is 'main'."""
        pr_create = _make_pr_create()

        with pytest.raises(ValueError, match="protected branch"):
            pr_create(
                branch_name="main",
                commit_message="should fail",
                title="Protected",
                body="Body",
                cwd=str(git_repo),
            )

    def test_pr_create_raises_for_protected_branch_master(
        self, git_repo: Path
    ) -> None:
        """pr:create raises ValueError when branch_name is 'master'."""
        pr_create = _make_pr_create()

        with pytest.raises(ValueError, match="protected branch"):
            pr_create(
                branch_name="master",
                commit_message="should fail",
                title="Protected",
                body="Body",
                cwd=str(git_repo),
            )

    def test_pr_create_uses_configurable_base_branch(self, git_repo: Path) -> None:
        """pr:create uses the specified base_branch rather than always 'main'."""
        pr_create = _make_pr_create()

        # The initial branch name may differ across git versions (main vs master).
        initial_branch = _current_branch(git_repo)

        # Create a 'develop' branch with its own commit.
        subprocess.run(
            ["git", "checkout", "-b", "develop"],
            cwd=str(git_repo),
            check=True,
            capture_output=True,
        )
        (git_repo / "develop_base.txt").write_text("develop base\n")
        subprocess.run(
            ["git", "add", "develop_base.txt"],
            cwd=str(git_repo),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "develop base commit"],
            cwd=str(git_repo),
            check=True,
            capture_output=True,
        )
        # Return to initial branch before invoking pr_create.
        subprocess.run(
            ["git", "checkout", initial_branch],
            cwd=str(git_repo),
            check=True,
            capture_output=True,
        )

        (git_repo / "feature_dev.txt").write_text("feature from develop\n")

        with patch(
            "agentry.binders.local.subprocess.run",
            side_effect=_make_fake_run(),
        ):
            result = pr_create(
                branch_name="feature/from-develop",
                commit_message="feature off develop",
                base_branch="develop",
                title="Develop based PR",
                body="Body",
                files=["feature_dev.txt"],
                cwd=str(git_repo),
            )

        assert result["status"] == "created"
        assert result["branch"] == "feature/from-develop"

    def test_pr_create_applies_agent_proposed_label(self, git_repo: Path) -> None:
        """pr:create passes the agent-proposed label to the gh CLI invocation."""
        pr_create = _make_pr_create()
        (git_repo / "labelled.txt").write_text("labelled\n")
        base_branch = _current_branch(git_repo)

        gh_invocations: list[list[str]] = []
        original_run = subprocess.run

        def capturing_run(cmd: list[str], **kwargs: Any) -> Any:
            if cmd[:3] == ["git", "push", "-u"]:
                mock = MagicMock()
                mock.stdout = ""
                mock.returncode = 0
                return mock
            if cmd[0] == "gh":
                gh_invocations.append(list(cmd))
                mock = MagicMock()
                mock.stdout = "https://github.com/owner/repo/pull/11\n"
                mock.returncode = 0
                return mock
            return original_run(cmd, **kwargs)

        with patch("agentry.binders.local.subprocess.run", side_effect=capturing_run):
            pr_create(
                branch_name="feature/label-test",
                commit_message="label test",
                base_branch=base_branch,
                title="Label Test PR",
                body="Body",
                label="agent-proposed",
                files=["labelled.txt"],
                cwd=str(git_repo),
            )

        assert gh_invocations, "Expected at least one gh CLI invocation."
        gh_cmd = gh_invocations[0]
        assert "--label" in gh_cmd
        label_index = gh_cmd.index("--label")
        assert gh_cmd[label_index + 1] == "agent-proposed"


# ---------------------------------------------------------------------------
# GitHubActionsBinder pr:create tests
# ---------------------------------------------------------------------------


class TestGitHubActionsBinderPrCreate:
    """Integration tests for GitHubActionsBinder pr:create using mocked httpx."""

    def test_pr_create_via_api_returns_created_status(
        self, tmp_path: Path
    ) -> None:
        """GitHubActionsBinder pr:create (mocked httpx) returns status 'created'."""
        binder = _make_github_binder(tmp_path)
        pr_create = binder.bind_tools(["pr:create"])["pr:create"]

        # Write a file to the workspace so the blob creation works.
        (tmp_path / "myfile.txt").write_text("content\n")

        def mock_get(url: str, **kwargs: Any) -> MagicMock:
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"object": {"sha": "base-sha-abc123"}}
            resp.raise_for_status.return_value = None
            return resp

        def mock_post(url: str, **kwargs: Any) -> MagicMock:
            resp = MagicMock()
            resp.status_code = 201
            resp.raise_for_status.return_value = None
            if "/git/blobs" in url:
                resp.json.return_value = {"sha": "blob-sha-xyz"}
            elif "/git/trees" in url:
                resp.json.return_value = {"sha": "tree-sha-xyz"}
            elif "/git/commits" in url:
                resp.json.return_value = {"sha": "commit-sha-xyz"}
            elif "/git/refs" in url:
                resp.json.return_value = {"ref": "refs/heads/feature/api-test"}
            elif "/pulls" in url:
                resp.json.return_value = {
                    "html_url": "https://github.com/owner/repo/pull/1",
                    "number": 1,
                }
            elif "/labels" in url:
                resp.json.return_value = [{"name": "agent-proposed"}]
            return resp

        with patch(
            "agentry.binders.github_actions.httpx.get", side_effect=mock_get
        ), patch("agentry.binders.github_actions.httpx.post", side_effect=mock_post):
            result = pr_create(
                branch_name="feature/api-test",
                commit_message="API test commit",
                title="API Test PR",
                body="Test body",
                files=["myfile.txt"],
            )

        assert result["status"] == "created"

    def test_pr_create_via_api_returns_pr_url(self, tmp_path: Path) -> None:
        """GitHubActionsBinder pr:create returns the PR html_url from the API."""
        binder = _make_github_binder(tmp_path)
        pr_create = binder.bind_tools(["pr:create"])["pr:create"]

        (tmp_path / "another.txt").write_text("another\n")

        expected_url = "https://github.com/owner/repo/pull/77"

        def mock_get(url: str, **kwargs: Any) -> MagicMock:
            resp = MagicMock()
            resp.json.return_value = {"object": {"sha": "base-sha"}}
            resp.raise_for_status.return_value = None
            return resp

        def mock_post(url: str, **kwargs: Any) -> MagicMock:
            resp = MagicMock()
            resp.raise_for_status.return_value = None
            if "/git/blobs" in url:
                resp.json.return_value = {"sha": "blob-sha"}
            elif "/git/trees" in url:
                resp.json.return_value = {"sha": "tree-sha"}
            elif "/git/commits" in url:
                resp.json.return_value = {"sha": "commit-sha"}
            elif "/git/refs" in url:
                resp.json.return_value = {}
            elif "/pulls" in url:
                resp.json.return_value = {
                    "html_url": expected_url,
                    "number": 77,
                }
            elif "/labels" in url:
                resp.json.return_value = []
            return resp

        with patch(
            "agentry.binders.github_actions.httpx.get", side_effect=mock_get
        ), patch("agentry.binders.github_actions.httpx.post", side_effect=mock_post):
            result = pr_create(
                branch_name="feature/url-test",
                commit_message="URL test",
                title="URL Test",
                body="Body",
                files=["another.txt"],
            )

        assert result["pr_url"] == expected_url

    def test_pr_create_raises_for_protected_branch(self, tmp_path: Path) -> None:
        """GitHubActionsBinder pr:create raises ValueError for protected branches."""
        binder = _make_github_binder(tmp_path)
        pr_create = binder.bind_tools(["pr:create"])["pr:create"]

        with pytest.raises(ValueError, match="protected branch"):
            pr_create(
                branch_name="main",
                commit_message="should fail",
                title="Protected",
                body="Body",
            )

    def test_pr_create_handles_403_error_gracefully(self, tmp_path: Path) -> None:
        """GitHubActionsBinder pr:create raises RuntimeError on 403 from API."""
        import httpx

        binder = _make_github_binder(tmp_path)
        pr_create = binder.bind_tools(["pr:create"])["pr:create"]

        def mock_get_403(url: str, **kwargs: Any) -> MagicMock:
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = 403
            resp.text = "Forbidden"
            raise httpx.HTTPStatusError(
                "403 Forbidden", request=MagicMock(), response=resp
            )

        with patch(
            "agentry.binders.github_actions.httpx.get", side_effect=mock_get_403
        ), pytest.raises(RuntimeError, match="403"):
            pr_create(
                branch_name="feature/403-test",
                commit_message="should fail",
                title="403 Test",
                body="Body",
            )

    def test_pr_create_handles_404_error_gracefully(self, tmp_path: Path) -> None:
        """GitHubActionsBinder pr:create raises RuntimeError on 404 from API."""
        import httpx

        binder = _make_github_binder(tmp_path)
        pr_create = binder.bind_tools(["pr:create"])["pr:create"]

        def mock_get_404(url: str, **kwargs: Any) -> MagicMock:
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = 404
            resp.text = "Not Found"
            raise httpx.HTTPStatusError(
                "404 Not Found", request=MagicMock(), response=resp
            )

        with patch(
            "agentry.binders.github_actions.httpx.get", side_effect=mock_get_404
        ), pytest.raises(RuntimeError, match="404"):
            pr_create(
                branch_name="feature/404-test",
                commit_message="should fail",
                title="404 Test",
                body="Body",
            )

    def test_pr_create_error_message_mentions_permissions_on_403(
        self, tmp_path: Path
    ) -> None:
        """403 error message includes hint about token permissions."""
        import httpx

        binder = _make_github_binder(tmp_path)
        pr_create = binder.bind_tools(["pr:create"])["pr:create"]

        def mock_get_403(url: str, **kwargs: Any) -> MagicMock:
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = 403
            resp.text = "Forbidden"
            raise httpx.HTTPStatusError("403", request=MagicMock(), response=resp)

        with patch(
            "agentry.binders.github_actions.httpx.get", side_effect=mock_get_403
        ), pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
            pr_create(
                branch_name="feature/permissions-hint",
                commit_message="permissions",
                title="Permissions",
                body="Body",
            )
