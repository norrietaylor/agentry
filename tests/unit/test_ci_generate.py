"""Unit and integration tests for agentry ci generate command (T04.3).

Tests cover:
- YAML generation for pull_request trigger: on.pull_request block present
- YAML generation for push trigger: on.push block present
- YAML generation for schedule trigger with cron: on.schedule block with cron expression
- YAML generation for issues trigger: on.issues block present
- YAML generation for multiple triggers: pull_request,schedule
- Permission derivation: pr:comment tool gets pull-requests: write
- Permission derivation: only repository:read gets contents: read only
- --dry-run: YAML printed to stdout, no file written
- File output: file written to correct path (<output_dir>/agentry-<name>.yaml)
- Composed workflow rejection: error message verified
- Missing --schedule when schedule trigger specified: error verified
- --target with unsupported value: error verified
- Generated YAML is valid YAML (parsed with yaml.safe_load)
- Minimal test workflow YAML fixture
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from agentry.ci.github_actions_renderer import (
    _build_triggers,
    _derive_permissions,
    render_pipeline_yaml,
)
from agentry.cli import cli
from agentry.models.workflow import WorkflowDefinition


# ---------------------------------------------------------------------------
# Minimal test workflow YAML fixtures
# ---------------------------------------------------------------------------

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

_WORKFLOW_WITH_PR_COMMENT_TOOL_YAML = """\
identity:
  name: pr-reviewer
  version: 1.0.0
  description: A PR review workflow that posts comments.

tools:
  capabilities:
    - pr:comment

model:
  provider: anthropic
  model_id: claude-sonnet-4-20250514
  temperature: 0.2
  max_tokens: 1024
  system_prompt: "Review the diff and post comments."

safety:
  trust: elevated
  resources:
    timeout: 60

output:
  schema: {}

composition:
  steps: []
"""

_WORKFLOW_WITH_REPO_READ_YAML = """\
identity:
  name: repo-reader
  version: 1.0.0
  description: A workflow that reads the repository.

tools:
  capabilities:
    - repository:read

model:
  provider: anthropic
  model_id: claude-sonnet-4-20250514
  temperature: 0.2
  max_tokens: 1024
  system_prompt: "Read and analyze."

safety:
  trust: elevated
  resources:
    timeout: 60

output:
  schema: {}

composition:
  steps: []
"""

_COMPOSED_WORKFLOW_YAML = """\
identity:
  name: composed-workflow
  version: 1.0.0
  description: A composed workflow.

tools:
  capabilities: []

model:
  provider: anthropic
  model_id: claude-sonnet-4-20250514
  temperature: 0.2
  max_tokens: 1024
  system_prompt: "Orchestrate."

safety:
  trust: elevated
  resources:
    timeout: 60

output:
  schema: {}

composition:
  steps:
    - name: triage
      workflow: workflows/triage.yaml
"""


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _write_workflow(tmp_path: Path, content: str, name: str = "workflow.yaml") -> Path:
    """Write a workflow YAML file to tmp_path and return the path."""
    wf = tmp_path / name
    wf.write_text(content)
    return wf


def _parse_yaml(text: str) -> dict[str, Any]:
    """Parse a YAML string and return the resulting dict."""
    result = yaml.safe_load(text)
    assert result is not None, "YAML parsed to None — empty document?"
    return result  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Unit tests: _derive_permissions
# ---------------------------------------------------------------------------


class TestDerivePermissions:
    """Unit tests for _derive_permissions()."""

    def test_empty_capabilities_returns_contents_read(self) -> None:
        """No capabilities defaults to contents:read only."""
        perms = _derive_permissions([])
        assert perms == {"contents": "read"}

    def test_pr_comment_adds_pull_requests_write(self) -> None:
        """pr:comment capability grants pull-requests:write."""
        perms = _derive_permissions(["pr:comment"])
        assert perms.get("pull-requests") == "write"

    def test_pr_comment_retains_contents_read(self) -> None:
        """pr:comment does not remove contents:read."""
        perms = _derive_permissions(["pr:comment"])
        assert perms.get("contents") == "read"

    def test_repository_read_gives_contents_read(self) -> None:
        """repository:read grants contents:read."""
        perms = _derive_permissions(["repository:read"])
        assert perms.get("contents") == "read"
        assert "pull-requests" not in perms

    def test_repository_write_gives_contents_write(self) -> None:
        """repository:write upgrades contents to write."""
        perms = _derive_permissions(["repository:write"])
        assert perms.get("contents") == "write"

    def test_multiple_capabilities_combined(self) -> None:
        """pr:comment + repository:read gives both pull-requests:write and contents:read."""
        perms = _derive_permissions(["pr:comment", "repository:read"])
        assert perms.get("pull-requests") == "write"
        assert perms.get("contents") == "read"

    def test_no_downgrade_from_write_to_read(self) -> None:
        """Adding repository:read after repository:write should not downgrade contents."""
        perms = _derive_permissions(["repository:write", "repository:read"])
        assert perms.get("contents") == "write"

    def test_issue_capability_adds_issues_write(self) -> None:
        """issue: prefix grants issues:write."""
        perms = _derive_permissions(["issue:create"])
        assert perms.get("issues") == "write"


# ---------------------------------------------------------------------------
# Unit tests: _build_triggers
# ---------------------------------------------------------------------------


class TestBuildTriggers:
    """Unit tests for _build_triggers()."""

    def test_pull_request_trigger(self) -> None:
        """pull_request trigger produces an on.pull_request block."""
        triggers = _build_triggers(["pull_request"], schedule=None)
        assert "pull_request" in triggers

    def test_push_trigger(self) -> None:
        """push trigger produces an on.push block."""
        triggers = _build_triggers(["push"], schedule=None)
        assert "push" in triggers

    def test_issues_trigger(self) -> None:
        """issues trigger produces an on.issues block."""
        triggers = _build_triggers(["issues"], schedule=None)
        assert "issues" in triggers

    def test_issues_trigger_has_types(self) -> None:
        """issues trigger block contains a 'types' list."""
        triggers = _build_triggers(["issues"], schedule=None)
        assert "types" in triggers["issues"]

    def test_schedule_trigger_with_cron(self) -> None:
        """schedule trigger with cron expression produces on.schedule with cron entry."""
        cron = "0 2 * * 1"
        triggers = _build_triggers(["schedule"], schedule=cron)
        assert "schedule" in triggers
        assert triggers["schedule"] == [{"cron": cron}]

    def test_multiple_triggers(self) -> None:
        """Multiple triggers all appear in the returned dict."""
        triggers = _build_triggers(["pull_request", "push"], schedule=None)
        assert "pull_request" in triggers
        assert "push" in triggers

    def test_pull_request_and_schedule(self) -> None:
        """pull_request + schedule both appear and schedule has cron."""
        cron = "0 2 * * 1"
        triggers = _build_triggers(["pull_request", "schedule"], schedule=cron)
        assert "pull_request" in triggers
        assert "schedule" in triggers
        assert triggers["schedule"] == [{"cron": cron}]


# ---------------------------------------------------------------------------
# Unit tests: render_pipeline_yaml
# ---------------------------------------------------------------------------


class TestRenderPipelineYaml:
    """Unit tests for render_pipeline_yaml()."""

    def _make_workflow(self, tmp_path: Path, content: str) -> WorkflowDefinition:
        from agentry.parser import load_workflow_file

        wf_path = _write_workflow(tmp_path, content)
        return load_workflow_file(str(wf_path))

    def test_output_is_valid_yaml(self, tmp_path: Path) -> None:
        """render_pipeline_yaml() output must parse without error."""
        from agentry.parser import load_workflow_file

        wf_path = _write_workflow(tmp_path, _MINIMAL_WORKFLOW_YAML)
        wf = load_workflow_file(str(wf_path))
        output = render_pipeline_yaml(wf, str(wf_path), ["pull_request"])
        parsed = _parse_yaml(output)
        assert isinstance(parsed, dict)

    def test_name_derived_from_workflow_identity(self, tmp_path: Path) -> None:
        """Generated YAML name includes the workflow identity name."""
        from agentry.parser import load_workflow_file

        wf_path = _write_workflow(tmp_path, _MINIMAL_WORKFLOW_YAML)
        wf = load_workflow_file(str(wf_path))
        output = render_pipeline_yaml(wf, str(wf_path), ["pull_request"])
        parsed = _parse_yaml(output)
        assert "code-review" in parsed["name"]

    def test_pull_request_trigger_in_on_block(self, tmp_path: Path) -> None:
        """YAML on block contains pull_request when requested."""
        from agentry.parser import load_workflow_file

        wf_path = _write_workflow(tmp_path, _MINIMAL_WORKFLOW_YAML)
        wf = load_workflow_file(str(wf_path))
        output = render_pipeline_yaml(wf, str(wf_path), ["pull_request"])
        parsed = _parse_yaml(output)
        assert "pull_request" in parsed["on"]

    def test_push_trigger_in_on_block(self, tmp_path: Path) -> None:
        """YAML on block contains push when requested."""
        from agentry.parser import load_workflow_file

        wf_path = _write_workflow(tmp_path, _MINIMAL_WORKFLOW_YAML)
        wf = load_workflow_file(str(wf_path))
        output = render_pipeline_yaml(wf, str(wf_path), ["push"])
        parsed = _parse_yaml(output)
        assert "push" in parsed["on"]

    def test_schedule_trigger_in_on_block_with_cron(self, tmp_path: Path) -> None:
        """YAML on block contains schedule with correct cron expression."""
        from agentry.parser import load_workflow_file

        cron = "0 2 * * 1"
        wf_path = _write_workflow(tmp_path, _MINIMAL_WORKFLOW_YAML)
        wf = load_workflow_file(str(wf_path))
        output = render_pipeline_yaml(wf, str(wf_path), ["schedule"], schedule=cron)
        parsed = _parse_yaml(output)
        assert "schedule" in parsed["on"]
        assert parsed["on"]["schedule"] == [{"cron": cron}]

    def test_issues_trigger_in_on_block(self, tmp_path: Path) -> None:
        """YAML on block contains issues when requested."""
        from agentry.parser import load_workflow_file

        wf_path = _write_workflow(tmp_path, _MINIMAL_WORKFLOW_YAML)
        wf = load_workflow_file(str(wf_path))
        output = render_pipeline_yaml(wf, str(wf_path), ["issues"])
        parsed = _parse_yaml(output)
        assert "issues" in parsed["on"]

    def test_multiple_triggers_both_present(self, tmp_path: Path) -> None:
        """Both pull_request and schedule appear in on block."""
        from agentry.parser import load_workflow_file

        cron = "0 2 * * 1"
        wf_path = _write_workflow(tmp_path, _MINIMAL_WORKFLOW_YAML)
        wf = load_workflow_file(str(wf_path))
        output = render_pipeline_yaml(
            wf, str(wf_path), ["pull_request", "schedule"], schedule=cron
        )
        parsed = _parse_yaml(output)
        assert "pull_request" in parsed["on"]
        assert "schedule" in parsed["on"]

    def test_pr_comment_tool_grants_pull_requests_write(self, tmp_path: Path) -> None:
        """Workflow with pr:comment tool gets pull-requests:write in permissions."""
        from agentry.parser import load_workflow_file

        wf_path = _write_workflow(tmp_path, _WORKFLOW_WITH_PR_COMMENT_TOOL_YAML)
        wf = load_workflow_file(str(wf_path))
        output = render_pipeline_yaml(wf, str(wf_path), ["pull_request"])
        parsed = _parse_yaml(output)
        perms = parsed.get("permissions", {})
        assert perms.get("pull-requests") == "write"

    def test_repository_read_only_gives_contents_read(self, tmp_path: Path) -> None:
        """Workflow with only repository:read gets contents:read, no extra permissions."""
        from agentry.parser import load_workflow_file

        wf_path = _write_workflow(tmp_path, _WORKFLOW_WITH_REPO_READ_YAML)
        wf = load_workflow_file(str(wf_path))
        output = render_pipeline_yaml(wf, str(wf_path), ["pull_request"])
        parsed = _parse_yaml(output)
        perms = parsed.get("permissions", {})
        assert perms.get("contents") == "read"
        assert "pull-requests" not in perms

    def test_jobs_section_present(self, tmp_path: Path) -> None:
        """Generated YAML contains a jobs section."""
        from agentry.parser import load_workflow_file

        wf_path = _write_workflow(tmp_path, _MINIMAL_WORKFLOW_YAML)
        wf = load_workflow_file(str(wf_path))
        output = render_pipeline_yaml(wf, str(wf_path), ["pull_request"])
        parsed = _parse_yaml(output)
        assert "jobs" in parsed

    def test_agentry_job_runs_on_ubuntu_latest(self, tmp_path: Path) -> None:
        """The agentry job uses ubuntu-latest runner."""
        from agentry.parser import load_workflow_file

        wf_path = _write_workflow(tmp_path, _MINIMAL_WORKFLOW_YAML)
        wf = load_workflow_file(str(wf_path))
        output = render_pipeline_yaml(wf, str(wf_path), ["pull_request"])
        parsed = _parse_yaml(output)
        assert parsed["jobs"]["agentry"]["runs-on"] == "ubuntu-latest"

    def test_run_step_includes_agentry_run_with_workflow_path(self, tmp_path: Path) -> None:
        """Run step command includes 'agentry run' and the workflow path."""
        from agentry.parser import load_workflow_file

        wf_path = _write_workflow(tmp_path, _MINIMAL_WORKFLOW_YAML)
        wf = load_workflow_file(str(wf_path))
        output = render_pipeline_yaml(wf, str(wf_path), ["pull_request"])
        parsed = _parse_yaml(output)
        steps = parsed["jobs"]["agentry"]["steps"]
        run_step = next((s for s in steps if "run" in s and "agentry run" in s["run"]), None)
        assert run_step is not None, "No step with 'agentry run' found"
        assert str(wf_path) in run_step["run"]

    def test_run_step_env_includes_anthropic_api_key_secret(self, tmp_path: Path) -> None:
        """Run step env injects ANTHROPIC_API_KEY from secrets."""
        from agentry.parser import load_workflow_file

        wf_path = _write_workflow(tmp_path, _MINIMAL_WORKFLOW_YAML)
        wf = load_workflow_file(str(wf_path))
        output = render_pipeline_yaml(wf, str(wf_path), ["pull_request"])
        parsed = _parse_yaml(output)
        steps = parsed["jobs"]["agentry"]["steps"]
        run_step = next((s for s in steps if "run" in s and "agentry run" in s["run"]), None)
        assert run_step is not None
        env = run_step.get("env", {})
        assert "ANTHROPIC_API_KEY" in env
        assert "secrets.ANTHROPIC_API_KEY" in env["ANTHROPIC_API_KEY"]

    def test_run_step_env_includes_github_token_secret(self, tmp_path: Path) -> None:
        """Run step env injects GITHUB_TOKEN from secrets."""
        from agentry.parser import load_workflow_file

        wf_path = _write_workflow(tmp_path, _MINIMAL_WORKFLOW_YAML)
        wf = load_workflow_file(str(wf_path))
        output = render_pipeline_yaml(wf, str(wf_path), ["pull_request"])
        parsed = _parse_yaml(output)
        steps = parsed["jobs"]["agentry"]["steps"]
        run_step = next((s for s in steps if "run" in s and "agentry run" in s["run"]), None)
        assert run_step is not None
        env = run_step.get("env", {})
        assert "GITHUB_TOKEN" in env
        assert "secrets.GITHUB_TOKEN" in env["GITHUB_TOKEN"]

    def test_steps_include_checkout(self, tmp_path: Path) -> None:
        """Generated YAML steps include a checkout step."""
        from agentry.parser import load_workflow_file

        wf_path = _write_workflow(tmp_path, _MINIMAL_WORKFLOW_YAML)
        wf = load_workflow_file(str(wf_path))
        output = render_pipeline_yaml(wf, str(wf_path), ["pull_request"])
        parsed = _parse_yaml(output)
        steps = parsed["jobs"]["agentry"]["steps"]
        checkout_step = next(
            (s for s in steps if s.get("uses", "").startswith("actions/checkout")), None
        )
        assert checkout_step is not None, "No checkout step found"

    def test_steps_include_setup_python(self, tmp_path: Path) -> None:
        """Generated YAML steps include a setup-python step."""
        from agentry.parser import load_workflow_file

        wf_path = _write_workflow(tmp_path, _MINIMAL_WORKFLOW_YAML)
        wf = load_workflow_file(str(wf_path))
        output = render_pipeline_yaml(wf, str(wf_path), ["pull_request"])
        parsed = _parse_yaml(output)
        steps = parsed["jobs"]["agentry"]["steps"]
        setup_step = next(
            (s for s in steps if s.get("uses", "").startswith("actions/setup-python")), None
        )
        assert setup_step is not None, "No setup-python step found"


# ---------------------------------------------------------------------------
# CLI integration tests: dry-run and file output
# ---------------------------------------------------------------------------


class TestCiGenerateDryRun:
    """CLI tests for --dry-run behaviour."""

    def test_dry_run_prints_valid_yaml_to_stdout(self, tmp_path: Path) -> None:
        """--dry-run must print valid YAML to stdout."""
        wf = _write_workflow(tmp_path, _MINIMAL_WORKFLOW_YAML)
        runner = CliRunner()
        result = runner.invoke(
            cli, ["ci", "generate", "--target", "github", "--dry-run", str(wf)]
        )
        assert result.exit_code == 0, result.output
        parsed = _parse_yaml(result.output)
        assert isinstance(parsed, dict)

    def test_dry_run_output_contains_name_field(self, tmp_path: Path) -> None:
        """--dry-run YAML output must contain a top-level 'name' field."""
        wf = _write_workflow(tmp_path, _MINIMAL_WORKFLOW_YAML)
        runner = CliRunner()
        result = runner.invoke(
            cli, ["ci", "generate", "--target", "github", "--dry-run", str(wf)]
        )
        assert result.exit_code == 0
        parsed = _parse_yaml(result.output)
        assert "name" in parsed

    def test_dry_run_output_contains_jobs_section(self, tmp_path: Path) -> None:
        """--dry-run YAML output must contain a 'jobs' section."""
        wf = _write_workflow(tmp_path, _MINIMAL_WORKFLOW_YAML)
        runner = CliRunner()
        result = runner.invoke(
            cli, ["ci", "generate", "--target", "github", "--dry-run", str(wf)]
        )
        assert result.exit_code == 0
        parsed = _parse_yaml(result.output)
        assert "jobs" in parsed

    def test_dry_run_does_not_write_file(self, tmp_path: Path) -> None:
        """--dry-run must not write any file to disk."""
        wf = _write_workflow(tmp_path, _MINIMAL_WORKFLOW_YAML)
        output_dir = tmp_path / ".github" / "workflows"
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "ci",
                "generate",
                "--target",
                "github",
                "--dry-run",
                "--output-dir",
                str(output_dir),
                str(wf),
            ],
        )
        assert result.exit_code == 0
        # No file should have been written
        assert not output_dir.exists() or not any(output_dir.iterdir())

    def test_dry_run_pull_request_trigger_yaml_has_on_pull_request(self, tmp_path: Path) -> None:
        """--dry-run with pull_request trigger produces on.pull_request block."""
        wf = _write_workflow(tmp_path, _MINIMAL_WORKFLOW_YAML)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "ci",
                "generate",
                "--target",
                "github",
                "--triggers",
                "pull_request",
                "--dry-run",
                str(wf),
            ],
        )
        assert result.exit_code == 0
        parsed = _parse_yaml(result.output)
        assert "pull_request" in parsed["on"]

    def test_dry_run_push_trigger_yaml_has_on_push(self, tmp_path: Path) -> None:
        """--dry-run with push trigger produces on.push block."""
        wf = _write_workflow(tmp_path, _MINIMAL_WORKFLOW_YAML)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "ci",
                "generate",
                "--target",
                "github",
                "--triggers",
                "push",
                "--dry-run",
                str(wf),
            ],
        )
        assert result.exit_code == 0
        parsed = _parse_yaml(result.output)
        assert "push" in parsed["on"]

    def test_dry_run_schedule_trigger_yaml_has_on_schedule_with_cron(
        self, tmp_path: Path
    ) -> None:
        """--dry-run with schedule + cron produces on.schedule block with correct cron."""
        wf = _write_workflow(tmp_path, _MINIMAL_WORKFLOW_YAML)
        cron = "0 2 * * 1"
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "ci",
                "generate",
                "--target",
                "github",
                "--triggers",
                "schedule",
                "--schedule",
                cron,
                "--dry-run",
                str(wf),
            ],
        )
        assert result.exit_code == 0
        parsed = _parse_yaml(result.output)
        assert "schedule" in parsed["on"]
        cron_entries = parsed["on"]["schedule"]
        assert any(entry.get("cron") == cron for entry in cron_entries)

    def test_dry_run_issues_trigger_yaml_has_on_issues(self, tmp_path: Path) -> None:
        """--dry-run with issues trigger produces on.issues block."""
        wf = _write_workflow(tmp_path, _MINIMAL_WORKFLOW_YAML)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "ci",
                "generate",
                "--target",
                "github",
                "--triggers",
                "issues",
                "--dry-run",
                str(wf),
            ],
        )
        assert result.exit_code == 0
        parsed = _parse_yaml(result.output)
        assert "issues" in parsed["on"]

    def test_dry_run_multiple_triggers_both_in_on_block(self, tmp_path: Path) -> None:
        """--dry-run with pull_request,schedule triggers produces both in on block."""
        wf = _write_workflow(tmp_path, _MINIMAL_WORKFLOW_YAML)
        cron = "0 2 * * 1"
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "ci",
                "generate",
                "--target",
                "github",
                "--triggers",
                "pull_request,schedule",
                "--schedule",
                cron,
                "--dry-run",
                str(wf),
            ],
        )
        assert result.exit_code == 0
        parsed = _parse_yaml(result.output)
        assert "pull_request" in parsed["on"]
        assert "schedule" in parsed["on"]


# ---------------------------------------------------------------------------
# CLI integration tests: file output
# ---------------------------------------------------------------------------


class TestCiGenerateFileOutput:
    """CLI tests for file output behaviour."""

    def test_file_output_default_dir(self, tmp_path: Path) -> None:
        """Without --dry-run, file is written to <output_dir>/agentry-<name>.yaml."""
        wf = _write_workflow(tmp_path, _MINIMAL_WORKFLOW_YAML, "code-review.yaml")
        output_dir = tmp_path / "output"
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "ci",
                "generate",
                "--target",
                "github",
                "--output-dir",
                str(output_dir),
                str(wf),
            ],
        )
        assert result.exit_code == 0, result.output
        expected = output_dir / "agentry-code-review.yaml"
        assert expected.exists(), f"Expected {expected} to exist"

    def test_file_output_content_is_valid_yaml(self, tmp_path: Path) -> None:
        """Written file must be valid YAML."""
        wf = _write_workflow(tmp_path, _MINIMAL_WORKFLOW_YAML, "code-review.yaml")
        output_dir = tmp_path / "output"
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "ci",
                "generate",
                "--target",
                "github",
                "--output-dir",
                str(output_dir),
                str(wf),
            ],
        )
        assert result.exit_code == 0
        written_file = output_dir / "agentry-code-review.yaml"
        content = written_file.read_text()
        parsed = _parse_yaml(content)
        assert isinstance(parsed, dict)

    def test_file_output_path_mentioned_in_stdout(self, tmp_path: Path) -> None:
        """agentry ci generate must print the generated file path to stdout."""
        wf = _write_workflow(tmp_path, _MINIMAL_WORKFLOW_YAML, "workflow.yaml")
        output_dir = tmp_path / "output"
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "ci",
                "generate",
                "--target",
                "github",
                "--output-dir",
                str(output_dir),
                str(wf),
            ],
        )
        assert result.exit_code == 0
        # Stdout should mention the output file path
        assert "agentry-workflow.yaml" in result.output

    def test_file_output_creates_parent_directory(self, tmp_path: Path) -> None:
        """Output directory must be created automatically if it does not exist."""
        wf = _write_workflow(tmp_path, _MINIMAL_WORKFLOW_YAML)
        output_dir = tmp_path / "deeply" / "nested" / "workflows"
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "ci",
                "generate",
                "--target",
                "github",
                "--output-dir",
                str(output_dir),
                str(wf),
            ],
        )
        assert result.exit_code == 0
        assert output_dir.exists()

    def test_file_output_pr_comment_permission_in_file(self, tmp_path: Path) -> None:
        """Written file for workflow with pr:comment must have pull-requests:write permission."""
        wf = _write_workflow(tmp_path, _WORKFLOW_WITH_PR_COMMENT_TOOL_YAML, "pr-reviewer.yaml")
        output_dir = tmp_path / "output"
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "ci",
                "generate",
                "--target",
                "github",
                "--output-dir",
                str(output_dir),
                str(wf),
            ],
        )
        assert result.exit_code == 0
        written_file = output_dir / "agentry-pr-reviewer.yaml"
        content = written_file.read_text()
        parsed = _parse_yaml(content)
        assert parsed["permissions"].get("pull-requests") == "write"

    def test_file_output_repo_read_only_permission_in_file(self, tmp_path: Path) -> None:
        """Written file for workflow with only repository:read has no pull-requests permission."""
        wf = _write_workflow(tmp_path, _WORKFLOW_WITH_REPO_READ_YAML, "repo-reader.yaml")
        output_dir = tmp_path / "output"
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "ci",
                "generate",
                "--target",
                "github",
                "--output-dir",
                str(output_dir),
                str(wf),
            ],
        )
        assert result.exit_code == 0
        written_file = output_dir / "agentry-repo-reader.yaml"
        content = written_file.read_text()
        parsed = _parse_yaml(content)
        assert parsed["permissions"].get("contents") == "read"
        assert "pull-requests" not in parsed["permissions"]


# ---------------------------------------------------------------------------
# CLI integration tests: error cases
# ---------------------------------------------------------------------------


class TestCiGenerateErrors:
    """CLI tests for error conditions."""

    def test_composed_workflow_rejected_with_error_message(self, tmp_path: Path) -> None:
        """Composed workflows must be rejected with the prescribed error message."""
        wf = _write_workflow(tmp_path, _COMPOSED_WORKFLOW_YAML)
        runner = CliRunner()
        result = runner.invoke(
            cli, ["ci", "generate", "--target", "github", str(wf)]
        )
        assert result.exit_code == 1
        combined = result.output + (str(result.exception) if result.exception else "")
        assert "Composed workflow CI generation is not yet supported" in combined
        assert "Generate CI config for each component workflow individually" in combined

    def test_missing_schedule_flag_when_schedule_trigger(self, tmp_path: Path) -> None:
        """schedule trigger without --schedule flag must exit 1 with a clear error."""
        wf = _write_workflow(tmp_path, _MINIMAL_WORKFLOW_YAML)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["ci", "generate", "--target", "github", "--triggers", "schedule", str(wf)],
        )
        assert result.exit_code == 1
        combined = result.output + (str(result.exception) if result.exception else "")
        assert "--schedule" in combined or "schedule" in combined.lower()

    def test_unsupported_target_exits_one(self, tmp_path: Path) -> None:
        """Unsupported --target value must exit 1 with the target name in the error."""
        wf = _write_workflow(tmp_path, _MINIMAL_WORKFLOW_YAML)
        runner = CliRunner()
        result = runner.invoke(
            cli, ["ci", "generate", "--target", "bitbucket", str(wf)]
        )
        assert result.exit_code == 1
        combined = result.output + (str(result.exception) if result.exception else "")
        assert "bitbucket" in combined

    def test_unsupported_target_mentions_github(self, tmp_path: Path) -> None:
        """Error for unsupported --target must mention 'github' as supported value."""
        wf = _write_workflow(tmp_path, _MINIMAL_WORKFLOW_YAML)
        runner = CliRunner()
        result = runner.invoke(
            cli, ["ci", "generate", "--target", "circleci", str(wf)]
        )
        assert result.exit_code == 1
        combined = result.output.lower() + (
            str(result.exception).lower() if result.exception else ""
        )
        assert "github" in combined
