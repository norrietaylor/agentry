"""Unit tests for T03.3: Tool bindings (repository:read, shell:execute) with security.

Tests cover:
- repository:read reads files within the repository root.
- repository:read rejects paths that escape the repository root (../ traversal).
- repository:read rejects symlinks pointing outside the repository root.
- repository:read raises FileNotFoundError for missing paths.
- repository:read raises IsADirectoryError for directory paths.
- shell:execute runs allowed read-only commands.
- shell:execute rejects disallowed commands (CommandNotAllowedError).
- shell:execute rejects disallowed git sub-commands.
- shell:execute returns command stdout.
- shell:execute raises subprocess.CalledProcessError on non-zero exit.
- bind_tools returns real callable implementations for both tools.
- PathTraversalError and CommandNotAllowedError have informative messages.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from agentry.binders import LocalBinder
from agentry.binders.exceptions import CommandNotAllowedError, PathTraversalError
from agentry.binders.local import (
    _make_repository_read,
    _make_shell_execute,
    _validate_shell_command,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def binder() -> LocalBinder:
    """Return a fresh LocalBinder instance."""
    return LocalBinder()


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repository with at least one file."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
    )
    # Create a file with known contents for reading tests.
    readme = tmp_path / "README.md"
    readme.write_text("# Test Repo\nHello world.\n")
    subprocess.run(
        ["git", "add", "."], cwd=str(tmp_path), check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
    )
    return tmp_path


@pytest.fixture()
def repo_read(git_repo: Path) -> Callable[..., str]:
    """Return a repository:read callable bound to a temp git repo."""
    fn = _make_repository_read()
    # Partial-bind repo_root so tests only need to supply path.
    import functools

    return functools.partial(fn, repo_root=str(git_repo))


@pytest.fixture()
def shell_exec() -> Callable[..., str]:
    """Return a shell:execute callable."""
    return _make_shell_execute()


# ---------------------------------------------------------------------------
# PathTraversalError exception
# ---------------------------------------------------------------------------


class TestPathTraversalError:
    def test_path_traversal_error_stores_repo_root(self, tmp_path: Path) -> None:
        err = PathTraversalError(str(tmp_path), "../outside")
        assert err.repo_root == str(tmp_path)

    def test_path_traversal_error_stores_requested_path(self, tmp_path: Path) -> None:
        err = PathTraversalError(str(tmp_path), "../outside")
        assert err.requested_path == "../outside"

    def test_path_traversal_error_message_mentions_path(self, tmp_path: Path) -> None:
        err = PathTraversalError(str(tmp_path), "../outside")
        assert "../outside" in str(err)

    def test_path_traversal_error_message_mentions_repo_root(
        self, tmp_path: Path
    ) -> None:
        err = PathTraversalError(str(tmp_path), "../outside")
        assert str(tmp_path) in str(err)


# ---------------------------------------------------------------------------
# CommandNotAllowedError exception
# ---------------------------------------------------------------------------


class TestCommandNotAllowedError:
    def test_command_not_allowed_stores_command(self) -> None:
        err = CommandNotAllowedError("rm -rf /")
        assert err.command == "rm -rf /"

    def test_command_not_allowed_message_mentions_command(self) -> None:
        err = CommandNotAllowedError("rm -rf /")
        assert "rm -rf /" in str(err)

    def test_command_not_allowed_message_mentions_allowlist(self) -> None:
        err = CommandNotAllowedError("rm -rf /")
        assert "allowlist" in str(err).lower() or "allow" in str(err).lower()


# ---------------------------------------------------------------------------
# repository:read — happy paths
# ---------------------------------------------------------------------------


class TestRepositoryReadHappyPath:
    def test_reads_file_in_root(self, repo_read: Callable[..., str]) -> None:
        content = repo_read(path="README.md")
        assert "Test Repo" in content

    def test_returns_full_file_contents(
        self, git_repo: Path, repo_read: Callable[..., str]
    ) -> None:
        expected = (git_repo / "README.md").read_text()
        assert repo_read(path="README.md") == expected

    def test_reads_nested_file(
        self, git_repo: Path, repo_read: Callable[..., str]
    ) -> None:
        subdir = git_repo / "src"
        subdir.mkdir()
        (subdir / "main.py").write_text("# main\n")
        content = repo_read(path="src/main.py")
        assert "# main" in content

    def test_reads_file_via_make_repository_read(self, git_repo: Path) -> None:
        fn = _make_repository_read()
        content = fn(repo_root=str(git_repo), path="README.md")
        assert "Test Repo" in content

    def test_callable_name(self) -> None:
        fn = _make_repository_read()
        assert fn.__name__ == "repository_read"


# ---------------------------------------------------------------------------
# repository:read — path traversal protection
# ---------------------------------------------------------------------------


class TestRepositoryReadPathTraversal:
    def test_dotdot_traversal_raises_path_traversal_error(
        self, repo_read: Callable[..., str], tmp_path: Path
    ) -> None:
        with pytest.raises(PathTraversalError):
            repo_read(path="../outside.txt")

    def test_dotdot_in_middle_raises_path_traversal_error(
        self, repo_read: Callable[..., str], git_repo: Path
    ) -> None:
        # Create a sibling directory to try to traverse into.
        sibling = git_repo.parent / "sibling"
        sibling.mkdir(exist_ok=True)
        with pytest.raises(PathTraversalError):
            repo_read(path=f"../{sibling.name}/secret.txt")

    def test_absolute_path_outside_repo_raises_path_traversal_error(
        self, git_repo: Path
    ) -> None:
        fn = _make_repository_read()
        # An absolute path to /etc/hosts (which is definitely outside the repo).
        with pytest.raises(PathTraversalError):
            fn(repo_root=str(git_repo), path="/etc/hosts")

    def test_error_contains_repo_root(
        self, git_repo: Path, repo_read: Callable[..., str]
    ) -> None:
        with pytest.raises(PathTraversalError) as exc_info:
            repo_read(path="../secret")
        assert str(git_repo.resolve()) in str(exc_info.value)

    def test_symlink_pointing_outside_raises(self, tmp_path: Path) -> None:
        """A symlink inside the repo pointing to a file outside must be rejected."""
        # Create a repo directory and a sibling directory that is 'outside'.
        repo_dir = tmp_path / "myrepo"
        repo_dir.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        outside_file = outside_dir / "secret.txt"
        outside_file.write_text("secret content")

        # Create a symlink inside the repo pointing to the outside file.
        link = repo_dir / "evil_link.txt"
        link.symlink_to(outside_file)

        fn = _make_repository_read()
        with pytest.raises(PathTraversalError):
            fn(repo_root=str(repo_dir), path="evil_link.txt")


# ---------------------------------------------------------------------------
# repository:read — missing files and directories
# ---------------------------------------------------------------------------


class TestRepositoryReadErrorCases:
    def test_missing_file_raises_file_not_found(
        self, repo_read: Callable[..., str]
    ) -> None:
        with pytest.raises(FileNotFoundError):
            repo_read(path="nonexistent.txt")

    def test_directory_path_raises_is_a_directory_error(
        self, git_repo: Path, repo_read: Callable[..., str]
    ) -> None:
        # The repo root itself is a directory.
        (git_repo / "subdir").mkdir()
        with pytest.raises(IsADirectoryError):
            repo_read(path="subdir")


# ---------------------------------------------------------------------------
# _validate_shell_command — allowlist validation
# ---------------------------------------------------------------------------


class TestValidateShellCommand:
    # Allowed commands
    @pytest.mark.parametrize(
        "cmd",
        [
            "git log",
            "git diff HEAD",
            "git show abc123",
            "git blame src/main.py",
            "ls -la",
            "find . -name '*.py'",
            "grep -r pattern .",
            "cat README.md",
            "head -n 10 file.txt",
            "tail -n 20 file.txt",
            "wc -l file.txt",
        ],
    )
    def test_allowed_command_does_not_raise(self, cmd: str) -> None:
        _validate_shell_command(cmd)  # Should not raise.

    # Disallowed executables
    @pytest.mark.parametrize(
        "cmd",
        [
            "rm -rf /",
            "echo hello",
            "curl https://example.com",
            "wget https://example.com",
            "python3 script.py",
            "bash script.sh",
            "chmod +x file",
            "mv src dst",
            "cp src dst",
            "touch file.txt",
            "mkdir -p /tmp/new",
        ],
    )
    def test_disallowed_command_raises(self, cmd: str) -> None:
        with pytest.raises(CommandNotAllowedError):
            _validate_shell_command(cmd)

    # Disallowed git sub-commands
    @pytest.mark.parametrize(
        "cmd",
        [
            "git commit -m msg",
            "git push origin main",
            "git checkout main",
            "git reset --hard",
            "git rm file.txt",
            "git add .",
            "git fetch",
            "git pull",
            "git rebase main",
            "git merge feature",
        ],
    )
    def test_disallowed_git_subcommand_raises(self, cmd: str) -> None:
        with pytest.raises(CommandNotAllowedError):
            _validate_shell_command(cmd)

    def test_empty_command_raises(self) -> None:
        with pytest.raises(CommandNotAllowedError):
            _validate_shell_command("")

    def test_git_without_subcommand_raises(self) -> None:
        with pytest.raises(CommandNotAllowedError):
            _validate_shell_command("git")

    def test_command_with_absolute_path_executable(self) -> None:
        """Executables specified by full path (e.g. /usr/bin/cat) are also checked."""
        _validate_shell_command("/usr/bin/cat README.md")  # Should not raise.

    def test_disallowed_executable_with_absolute_path_raises(self) -> None:
        with pytest.raises(CommandNotAllowedError):
            _validate_shell_command("/usr/bin/rm -rf /")


# ---------------------------------------------------------------------------
# shell:execute — happy paths
# ---------------------------------------------------------------------------


class TestShellExecuteHappyPath:
    def test_runs_cat_command(
        self, shell_exec: Callable[..., str], git_repo: Path
    ) -> None:
        output = shell_exec(command="cat README.md", cwd=str(git_repo))
        assert "Test Repo" in output

    def test_runs_ls_command(
        self, shell_exec: Callable[..., str], git_repo: Path
    ) -> None:
        output = shell_exec(command="ls", cwd=str(git_repo))
        assert "README.md" in output

    def test_runs_git_log(
        self, shell_exec: Callable[..., str], git_repo: Path
    ) -> None:
        output = shell_exec(command="git log --oneline", cwd=str(git_repo))
        assert "init" in output

    def test_returns_stdout_as_string(
        self, shell_exec: Callable[..., str], git_repo: Path
    ) -> None:
        result = shell_exec(command="wc -l README.md", cwd=str(git_repo))
        assert isinstance(result, str)

    def test_callable_name(self) -> None:
        fn = _make_shell_execute()
        assert fn.__name__ == "shell_execute"

    def test_cwd_defaults_to_none(self, shell_exec: Callable[..., str]) -> None:
        """shell:execute works without an explicit cwd."""
        result = shell_exec(command="ls")
        assert isinstance(result, str)

    def test_runs_grep_command(
        self, shell_exec: Callable[..., str], git_repo: Path
    ) -> None:
        output = shell_exec(command="grep -r 'Hello' .", cwd=str(git_repo))
        assert "Hello" in output

    def test_runs_head_command(
        self, shell_exec: Callable[..., str], git_repo: Path
    ) -> None:
        output = shell_exec(command="head -n 1 README.md", cwd=str(git_repo))
        assert "# Test Repo" in output


# ---------------------------------------------------------------------------
# shell:execute — security: rejects disallowed commands
# ---------------------------------------------------------------------------


class TestShellExecuteSecurity:
    def test_rm_command_rejected(self, shell_exec: Callable[..., str]) -> None:
        with pytest.raises(CommandNotAllowedError):
            shell_exec(command="rm -rf /tmp/test")

    def test_echo_command_rejected(self, shell_exec: Callable[..., str]) -> None:
        with pytest.raises(CommandNotAllowedError):
            shell_exec(command="echo hello")

    def test_curl_command_rejected(self, shell_exec: Callable[..., str]) -> None:
        with pytest.raises(CommandNotAllowedError):
            shell_exec(command="curl https://example.com")

    def test_git_commit_rejected(self, shell_exec: Callable[..., str]) -> None:
        with pytest.raises(CommandNotAllowedError):
            shell_exec(command="git commit -m test")

    def test_git_push_rejected(self, shell_exec: Callable[..., str]) -> None:
        with pytest.raises(CommandNotAllowedError):
            shell_exec(command="git push origin main")

    def test_git_checkout_rejected(self, shell_exec: Callable[..., str]) -> None:
        with pytest.raises(CommandNotAllowedError):
            shell_exec(command="git checkout main")

    def test_error_includes_command(self, shell_exec: Callable[..., str]) -> None:
        with pytest.raises(CommandNotAllowedError) as exc_info:
            shell_exec(command="rm -rf /")
        assert "rm -rf /" in str(exc_info.value)


# ---------------------------------------------------------------------------
# shell:execute — command failure
# ---------------------------------------------------------------------------


class TestShellExecuteFailure:
    def test_nonzero_exit_raises_called_process_error(
        self, shell_exec: Callable[..., str], tmp_path: Path
    ) -> None:
        with pytest.raises(subprocess.CalledProcessError):
            shell_exec(
                command="cat nonexistent_file_xyz_12345.txt", cwd=str(tmp_path)
            )


# ---------------------------------------------------------------------------
# bind_tools — integration: real implementations returned
# ---------------------------------------------------------------------------


class TestBindToolsRealImplementations:
    def test_repository_read_from_bind_tools_is_real(
        self, binder: LocalBinder, git_repo: Path
    ) -> None:
        bindings = binder.bind_tools(["repository:read"])
        fn = bindings["repository:read"]
        content = fn(repo_root=str(git_repo), path="README.md")
        assert "Test Repo" in content

    def test_shell_execute_from_bind_tools_is_real(
        self, binder: LocalBinder, git_repo: Path
    ) -> None:
        bindings = binder.bind_tools(["shell:execute"])
        fn = bindings["shell:execute"]
        output = fn(command="git log --oneline", cwd=str(git_repo))
        assert "init" in output

    def test_both_tools_bound_together(
        self, binder: LocalBinder, git_repo: Path
    ) -> None:
        bindings = binder.bind_tools(["repository:read", "shell:execute"])
        assert callable(bindings["repository:read"])
        assert callable(bindings["shell:execute"])

    def test_repository_read_rejects_traversal_when_bound(
        self, binder: LocalBinder, git_repo: Path
    ) -> None:
        bindings = binder.bind_tools(["repository:read"])
        fn = bindings["repository:read"]
        with pytest.raises(PathTraversalError):
            fn(repo_root=str(git_repo), path="../outside")

    def test_shell_execute_rejects_disallowed_when_bound(
        self, binder: LocalBinder
    ) -> None:
        bindings = binder.bind_tools(["shell:execute"])
        fn = bindings["shell:execute"]
        with pytest.raises(CommandNotAllowedError):
            fn(command="rm -rf /")
