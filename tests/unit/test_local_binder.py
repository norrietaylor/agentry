"""Unit tests for T03.1: EnvironmentBinder protocol and LocalBinder skeleton.

Tests cover:
- LocalBinder instantiates and satisfies the EnvironmentBinder protocol.
- resolve_inputs() passes through string inputs correctly.
- resolve_inputs() raises ValueError for missing required inputs.
- resolve_inputs() returns None for optional missing inputs.
- bind_tools() accepts supported tools and rejects unsupported ones.
- map_outputs() produces paths under .agentry/runs/<run_id>/.
- generate_pipeline_config() raises NotImplementedError.
- Git repository detection: _assert_git_repo() passes for git dirs, raises for others.
- get_binder() returns LocalBinder when no name given.
- discover_binders() always contains 'local'.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from agentry.binders import (
    LocalBinder,
    NotAGitRepositoryError,
    UnsupportedToolError,
    discover_binders,
    get_binder,
)
from agentry.binders.local import SUPPORTED_TOOLS, _assert_git_repo
from agentry.binders.protocol import EnvironmentBinder

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def binder() -> LocalBinder:
    """Return a fresh LocalBinder instance."""
    return LocalBinder()


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repository in a temp directory."""
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
def non_git_dir(tmp_path: Path) -> Path:
    """Return a temp directory that is NOT a git repository."""
    return tmp_path


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    """LocalBinder satisfies the EnvironmentBinder protocol."""

    def test_local_binder_is_environment_binder(self, binder: LocalBinder) -> None:
        assert isinstance(binder, EnvironmentBinder)

    def test_local_binder_has_resolve_inputs(self, binder: LocalBinder) -> None:
        assert callable(binder.resolve_inputs)

    def test_local_binder_has_bind_tools(self, binder: LocalBinder) -> None:
        assert callable(binder.bind_tools)

    def test_local_binder_has_map_outputs(self, binder: LocalBinder) -> None:
        assert callable(binder.map_outputs)

    def test_local_binder_has_generate_pipeline_config(
        self, binder: LocalBinder
    ) -> None:
        assert callable(binder.generate_pipeline_config)

    def test_local_binder_name(self, binder: LocalBinder) -> None:
        assert binder.name == "local"


# ---------------------------------------------------------------------------
# generate_pipeline_config raises NotImplementedError
# ---------------------------------------------------------------------------


class TestGeneratePipelineConfig:
    def test_raises_not_implemented(self, binder: LocalBinder) -> None:
        with pytest.raises(NotImplementedError):
            binder.generate_pipeline_config()

    def test_error_message_mentions_phase_3(self, binder: LocalBinder) -> None:
        with pytest.raises(NotImplementedError, match="Phase 3"):
            binder.generate_pipeline_config()


# ---------------------------------------------------------------------------
# resolve_inputs: string pass-through
# ---------------------------------------------------------------------------


class TestResolveInputsString:
    def test_string_input_passthrough(self, binder: LocalBinder) -> None:
        declarations = {"description": {"type": "string", "required": True}}
        result = binder.resolve_inputs(declarations, {"description": "hello"})
        assert result["description"] == "hello"

    def test_multiple_string_inputs(self, binder: LocalBinder) -> None:
        declarations = {
            "a": {"type": "string", "required": True},
            "b": {"type": "string", "required": False},
        }
        result = binder.resolve_inputs(declarations, {"a": "foo", "b": "bar"})
        assert result["a"] == "foo"
        assert result["b"] == "bar"

    def test_missing_optional_input_returns_none(self, binder: LocalBinder) -> None:
        declarations = {"optional": {"type": "string", "required": False}}
        result = binder.resolve_inputs(declarations, {})
        assert result["optional"] is None

    def test_missing_required_input_raises_value_error(
        self, binder: LocalBinder
    ) -> None:
        declarations = {"issue": {"type": "string", "required": True}}
        with pytest.raises(ValueError, match="issue"):
            binder.resolve_inputs(declarations, {})

    def test_error_message_suggests_fix(self, binder: LocalBinder) -> None:
        declarations = {"diff": {"type": "string", "required": True}}
        with pytest.raises(ValueError, match="--input diff="):
            binder.resolve_inputs(declarations, {})


# ---------------------------------------------------------------------------
# resolve_inputs: git-diff (skeleton — raises NotImplementedError in T03.1)
# ---------------------------------------------------------------------------


class TestResolveInputsGitDiff:
    def test_git_diff_on_non_git_dir_raises(
        self, binder: LocalBinder, non_git_dir: Path
    ) -> None:
        declarations = {
            "diff": {"type": "git-diff", "required": True, "target": str(non_git_dir)}
        }
        with pytest.raises(NotAGitRepositoryError):
            binder.resolve_inputs(declarations, {"diff": "HEAD~1"})

    def test_git_diff_on_git_dir_raises_not_implemented(
        self, binder: LocalBinder, git_repo: Path
    ) -> None:
        declarations = {
            "diff": {"type": "git-diff", "required": True, "target": str(git_repo)}
        }
        # T03.1 skeleton: passes git check, then raises NotImplementedError.
        with pytest.raises(NotImplementedError):
            binder.resolve_inputs(declarations, {"diff": "HEAD~1"})


# ---------------------------------------------------------------------------
# resolve_inputs: repository-ref
# ---------------------------------------------------------------------------


class TestResolveInputsRepositoryRef:
    def test_repository_ref_on_non_git_dir_raises(
        self, binder: LocalBinder, non_git_dir: Path
    ) -> None:
        declarations = {
            "repo": {
                "type": "repository-ref",
                "required": True,
                "target": str(non_git_dir),
            }
        }
        with pytest.raises(NotAGitRepositoryError):
            binder.resolve_inputs(declarations, {"repo": str(non_git_dir)})

    def test_repository_ref_on_git_dir_returns_absolute_path(
        self, binder: LocalBinder, git_repo: Path
    ) -> None:
        declarations = {
            "repo": {
                "type": "repository-ref",
                "required": True,
                "target": str(git_repo),
            }
        }
        result = binder.resolve_inputs(declarations, {"repo": str(git_repo)})
        assert os.path.isabs(result["repo"])
        assert result["repo"] == str(git_repo.resolve())


# ---------------------------------------------------------------------------
# bind_tools
# ---------------------------------------------------------------------------


class TestBindTools:
    def test_supported_tools_are_bound(self, binder: LocalBinder) -> None:
        bindings = binder.bind_tools(list(SUPPORTED_TOOLS))
        for tool_name in SUPPORTED_TOOLS:
            assert tool_name in bindings

    def test_bound_tool_is_callable(self, binder: LocalBinder) -> None:
        bindings = binder.bind_tools(["repository:read"])
        assert callable(bindings["repository:read"])

    def test_unsupported_tool_raises(self, binder: LocalBinder) -> None:
        with pytest.raises(UnsupportedToolError):
            binder.bind_tools(["pr:comment"])

    def test_unsupported_tool_error_message(self, binder: LocalBinder) -> None:
        with pytest.raises(UnsupportedToolError, match="pr:comment"):
            binder.bind_tools(["pr:comment"])

    def test_stub_raises_not_implemented_on_call(self, binder: LocalBinder) -> None:
        bindings = binder.bind_tools(["repository:read"])
        with pytest.raises(NotImplementedError):
            bindings["repository:read"]()

    def test_empty_tool_list(self, binder: LocalBinder) -> None:
        bindings = binder.bind_tools([])
        assert bindings == {}


# ---------------------------------------------------------------------------
# map_outputs
# ---------------------------------------------------------------------------


class TestMapOutputs:
    def test_output_paths_under_agentry_runs(
        self, binder: LocalBinder, tmp_path: Path
    ) -> None:
        paths = binder.map_outputs({}, str(tmp_path), "20260101T120000")
        for path in paths.values():
            assert ".agentry/runs/20260101T120000" in path

    def test_output_key_present(self, binder: LocalBinder, tmp_path: Path) -> None:
        paths = binder.map_outputs({}, str(tmp_path), "20260101T120000")
        assert "output" in paths

    def test_execution_record_key_present(
        self, binder: LocalBinder, tmp_path: Path
    ) -> None:
        paths = binder.map_outputs({}, str(tmp_path), "20260101T120000")
        assert "execution_record" in paths

    def test_execution_record_filename(
        self, binder: LocalBinder, tmp_path: Path
    ) -> None:
        paths = binder.map_outputs({}, str(tmp_path), "20260101T120000")
        assert paths["execution_record"].endswith("execution-record.json")

    def test_declared_output_paths_included(
        self, binder: LocalBinder, tmp_path: Path
    ) -> None:
        output_decl = {"output_paths": ["findings.json"]}
        paths = binder.map_outputs(output_decl, str(tmp_path), "20260101T120000")
        assert "findings" in paths
        assert paths["findings"].endswith("findings.json")


# ---------------------------------------------------------------------------
# _assert_git_repo helper
# ---------------------------------------------------------------------------


class TestAssertGitRepo:
    def test_passes_for_git_directory(self, git_repo: Path) -> None:
        result = _assert_git_repo(str(git_repo))
        assert result == git_repo.resolve()

    def test_raises_for_non_git_directory(self, non_git_dir: Path) -> None:
        with pytest.raises(NotAGitRepositoryError):
            _assert_git_repo(str(non_git_dir))

    def test_error_contains_path(self, non_git_dir: Path) -> None:
        with pytest.raises(NotAGitRepositoryError, match=str(non_git_dir)):
            _assert_git_repo(str(non_git_dir))

    def test_error_message_is_helpful(self, non_git_dir: Path) -> None:
        with pytest.raises(
            NotAGitRepositoryError, match="not a git repository"
        ):
            _assert_git_repo(str(non_git_dir))


# ---------------------------------------------------------------------------
# Registry: discover_binders and get_binder
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_discover_binders_always_has_local(self) -> None:
        binders = discover_binders()
        assert "local" in binders

    def test_discover_binders_local_is_local_binder_class(self) -> None:
        binders = discover_binders()
        assert binders["local"] is LocalBinder

    def test_get_binder_default_returns_local(self) -> None:
        binder = get_binder()
        assert isinstance(binder, LocalBinder)

    def test_get_binder_local_name_returns_local(self) -> None:
        binder = get_binder("local")
        assert isinstance(binder, LocalBinder)

    def test_get_binder_unknown_name_raises_key_error(self) -> None:
        with pytest.raises(KeyError):
            get_binder("nonexistent-binder")

    def test_get_binder_error_message_mentions_name(self) -> None:
        with pytest.raises(KeyError, match="nonexistent-binder"):
            get_binder("nonexistent-binder")
