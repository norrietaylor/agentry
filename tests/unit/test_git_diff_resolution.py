"""Unit tests for git-diff input resolution (T02.2).

Tests cover:
- _is_git_ref() correctly identifies valid git refs (HEAD~1, HEAD~3, HEAD^2,
  SHA prefix, range syntax, named refs like origin/main).
- _is_git_ref() correctly rejects raw diff text, multi-line text, empty strings,
  and arbitrary strings that are not git refs.
- _resolve_git_diff() runs git diff for valid git refs in a real temp git repo.
- _resolve_git_diff() returns raw text directly when input is not a git ref.
- _resolve_git_diff() falls back to raw text when git diff fails (invalid ref).
- _resolve_git_diff() raises NotAGitRepositoryError for git refs in non-git dir.
- _resolve_git_diff() uses target directory from spec dict when provided.
- Integration through resolve_inputs(): --input diff=HEAD~1 resolves correctly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentry.binders.exceptions import NotAGitRepositoryError
from agentry.binders.local import LocalBinder, _is_git_ref

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def binder() -> LocalBinder:
    """Return a fresh LocalBinder instance."""
    return LocalBinder()


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repository in a temp directory with one commit."""
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
    # Initial commit so the repo has a valid HEAD.
    (tmp_path / "readme.txt").write_text("initial\n")
    subprocess.run(
        ["git", "add", "readme.txt"],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "initial commit"],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
    )
    return tmp_path


@pytest.fixture()
def git_repo_two_commits(git_repo: Path) -> Path:
    """Extend the git_repo fixture to have two commits."""
    (git_repo / "second.txt").write_text("second file\n")
    subprocess.run(
        ["git", "add", "second.txt"],
        cwd=str(git_repo),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "second commit"],
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
# _is_git_ref: valid git refs
# ---------------------------------------------------------------------------


class TestIsGitRefValid:
    """_is_git_ref() must return True for recognised git ref patterns."""

    def test_head(self) -> None:
        """Plain HEAD is a valid ref."""
        assert _is_git_ref("HEAD") is True

    def test_head_tilde_1(self) -> None:
        """HEAD~1 is a valid relative ref."""
        assert _is_git_ref("HEAD~1") is True

    def test_head_tilde_3(self) -> None:
        """HEAD~3 is a valid relative ref."""
        assert _is_git_ref("HEAD~3") is True

    def test_head_caret_2(self) -> None:
        """HEAD^2 is a valid relative ref."""
        assert _is_git_ref("HEAD^2") is True

    def test_head_caret_no_number(self) -> None:
        """HEAD^ (no number) is a valid relative ref."""
        assert _is_git_ref("HEAD^") is True

    def test_sha_prefix_7_chars(self) -> None:
        """A 7-character hex SHA prefix is a valid ref."""
        assert _is_git_ref("abc1234") is True

    def test_sha_prefix_40_chars(self) -> None:
        """A full 40-character SHA is a valid ref."""
        assert _is_git_ref("a" * 40) is True

    def test_sha_prefix_mixed_case(self) -> None:
        """SHA prefixes are case-insensitive."""
        assert _is_git_ref("ABCDEF1") is True

    def test_range_syntax_double_dot(self) -> None:
        """main..feature is a valid range ref."""
        assert _is_git_ref("main..feature") is True

    def test_range_syntax_triple_dot(self) -> None:
        """main...feature is a valid symmetric difference ref."""
        assert _is_git_ref("main...feature") is True

    def test_range_syntax_with_remote(self) -> None:
        """origin/main..HEAD is a valid range ref."""
        assert _is_git_ref("origin/main..HEAD") is True

    def test_named_ref_main(self) -> None:
        """'main' is a valid named ref."""
        assert _is_git_ref("main") is True

    def test_named_ref_origin_main(self) -> None:
        """'origin/main' is a valid named ref with remote prefix."""
        assert _is_git_ref("origin/main") is True

    def test_named_ref_feature_branch(self) -> None:
        """'feature/my-branch' is a valid named ref."""
        assert _is_git_ref("feature/my-branch") is True

    def test_named_ref_semver_tag(self) -> None:
        """'v1.2.3' is a valid tag ref."""
        assert _is_git_ref("v1.2.3") is True


# ---------------------------------------------------------------------------
# _is_git_ref: invalid / raw diff inputs
# ---------------------------------------------------------------------------


class TestIsGitRefInvalid:
    """_is_git_ref() must return False for raw diff text and other non-ref strings."""

    def test_raw_diff_text_starts_with_diff(self) -> None:
        """Strings starting with 'diff --git' are not refs."""
        assert _is_git_ref("diff --git a/foo.py b/foo.py") is False

    def test_raw_diff_text_with_diff_header(self) -> None:
        """Multi-token diff header text is not a ref."""
        assert _is_git_ref("diff --git a/README.md b/README.md\n--- a/README.md") is False

    def test_multi_line_text(self) -> None:
        """Multi-line strings are not refs."""
        assert _is_git_ref("first line\nsecond line") is False

    def test_empty_string(self) -> None:
        """Empty string is not a ref."""
        assert _is_git_ref("") is False

    def test_whitespace_only(self) -> None:
        """Whitespace-only string is not a ref."""
        assert _is_git_ref("   ") is False

    def test_arbitrary_string_with_spaces(self) -> None:
        """Arbitrary strings containing spaces are not refs."""
        assert _is_git_ref("not a ref at all") is False

    def test_string_with_leading_dashes(self) -> None:
        """Strings that look like diff markers are not refs."""
        assert _is_git_ref("--- a/file.py\n+++ b/file.py") is False

    def test_string_with_shell_redirection(self) -> None:
        """Strings with shell redirection characters are not refs."""
        assert _is_git_ref("foo > bar") is False

    def test_string_with_semicolon(self) -> None:
        """Strings with semicolons (shell command separators) are not refs."""
        assert _is_git_ref("main; rm -rf /") is False


# ---------------------------------------------------------------------------
# _resolve_git_diff: runs git diff for valid git refs
# ---------------------------------------------------------------------------


class TestResolveGitDiffWithRef:
    """_resolve_git_diff() must invoke git diff when the input is a git ref."""

    def test_returns_string_for_valid_ref(
        self, binder: LocalBinder, git_repo_two_commits: Path
    ) -> None:
        """git diff HEAD~1 returns a non-empty diff string."""
        spec = {"target": str(git_repo_two_commits)}
        result = binder._resolve_git_diff("HEAD~1", spec)
        assert isinstance(result, str)

    def test_diff_contains_changed_file(
        self, binder: LocalBinder, git_repo_two_commits: Path
    ) -> None:
        """The diff output must mention the file introduced in the second commit."""
        spec = {"target": str(git_repo_two_commits)}
        result = binder._resolve_git_diff("HEAD~1", spec)
        assert "second.txt" in result

    def test_head_with_clean_worktree_returns_empty(
        self, binder: LocalBinder, git_repo: Path
    ) -> None:
        """git diff HEAD on a clean working tree returns an empty string."""
        spec = {"target": str(git_repo)}
        result = binder._resolve_git_diff("HEAD", spec)
        assert result == ""

    def test_uses_target_from_spec(
        self, binder: LocalBinder, git_repo_two_commits: Path
    ) -> None:
        """When 'target' is in spec, that directory is used for git diff."""
        spec = {"target": str(git_repo_two_commits)}
        result = binder._resolve_git_diff("HEAD~1", spec)
        # The result is a string (diff output), not the ref itself.
        assert result != "HEAD~1"


# ---------------------------------------------------------------------------
# _resolve_git_diff: raw diff pass-through
# ---------------------------------------------------------------------------


class TestResolveGitDiffRawText:
    """_resolve_git_diff() must return raw text as-is when input is not a git ref."""

    def test_raw_diff_text_returned_unchanged(
        self, binder: LocalBinder
    ) -> None:
        """Raw diff text is returned without calling git."""
        raw_diff = "diff --git a/foo.py b/foo.py\n--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n"
        spec: dict = {}
        result = binder._resolve_git_diff(raw_diff, spec)
        assert result == raw_diff

    def test_arbitrary_non_ref_string_returned_unchanged(
        self, binder: LocalBinder
    ) -> None:
        """An arbitrary multi-word string is returned as-is."""
        value = "not a git ref"
        spec: dict = {}
        result = binder._resolve_git_diff(value, spec)
        assert result == value

    def test_multi_line_string_returned_unchanged(
        self, binder: LocalBinder
    ) -> None:
        """Multi-line strings are treated as raw diff content."""
        value = "line one\nline two\nline three\n"
        spec: dict = {}
        result = binder._resolve_git_diff(value, spec)
        assert result == value


# ---------------------------------------------------------------------------
# _resolve_git_diff: fallback on git diff failure
# ---------------------------------------------------------------------------


class TestResolveGitDiffFallback:
    """_resolve_git_diff() must fall back to raw text when git diff fails."""

    def test_invalid_ref_falls_back_to_raw_value(
        self, binder: LocalBinder, git_repo: Path
    ) -> None:
        """When git diff fails for an unknown ref, value is returned unchanged."""
        # "abc1234" looks like a SHA ref but doesn't exist in the repo.
        spec = {"target": str(git_repo)}
        result = binder._resolve_git_diff("abc1234", spec)
        assert result == "abc1234"

    def test_nonexistent_branch_falls_back(
        self, binder: LocalBinder, git_repo: Path
    ) -> None:
        """A branch name that doesn't exist falls back to the raw value."""
        spec = {"target": str(git_repo)}
        result = binder._resolve_git_diff("nonexistent-branch", spec)
        assert result == "nonexistent-branch"


# ---------------------------------------------------------------------------
# _resolve_git_diff: NotAGitRepositoryError for non-git directory
# ---------------------------------------------------------------------------


class TestResolveGitDiffNonGitDir:
    """_resolve_git_diff() must raise NotAGitRepositoryError for non-git dirs."""

    def test_raises_for_non_git_dir_with_ref(
        self, binder: LocalBinder, non_git_dir: Path
    ) -> None:
        """A valid git ref in a non-git directory raises NotAGitRepositoryError."""
        spec = {"target": str(non_git_dir)}
        with pytest.raises(NotAGitRepositoryError):
            binder._resolve_git_diff("HEAD~1", spec)

    def test_error_mentions_path(
        self, binder: LocalBinder, non_git_dir: Path
    ) -> None:
        """The error message includes the target path."""
        spec = {"target": str(non_git_dir)}
        with pytest.raises(NotAGitRepositoryError, match=str(non_git_dir)):
            binder._resolve_git_diff("HEAD~1", spec)

    def test_does_not_raise_for_raw_diff_in_non_git_dir(
        self, binder: LocalBinder, non_git_dir: Path
    ) -> None:
        """Raw diff text does NOT trigger the git-repo check."""
        raw_diff = "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
        spec = {"target": str(non_git_dir)}
        # Should return raw diff without raising.
        result = binder._resolve_git_diff(raw_diff, spec)
        assert result == raw_diff


# ---------------------------------------------------------------------------
# _resolve_git_diff: target directory from spec
# ---------------------------------------------------------------------------


class TestResolveGitDiffTargetFromSpec:
    """_resolve_git_diff() uses the 'target' key from the spec dict."""

    def test_spec_target_used_for_diff(
        self, binder: LocalBinder, git_repo_two_commits: Path
    ) -> None:
        """The spec's 'target' directory is passed to git diff as cwd."""
        spec = {"target": str(git_repo_two_commits)}
        result = binder._resolve_git_diff("HEAD~1", spec)
        # diff should contain the file added in the second commit.
        assert "second.txt" in result

    def test_spec_without_target_uses_cwd(
        self, binder: LocalBinder, git_repo_two_commits: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When 'target' is absent from spec, os.getcwd() is used."""
        monkeypatch.chdir(str(git_repo_two_commits))
        spec: dict = {}
        result = binder._resolve_git_diff("HEAD~1", spec)
        assert isinstance(result, str)
        assert "second.txt" in result


# ---------------------------------------------------------------------------
# Integration: resolve_inputs() with git-diff type
# ---------------------------------------------------------------------------


class TestResolveInputsIntegration:
    """resolve_inputs() correctly handles git-diff inputs end-to-end."""

    def test_resolve_inputs_git_diff_with_ref(
        self, binder: LocalBinder, git_repo_two_commits: Path
    ) -> None:
        """--input diff=HEAD~1 resolves to the git diff output."""
        declarations = {
            "diff": {
                "type": "git-diff",
                "required": True,
                "target": str(git_repo_two_commits),
            }
        }
        result = binder.resolve_inputs(declarations, {"diff": "HEAD~1"})
        assert isinstance(result["diff"], str)
        assert "second.txt" in result["diff"]

    def test_resolve_inputs_git_diff_with_raw_text(
        self, binder: LocalBinder
    ) -> None:
        """--input diff=<raw diff text> returns the raw text unchanged."""
        raw_diff = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
        declarations = {"diff": {"type": "git-diff", "required": True}}
        result = binder.resolve_inputs(declarations, {"diff": raw_diff})
        assert result["diff"] == raw_diff

    def test_resolve_inputs_git_diff_non_git_dir_raises(
        self, binder: LocalBinder, non_git_dir: Path
    ) -> None:
        """resolve_inputs() propagates NotAGitRepositoryError for git refs."""
        declarations = {
            "diff": {
                "type": "git-diff",
                "required": True,
                "target": str(non_git_dir),
            }
        }
        with pytest.raises(NotAGitRepositoryError):
            binder.resolve_inputs(declarations, {"diff": "HEAD~1"})

    def test_resolve_inputs_git_diff_invalid_ref_fallback(
        self, binder: LocalBinder, git_repo: Path
    ) -> None:
        """resolve_inputs() falls back to raw value when git diff fails."""
        declarations = {
            "diff": {
                "type": "git-diff",
                "required": True,
                "target": str(git_repo),
            }
        }
        result = binder.resolve_inputs(declarations, {"diff": "abc1234"})
        assert result["diff"] == "abc1234"
