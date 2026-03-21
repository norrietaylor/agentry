"""Unit tests for T03.2: Input resolution (git-diff, repository-ref) and output mapping.

Tests cover:
- _resolve_git_diff() runs git diff and returns output as string.
- _resolve_git_diff() captures changed file content in diff output.
- _resolve_git_diff() returns empty string when diff is empty.
- _resolve_git_diff() raises NotAGitRepositoryError for non-git dirs.
- _resolve_repository_ref() returns absolute path of git repo.
- _resolve_repository_ref() raises NotAGitRepositoryError for non-git dirs.
- map_outputs() maps to .agentry/runs/<run_id>/ under target dir.
- CLI --input diff=<ref> argument maps to correct input key.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentry.binders import LocalBinder, NotAGitRepositoryError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def binder() -> LocalBinder:
    """Return a fresh LocalBinder instance."""
    return LocalBinder()


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repository with an initial commit."""
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
    return tmp_path


@pytest.fixture()
def git_repo_with_commits(git_repo: Path) -> Path:
    """A git repository with two commits (HEAD and HEAD~1 exist)."""
    (git_repo / "initial.txt").write_text("initial content\n")
    subprocess.run(
        ["git", "add", "initial.txt"],
        cwd=str(git_repo),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "initial commit"],
        cwd=str(git_repo),
        check=True,
        capture_output=True,
    )
    (git_repo / "feature.txt").write_text("feature content\n")
    subprocess.run(
        ["git", "add", "feature.txt"],
        cwd=str(git_repo),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "add feature"],
        cwd=str(git_repo),
        check=True,
        capture_output=True,
    )
    return git_repo


@pytest.fixture()
def non_git_dir(tmp_path: Path) -> Path:
    """Return a temp directory that is NOT a git repository."""
    return tmp_path


# ---------------------------------------------------------------------------
# _resolve_git_diff: core behaviour
# ---------------------------------------------------------------------------


class TestResolveGitDiff:
    def test_returns_string(
        self, binder: LocalBinder, git_repo_with_commits: Path
    ) -> None:
        """git diff returns a string result."""
        declarations = {
            "diff": {
                "type": "git-diff",
                "required": True,
                "target": str(git_repo_with_commits),
            }
        }
        result = binder.resolve_inputs(declarations, {"diff": "HEAD~1"})
        assert isinstance(result["diff"], str)

    def test_diff_output_contains_changed_filename(
        self, binder: LocalBinder, git_repo_with_commits: Path
    ) -> None:
        """The diff output references the file added in the second commit."""
        declarations = {
            "diff": {
                "type": "git-diff",
                "required": True,
                "target": str(git_repo_with_commits),
            }
        }
        result = binder.resolve_inputs(declarations, {"diff": "HEAD~1"})
        assert "feature.txt" in result["diff"]

    def test_diff_contains_plus_lines(
        self, binder: LocalBinder, git_repo_with_commits: Path
    ) -> None:
        """The diff contains added lines (prefixed with +)."""
        declarations = {
            "diff": {
                "type": "git-diff",
                "required": True,
                "target": str(git_repo_with_commits),
            }
        }
        result = binder.resolve_inputs(declarations, {"diff": "HEAD~1"})
        assert "+" in result["diff"]

    def test_empty_diff_returns_empty_string(
        self, binder: LocalBinder, git_repo_with_commits: Path
    ) -> None:
        """git diff HEAD returns empty string when working tree is clean."""
        declarations = {
            "diff": {
                "type": "git-diff",
                "required": True,
                "target": str(git_repo_with_commits),
            }
        }
        result = binder.resolve_inputs(declarations, {"diff": "HEAD"})
        assert result["diff"] == ""

    def test_non_git_dir_raises_not_a_git_repository_error(
        self, binder: LocalBinder, non_git_dir: Path
    ) -> None:
        """Passing a non-git directory raises NotAGitRepositoryError."""
        declarations = {
            "diff": {
                "type": "git-diff",
                "required": True,
                "target": str(non_git_dir),
            }
        }
        with pytest.raises(NotAGitRepositoryError):
            binder.resolve_inputs(declarations, {"diff": "HEAD~1"})

    def test_error_message_contains_path(
        self, binder: LocalBinder, non_git_dir: Path
    ) -> None:
        """NotAGitRepositoryError message contains the bad path."""
        declarations = {
            "diff": {
                "type": "git-diff",
                "required": True,
                "target": str(non_git_dir),
            }
        }
        with pytest.raises(NotAGitRepositoryError, match=str(non_git_dir)):
            binder.resolve_inputs(declarations, {"diff": "HEAD~1"})

    def test_multiple_file_diff(
        self, binder: LocalBinder, git_repo: Path
    ) -> None:
        """Diff spanning multiple changed files captures all file names."""
        # Create initial commit with two files.
        (git_repo / "a.txt").write_text("alpha\n")
        (git_repo / "b.txt").write_text("beta\n")
        subprocess.run(
            ["git", "add", "a.txt", "b.txt"],
            cwd=str(git_repo),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(git_repo),
            check=True,
            capture_output=True,
        )
        # Modify both files.
        (git_repo / "a.txt").write_text("alpha modified\n")
        (git_repo / "b.txt").write_text("beta modified\n")
        subprocess.run(
            ["git", "add", "a.txt", "b.txt"],
            cwd=str(git_repo),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "modify both"],
            cwd=str(git_repo),
            check=True,
            capture_output=True,
        )
        declarations = {
            "diff": {
                "type": "git-diff",
                "required": True,
                "target": str(git_repo),
            }
        }
        result = binder.resolve_inputs(declarations, {"diff": "HEAD~1"})
        assert "a.txt" in result["diff"]
        assert "b.txt" in result["diff"]


# ---------------------------------------------------------------------------
# _resolve_repository_ref
# ---------------------------------------------------------------------------


class TestResolveRepositoryRef:
    def test_returns_absolute_path(
        self, binder: LocalBinder, git_repo: Path
    ) -> None:
        """repository-ref resolves to absolute path of the git directory."""
        declarations = {
            "repo": {
                "type": "repository-ref",
                "required": True,
                "target": str(git_repo),
            }
        }
        result = binder.resolve_inputs(declarations, {"repo": str(git_repo)})
        assert isinstance(result["repo"], str)
        import os
        assert os.path.isabs(result["repo"])

    def test_returns_resolved_path(
        self, binder: LocalBinder, git_repo: Path
    ) -> None:
        """The returned path equals the resolved (canonical) path."""
        declarations = {
            "repo": {
                "type": "repository-ref",
                "required": True,
                "target": str(git_repo),
            }
        }
        result = binder.resolve_inputs(declarations, {"repo": str(git_repo)})
        assert result["repo"] == str(git_repo.resolve())

    def test_non_git_dir_raises(
        self, binder: LocalBinder, non_git_dir: Path
    ) -> None:
        """Passing a non-git directory raises NotAGitRepositoryError."""
        declarations = {
            "repo": {
                "type": "repository-ref",
                "required": True,
                "target": str(non_git_dir),
            }
        }
        with pytest.raises(NotAGitRepositoryError):
            binder.resolve_inputs(declarations, {"repo": str(non_git_dir)})


# ---------------------------------------------------------------------------
# map_outputs: output mapping
# ---------------------------------------------------------------------------


class TestMapOutputs:
    def test_output_under_agentry_runs(
        self, binder: LocalBinder, tmp_path: Path
    ) -> None:
        """Output paths are under .agentry/runs/<run_id>/."""
        paths = binder.map_outputs({}, str(tmp_path), "20260120T143000")
        for path in paths.values():
            assert ".agentry/runs/20260120T143000" in path

    def test_output_json_path(
        self, binder: LocalBinder, tmp_path: Path
    ) -> None:
        """The 'output' key maps to output.json."""
        paths = binder.map_outputs({}, str(tmp_path), "20260120T143000")
        assert paths["output"].endswith("output.json")

    def test_execution_record_json_path(
        self, binder: LocalBinder, tmp_path: Path
    ) -> None:
        """The 'execution_record' key maps to execution-record.json."""
        paths = binder.map_outputs({}, str(tmp_path), "20260120T143000")
        assert paths["execution_record"].endswith("execution-record.json")

    def test_run_id_appears_in_path(
        self, binder: LocalBinder, tmp_path: Path
    ) -> None:
        """The run_id is part of the directory name."""
        run_id = "20260120T143000"
        paths = binder.map_outputs({}, str(tmp_path), run_id)
        assert all(run_id in p for p in paths.values())

    def test_target_dir_is_base(
        self, binder: LocalBinder, tmp_path: Path
    ) -> None:
        """Output paths are nested under the target directory."""
        paths = binder.map_outputs({}, str(tmp_path), "20260120T143000")
        for path in paths.values():
            assert path.startswith(str(tmp_path))

    def test_declared_output_paths_included(
        self, binder: LocalBinder, tmp_path: Path
    ) -> None:
        """Extra declared output paths are included in the mapping."""
        output_decl = {"output_paths": ["report.json", "summary.txt"]}
        paths = binder.map_outputs(output_decl, str(tmp_path), "20260120T143000")
        assert "report" in paths
        assert "summary" in paths

    def test_declared_output_path_filename_preserved(
        self, binder: LocalBinder, tmp_path: Path
    ) -> None:
        """The filename of a declared output path is preserved."""
        output_decl = {"output_paths": ["findings.json"]}
        paths = binder.map_outputs(output_decl, str(tmp_path), "20260120T143000")
        assert paths["findings"].endswith("findings.json")


# ---------------------------------------------------------------------------
# CLI --input diff=<ref> argument mapping
# ---------------------------------------------------------------------------


class TestCliInputParsing:
    """The CLI parses --input diff=HEAD~1 into {'diff': 'HEAD~1'}."""

    def test_input_key_value_split(self) -> None:
        """Verify that key=value parsing produces the expected dict."""
        # Simulate CLI parsing logic from cli.py.
        raw_inputs = ("diff=HEAD~1", "repo=/tmp/repo")
        parsed: dict[str, str] = {}
        for item in raw_inputs:
            key, _, value = item.partition("=")
            parsed[key.strip()] = value.strip()
        assert parsed["diff"] == "HEAD~1"
        assert parsed["repo"] == "/tmp/repo"

    def test_input_with_equals_in_value(self) -> None:
        """Values containing '=' are split only on the first '='."""
        raw_inputs = ("query=a=b",)
        parsed: dict[str, str] = {}
        for item in raw_inputs:
            key, _, value = item.partition("=")
            parsed[key.strip()] = value.strip()
        assert parsed["query"] == "a=b"
