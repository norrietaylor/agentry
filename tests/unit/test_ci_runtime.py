"""Unit tests for T05.3: CI runtime binder auto-detection and generate_pipeline_config.

Tests cover:
- Auto-detection: GITHUB_ACTIONS=true selects github-actions binder
- Auto-detection: GITHUB_ACTIONS unset selects local binder
- --binder override: explicit flag beats env-var auto-detection
- Preflight wiring: github-actions binder adds GitHubTokenScopeCheck
- generate_pipeline_config returns correct top-level structure
- generate_pipeline_config permissions derivation for various tool sets
- generate_pipeline_config trigger block construction
- generate_pipeline_config run step references workflow_path
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from agentry.binders.github_actions import GitHubActionsBinder
from agentry.cli import cli


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_gha_env(
    tmp_path: Path,
    event_name: str = "push",
    payload: dict[str, Any] | None = None,
    token: str = "ghp_testtoken",
    repository: str = "owner/repo",
) -> dict[str, str]:
    """Build a minimal GitHub Actions environment dict for testing."""
    if payload is None:
        payload = {}
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(payload), encoding="utf-8")
    workspace = str(tmp_path / "workspace")
    Path(workspace).mkdir(parents=True, exist_ok=True)
    return {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": event_name,
        "GITHUB_EVENT_PATH": str(event_file),
        "GITHUB_WORKSPACE": workspace,
        "GITHUB_REPOSITORY": repository,
        "GITHUB_TOKEN": token,
    }


def _make_binder(tmp_path: Path, **kwargs: Any) -> GitHubActionsBinder:
    """Return a GitHubActionsBinder constructed with a minimal env."""
    env = _make_gha_env(tmp_path, **kwargs)
    # Strip the GITHUB_ACTIONS key -- the binder itself doesn't need it.
    env.pop("GITHUB_ACTIONS", None)
    return GitHubActionsBinder(env=env)


_MINIMAL_WORKFLOW_YAML = """\
identity:
  name: code-review
  version: 1.0.0
  description: A code review workflow.

tools:
  capabilities: []

model:
  provider: anthropic
  model_id: claude-sonnet-4-20250514
  temperature: 0.2
  max_tokens: 1024
  system_prompt: "Review the diff."

safety:
  trust: elevated
  resources:
    timeout: 60

output:
  schema: {}

composition:
  steps: []
"""

_WORKFLOW_WITH_PR_TOOLS_YAML = """\
identity:
  name: pr-review
  version: 1.0.0
  description: A PR review workflow.

tools:
  capabilities:
    - pr:comment
    - pr:review
    - repository:read

model:
  provider: anthropic
  model_id: claude-sonnet-4-20250514
  temperature: 0.2
  max_tokens: 1024
  system_prompt: "Review and comment on the PR."

safety:
  trust: elevated
  resources:
    timeout: 60

output:
  schema: {}

composition:
  steps: []
"""


# ---------------------------------------------------------------------------
# Binder auto-detection via CLI (test the CLI's binder resolution logic)
# ---------------------------------------------------------------------------


class TestBinderAutoDetection:
    """Verify that binder is auto-detected from GITHUB_ACTIONS env var."""

    def test_github_actions_env_selects_github_actions_binder(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When GITHUB_ACTIONS=true, _binder_name should be 'github-actions'."""
        # Set up env vars that the GitHubActionsBinder constructor needs.
        event_file = tmp_path / "event.json"
        event_file.write_text("{}", encoding="utf-8")
        ws = str(tmp_path / "ws")
        Path(ws).mkdir()

        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_file))
        monkeypatch.setenv("GITHUB_WORKSPACE", ws)
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_fake_token")

        workflow_file = tmp_path / "workflow.yaml"
        workflow_file.write_text(_MINIMAL_WORKFLOW_YAML, encoding="utf-8")

        captured_binder_name: list[str] = []

        original_get_binder = __import__(
            "agentry.binders.registry", fromlist=["get_binder"]
        ).get_binder

        def spy_get_binder(name: str | None = None) -> Any:
            if name is not None:
                captured_binder_name.append(name)
            return original_get_binder(name)

        runner = CliRunner(mix_stderr=False)
        with patch("agentry.binders.registry.get_binder", side_effect=spy_get_binder):
            runner.invoke(
                cli,
                ["run", str(workflow_file), "--skip-preflight"],
                catch_exceptions=True,
            )

        assert "github-actions" in captured_binder_name, (
            f"Expected 'github-actions' binder to be selected. "
            f"Got: {captured_binder_name!r}"
        )

    def test_no_github_actions_env_selects_local_binder(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without GITHUB_ACTIONS=true, binder should default to 'local'."""
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

        workflow_file = tmp_path / "workflow.yaml"
        workflow_file.write_text(_MINIMAL_WORKFLOW_YAML, encoding="utf-8")

        captured_binder_name: list[str] = []

        original_get_binder = __import__(
            "agentry.binders.registry", fromlist=["get_binder"]
        ).get_binder

        def spy_get_binder(name: str | None = None) -> Any:
            if name is not None:
                captured_binder_name.append(name)
            return original_get_binder(name)

        runner = CliRunner(mix_stderr=False)
        with patch("agentry.binders.registry.get_binder", side_effect=spy_get_binder):
            runner.invoke(
                cli,
                ["run", str(workflow_file), "--skip-preflight"],
                catch_exceptions=True,
                env={"ANTHROPIC_API_KEY": "test-key"},
            )

        # local binder is selected without explicit name, but "local" is passed
        assert "github-actions" not in captured_binder_name, (
            f"github-actions binder should NOT be selected without GITHUB_ACTIONS=true. "
            f"Got: {captured_binder_name!r}"
        )

    def test_github_actions_false_selects_local_binder(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GITHUB_ACTIONS=false (not 'true') should still select local binder."""
        monkeypatch.setenv("GITHUB_ACTIONS", "false")

        workflow_file = tmp_path / "workflow.yaml"
        workflow_file.write_text(_MINIMAL_WORKFLOW_YAML, encoding="utf-8")

        captured_binder_name: list[str] = []
        original_get_binder = __import__(
            "agentry.binders.registry", fromlist=["get_binder"]
        ).get_binder

        def spy_get_binder(name: str | None = None) -> Any:
            if name is not None:
                captured_binder_name.append(name)
            return original_get_binder(name)

        runner = CliRunner(mix_stderr=False)
        with patch("agentry.binders.registry.get_binder", side_effect=spy_get_binder):
            runner.invoke(
                cli,
                ["run", str(workflow_file), "--skip-preflight"],
                catch_exceptions=True,
                env={"GITHUB_ACTIONS": "false", "ANTHROPIC_API_KEY": "test-key"},
            )

        assert "github-actions" not in captured_binder_name, (
            f"GITHUB_ACTIONS=false should NOT select github-actions binder. "
            f"Got: {captured_binder_name!r}"
        )


# ---------------------------------------------------------------------------
# --binder override
# ---------------------------------------------------------------------------


class TestBinderOverrideFlag:
    """Verify that --binder flag overrides auto-detection."""

    def test_binder_local_overrides_github_actions_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--binder local with GITHUB_ACTIONS=true must use local binder."""
        workflow_file = tmp_path / "workflow.yaml"
        workflow_file.write_text(_MINIMAL_WORKFLOW_YAML, encoding="utf-8")

        captured_binder_name: list[str] = []
        original_get_binder = __import__(
            "agentry.binders.registry", fromlist=["get_binder"]
        ).get_binder

        def spy_get_binder(name: str | None = None) -> Any:
            if name is not None:
                captured_binder_name.append(name)
            return original_get_binder(name)

        runner = CliRunner(mix_stderr=False)
        with patch("agentry.binders.registry.get_binder", side_effect=spy_get_binder):
            runner.invoke(
                cli,
                ["run", str(workflow_file), "--skip-preflight", "--binder", "local"],
                catch_exceptions=True,
                env={"GITHUB_ACTIONS": "true", "ANTHROPIC_API_KEY": "test-key"},
            )

        assert "local" in captured_binder_name, (
            f"Expected 'local' binder when --binder local given. Got: {captured_binder_name!r}"
        )
        assert "github-actions" not in captured_binder_name

    def test_binder_flag_value_is_passed_to_get_binder(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The explicit --binder NAME flag value is forwarded to get_binder()."""
        workflow_file = tmp_path / "workflow.yaml"
        workflow_file.write_text(_MINIMAL_WORKFLOW_YAML, encoding="utf-8")

        captured_binder_name: list[str] = []
        original_get_binder = __import__(
            "agentry.binders.registry", fromlist=["get_binder"]
        ).get_binder

        def spy_get_binder(name: str | None = None) -> Any:
            if name is not None:
                captured_binder_name.append(name)
            return original_get_binder(name)

        runner = CliRunner(mix_stderr=False)
        with patch("agentry.binders.registry.get_binder", side_effect=spy_get_binder):
            runner.invoke(
                cli,
                ["run", str(workflow_file), "--skip-preflight", "--binder", "local"],
                catch_exceptions=True,
                env={"ANTHROPIC_API_KEY": "test-key"},
            )

        assert "local" in captured_binder_name


# ---------------------------------------------------------------------------
# Preflight wiring: GitHubTokenScopeCheck added for github-actions binder
# ---------------------------------------------------------------------------


class TestPreflightWiring:
    """Verify GitHubTokenScopeCheck is added to checks when using github-actions binder."""

    def test_github_token_scope_check_added_for_github_actions_binder(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When binder is github-actions, GitHubTokenScopeCheck is in the check list."""
        event_file = tmp_path / "event.json"
        event_file.write_text("{}", encoding="utf-8")
        ws = str(tmp_path / "ws")
        Path(ws).mkdir()

        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_fake_token")
        monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
        monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_file))
        monkeypatch.setenv("GITHUB_WORKSPACE", ws)
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-api-key")

        workflow_file = tmp_path / "workflow.yaml"
        workflow_file.write_text(_MINIMAL_WORKFLOW_YAML, encoding="utf-8")

        collected_checks: list[Any] = []

        def spy_setup_phase(**kwargs: Any) -> Any:
            # The CLI constructs SetupPhase with preflight_checks= keyword arg.
            checks = kwargs.get("preflight_checks", [])
            collected_checks.extend(checks)
            mock = MagicMock()
            mock_result = MagicMock()
            mock_result.manifest_path = str(tmp_path / "manifest.json")
            mock.run.return_value = mock_result
            return mock

        runner = CliRunner(mix_stderr=False)
        with patch("agentry.security.setup.SetupPhase", side_effect=spy_setup_phase):
            runner.invoke(
                cli,
                ["run", str(workflow_file)],
                catch_exceptions=True,
            )

        check_types = [type(c).__name__ for c in collected_checks]
        assert "GitHubTokenScopeCheck" in check_types, (
            f"Expected GitHubTokenScopeCheck in preflight checks when using github-actions binder. "
            f"Found check types: {check_types!r}"
        )

    def test_github_token_scope_check_not_added_for_local_binder(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When binder is local, GitHubTokenScopeCheck must NOT be in the check list."""
        workflow_file = tmp_path / "workflow.yaml"
        workflow_file.write_text(_MINIMAL_WORKFLOW_YAML, encoding="utf-8")

        collected_checks: list[Any] = []

        def spy_setup_phase(*args: Any, **kwargs: Any) -> Any:
            checks = kwargs.get("checks", args[2] if len(args) > 2 else [])
            collected_checks.extend(checks)
            mock = MagicMock()
            mock.run.return_value = MagicMock(passed=True, manifest={})
            return mock

        env = {
            "ANTHROPIC_API_KEY": "test-api-key",
        }
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

        runner = CliRunner(mix_stderr=False)
        with patch("agentry.security.setup.SetupPhase", side_effect=spy_setup_phase):
            runner.invoke(
                cli,
                ["run", str(workflow_file)],
                catch_exceptions=True,
                env=env,
            )

        check_types = [type(c).__name__ for c in collected_checks]
        assert "GitHubTokenScopeCheck" not in check_types, (
            f"GitHubTokenScopeCheck should NOT be added for local binder. "
            f"Found check types: {check_types!r}"
        )


# ---------------------------------------------------------------------------
# generate_pipeline_config: structure and permissions
# ---------------------------------------------------------------------------


class TestGeneratePipelineConfigStructure:
    """Verify generate_pipeline_config returns the correct top-level structure."""

    def test_returns_dict_with_required_keys(self, tmp_path: Path) -> None:
        binder = _make_binder(tmp_path)
        config = binder.generate_pipeline_config()
        assert isinstance(config, dict)
        for key in ("name", "on", "permissions", "jobs"):
            assert key in config, f"Missing required key {key!r} in pipeline config"

    def test_name_includes_workflow_name(self, tmp_path: Path) -> None:
        binder = _make_binder(tmp_path)
        config = binder.generate_pipeline_config(workflow_name="my-analysis")
        assert config["name"] == "Agentry: my-analysis"

    def test_default_name_is_agentry_workflow(self, tmp_path: Path) -> None:
        binder = _make_binder(tmp_path)
        config = binder.generate_pipeline_config()
        assert config["name"] == "Agentry: agentry-workflow"

    def test_jobs_block_contains_agentry_job(self, tmp_path: Path) -> None:
        binder = _make_binder(tmp_path)
        config = binder.generate_pipeline_config()
        assert "agentry" in config["jobs"]

    def test_agentry_job_runs_on_ubuntu(self, tmp_path: Path) -> None:
        binder = _make_binder(tmp_path)
        config = binder.generate_pipeline_config()
        assert config["jobs"]["agentry"]["runs-on"] == "ubuntu-latest"

    def test_agentry_job_has_steps(self, tmp_path: Path) -> None:
        binder = _make_binder(tmp_path)
        config = binder.generate_pipeline_config()
        steps = config["jobs"]["agentry"]["steps"]
        assert isinstance(steps, list)
        assert len(steps) > 0

    def test_steps_include_checkout(self, tmp_path: Path) -> None:
        binder = _make_binder(tmp_path)
        config = binder.generate_pipeline_config()
        step_names = [s.get("name", "") for s in config["jobs"]["agentry"]["steps"]]
        assert any("checkout" in name.lower() for name in step_names)

    def test_steps_include_agentry_run(self, tmp_path: Path) -> None:
        binder = _make_binder(tmp_path)
        config = binder.generate_pipeline_config(workflow_path="my-workflow.yaml")
        run_steps = [s for s in config["jobs"]["agentry"]["steps"] if "run" in s]
        run_commands = [s["run"] for s in run_steps]
        assert any("agentry run" in cmd for cmd in run_commands)

    def test_run_step_includes_workflow_path(self, tmp_path: Path) -> None:
        binder = _make_binder(tmp_path)
        config = binder.generate_pipeline_config(workflow_path="workflows/review.yaml")
        run_steps = [s for s in config["jobs"]["agentry"]["steps"] if "run" in s]
        run_commands = [s["run"] for s in run_steps]
        assert any("workflows/review.yaml" in cmd for cmd in run_commands)

    def test_default_workflow_path_is_workflow_yaml(self, tmp_path: Path) -> None:
        binder = _make_binder(tmp_path)
        config = binder.generate_pipeline_config()
        run_steps = [s for s in config["jobs"]["agentry"]["steps"] if "run" in s]
        run_commands = [s["run"] for s in run_steps]
        assert any("workflow.yaml" in cmd for cmd in run_commands)


# ---------------------------------------------------------------------------
# generate_pipeline_config: permissions derivation
# ---------------------------------------------------------------------------


class TestGeneratePipelineConfigPermissions:
    """Verify permissions block is derived correctly from tool declarations."""

    def test_no_tools_yields_contents_read(self, tmp_path: Path) -> None:
        binder = _make_binder(tmp_path)
        config = binder.generate_pipeline_config(tool_declarations=[])
        assert config["permissions"]["contents"] == "read"

    def test_pr_comment_tool_yields_pull_requests_write(self, tmp_path: Path) -> None:
        binder = _make_binder(tmp_path)
        config = binder.generate_pipeline_config(
            tool_declarations=["pr:comment"]
        )
        assert config["permissions"].get("pull-requests") == "write"

    def test_pr_review_tool_yields_pull_requests_write(self, tmp_path: Path) -> None:
        binder = _make_binder(tmp_path)
        config = binder.generate_pipeline_config(
            tool_declarations=["pr:review"]
        )
        assert config["permissions"].get("pull-requests") == "write"

    def test_repository_read_tool_keeps_contents_read(self, tmp_path: Path) -> None:
        binder = _make_binder(tmp_path)
        config = binder.generate_pipeline_config(
            tool_declarations=["repository:read"]
        )
        assert config["permissions"]["contents"] == "read"

    def test_repository_write_tool_upgrades_to_contents_write(
        self, tmp_path: Path
    ) -> None:
        binder = _make_binder(tmp_path)
        config = binder.generate_pipeline_config(
            tool_declarations=["repository:write"]
        )
        assert config["permissions"]["contents"] == "write"

    def test_pr_and_repository_tools_combined(self, tmp_path: Path) -> None:
        binder = _make_binder(tmp_path)
        config = binder.generate_pipeline_config(
            tool_declarations=["pr:comment", "pr:review", "repository:read"]
        )
        assert config["permissions"]["contents"] == "read"
        assert config["permissions"]["pull-requests"] == "write"

    def test_contents_read_always_present(self, tmp_path: Path) -> None:
        """contents:read is always set as the baseline."""
        binder = _make_binder(tmp_path)
        config = binder.generate_pipeline_config(
            tool_declarations=["pr:comment"]
        )
        assert "contents" in config["permissions"]

    def test_no_pull_requests_permission_without_pr_tools(
        self, tmp_path: Path
    ) -> None:
        binder = _make_binder(tmp_path)
        config = binder.generate_pipeline_config(
            tool_declarations=["repository:read", "shell:execute"]
        )
        assert config["permissions"].get("pull-requests") is None


# ---------------------------------------------------------------------------
# generate_pipeline_config: trigger block
# ---------------------------------------------------------------------------


class TestGeneratePipelineConfigTriggers:
    """Verify on: trigger block is constructed correctly."""

    def test_default_trigger_is_pull_request(self, tmp_path: Path) -> None:
        binder = _make_binder(tmp_path)
        config = binder.generate_pipeline_config()
        assert "pull_request" in config["on"]

    def test_push_trigger_added(self, tmp_path: Path) -> None:
        binder = _make_binder(tmp_path)
        config = binder.generate_pipeline_config(triggers=["push"])
        assert "push" in config["on"]

    def test_schedule_trigger_includes_cron(self, tmp_path: Path) -> None:
        binder = _make_binder(tmp_path)
        config = binder.generate_pipeline_config(
            triggers=["schedule"], schedule="0 2 * * 1"
        )
        assert "schedule" in config["on"]
        schedules = config["on"]["schedule"]
        assert isinstance(schedules, list)
        assert any(entry.get("cron") == "0 2 * * 1" for entry in schedules)

    def test_issues_trigger_includes_types(self, tmp_path: Path) -> None:
        binder = _make_binder(tmp_path)
        config = binder.generate_pipeline_config(triggers=["issues"])
        assert "issues" in config["on"]
        assert "types" in config["on"]["issues"]

    def test_multiple_triggers(self, tmp_path: Path) -> None:
        binder = _make_binder(tmp_path)
        config = binder.generate_pipeline_config(
            triggers=["pull_request", "push"]
        )
        assert "pull_request" in config["on"]
        assert "push" in config["on"]
