"""Integration tests for T05.3: End-to-end CI pipeline YAML generation.

Tests invoke ``agentry ci generate`` via CliRunner on real workflow fixture files
and validate that the generated YAML is structurally correct GitHub Actions syntax.

Tests cover:
- Full end-to-end: generate GitHub Actions YAML from a workflow definition file
- YAML structure validation via yaml.safe_load
- Generated run step invokes agentry run with correct arguments
- Permissions block matches expected values for given tool sets
- Trigger configuration is correct for each supported trigger type
- Output file is created when --output-dir is specified
- --dry-run prints YAML to stdout

Uses CliRunner for CLI tests, tmp_path for output files.
All tests in this module are marked @pytest.mark.integration.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from agentry.cli import cli

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Workflow fixture content strings
# ---------------------------------------------------------------------------

_SIMPLE_WORKFLOW_YAML = """\
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

_PR_TOOLS_WORKFLOW_YAML = """\
identity:
  name: pr-review
  version: 1.0.0
  description: A PR review workflow that comments on pull requests.

tools:
  capabilities:
    - pr:comment
    - pr:review
    - repository:read

model:
  provider: anthropic
  model_id: claude-sonnet-4-20250514
  temperature: 0.2
  max_tokens: 2048
  system_prompt: "Review and comment on the PR."

safety:
  trust: elevated
  resources:
    timeout: 120

output:
  schema: {}

composition:
  steps: []
"""

_WRITE_WORKFLOW_YAML = """\
identity:
  name: repo-updater
  version: 1.0.0
  description: A workflow that writes to the repository.

tools:
  capabilities:
    - repository:write
    - shell:execute

model:
  provider: anthropic
  model_id: claude-sonnet-4-20250514
  temperature: 0.2
  max_tokens: 1024
  system_prompt: "Update the repository."

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
# Helpers
# ---------------------------------------------------------------------------


def _run_generate(
    workflow_path: str,
    output_dir: str | None = None,
    triggers: str | None = None,
    schedule: str | None = None,
    dry_run: bool = False,
    target: str = "github",
    extra_args: list[str] | None = None,
) -> "click.testing.Result":
    """Invoke ``agentry ci generate`` and return the CliRunner result."""
    runner = CliRunner(mix_stderr=False)
    args = ["ci", "generate", "--target", target, workflow_path]
    if output_dir is not None:
        args += ["--output-dir", output_dir]
    if triggers is not None:
        args += ["--triggers", triggers]
    if schedule is not None:
        args += ["--schedule", schedule]
    if dry_run:
        args.append("--dry-run")
    if extra_args:
        args.extend(extra_args)
    return runner.invoke(cli, args, catch_exceptions=False)


# ---------------------------------------------------------------------------
# End-to-end: basic generation and YAML validity
# ---------------------------------------------------------------------------


class TestCIGenerateYAMLValidity:
    """Verify that generated output is valid parseable GitHub Actions YAML."""

    def test_dry_run_produces_valid_yaml(self, tmp_path: Path) -> None:
        """--dry-run output must be parseable by yaml.safe_load."""
        workflow_file = tmp_path / "workflow.yaml"
        workflow_file.write_text(_SIMPLE_WORKFLOW_YAML, encoding="utf-8")

        result = _run_generate(str(workflow_file), dry_run=True)
        assert result.exit_code == 0, f"Command failed: {result.output}"

        parsed = yaml.safe_load(result.output)
        assert isinstance(parsed, dict), "Output must be a YAML mapping"

    def test_dry_run_output_has_required_top_level_keys(self, tmp_path: Path) -> None:
        """Generated YAML must have name, on, permissions, and jobs."""
        workflow_file = tmp_path / "workflow.yaml"
        workflow_file.write_text(_SIMPLE_WORKFLOW_YAML, encoding="utf-8")

        result = _run_generate(str(workflow_file), dry_run=True)
        assert result.exit_code == 0

        parsed = yaml.safe_load(result.output)
        for key in ("name", "on", "permissions", "jobs"):
            assert key in parsed, f"Missing required key {key!r} in generated YAML"

    def test_output_file_contains_valid_yaml(self, tmp_path: Path) -> None:
        """When writing to --output-dir, the file must contain valid YAML."""
        workflow_file = tmp_path / "workflow.yaml"
        workflow_file.write_text(_SIMPLE_WORKFLOW_YAML, encoding="utf-8")
        out_dir = tmp_path / "output"

        result = _run_generate(str(workflow_file), output_dir=str(out_dir))
        assert result.exit_code == 0, f"Command failed: {result.output}"

        output_files = list(out_dir.glob("*.yaml"))
        assert len(output_files) == 1, "Expected exactly one output YAML file"

        parsed = yaml.safe_load(output_files[0].read_text(encoding="utf-8"))
        assert isinstance(parsed, dict)
        assert "jobs" in parsed

    def test_output_file_created_in_output_dir(self, tmp_path: Path) -> None:
        """The output file should be created inside --output-dir."""
        workflow_file = tmp_path / "my-workflow.yaml"
        workflow_file.write_text(_SIMPLE_WORKFLOW_YAML, encoding="utf-8")
        out_dir = tmp_path / "ci-output"

        result = _run_generate(str(workflow_file), output_dir=str(out_dir))
        assert result.exit_code == 0

        # Output file should be agentry-{stem}.yaml
        expected_file = out_dir / "agentry-my-workflow.yaml"
        assert expected_file.exists(), (
            f"Expected output file {expected_file} not found. "
            f"Directory contents: {list(out_dir.iterdir())}"
        )


# ---------------------------------------------------------------------------
# End-to-end: run step invokes agentry run with correct arguments
# ---------------------------------------------------------------------------


class TestCIGenerateRunStep:
    """Verify the generated run step calls agentry run correctly."""

    def test_run_step_invokes_agentry_run(self, tmp_path: Path) -> None:
        workflow_file = tmp_path / "workflow.yaml"
        workflow_file.write_text(_SIMPLE_WORKFLOW_YAML, encoding="utf-8")

        result = _run_generate(str(workflow_file), dry_run=True)
        assert result.exit_code == 0

        parsed = yaml.safe_load(result.output)
        steps = parsed["jobs"]["agentry"]["steps"]
        run_steps = [s for s in steps if "run" in s]
        run_commands = [s["run"] for s in run_steps]
        assert any("agentry run" in cmd for cmd in run_commands), (
            f"Expected 'agentry run' in a step's run command. Got: {run_commands!r}"
        )

    def test_run_step_includes_workflow_file_path(self, tmp_path: Path) -> None:
        """The agentry run command must reference the workflow file path."""
        workflow_file = tmp_path / "review.yaml"
        workflow_file.write_text(_SIMPLE_WORKFLOW_YAML, encoding="utf-8")

        result = _run_generate(str(workflow_file), dry_run=True)
        assert result.exit_code == 0

        parsed = yaml.safe_load(result.output)
        steps = parsed["jobs"]["agentry"]["steps"]
        run_steps = [s for s in steps if "run" in s]
        run_commands = [s["run"] for s in run_steps]
        assert any(str(workflow_file) in cmd for cmd in run_commands), (
            f"Expected workflow path in agentry run command. "
            f"workflow_file={workflow_file!r}, run_commands={run_commands!r}"
        )

    def test_run_step_has_github_token_env(self, tmp_path: Path) -> None:
        """The run step must expose GITHUB_TOKEN from secrets."""
        workflow_file = tmp_path / "workflow.yaml"
        workflow_file.write_text(_SIMPLE_WORKFLOW_YAML, encoding="utf-8")

        result = _run_generate(str(workflow_file), dry_run=True)
        assert result.exit_code == 0

        parsed = yaml.safe_load(result.output)
        steps = parsed["jobs"]["agentry"]["steps"]
        # The run step for agentry should have an env block with GITHUB_TOKEN
        agentry_run_steps = [
            s for s in steps
            if "run" in s and "agentry run" in s.get("run", "")
        ]
        assert agentry_run_steps, "Expected an agentry run step"
        env_block = agentry_run_steps[0].get("env", {})
        assert "GITHUB_TOKEN" in env_block, (
            f"Expected GITHUB_TOKEN in run step env. Got: {env_block!r}"
        )

    def test_run_step_has_anthropic_api_key_env(self, tmp_path: Path) -> None:
        """The run step must expose ANTHROPIC_API_KEY from secrets."""
        workflow_file = tmp_path / "workflow.yaml"
        workflow_file.write_text(_SIMPLE_WORKFLOW_YAML, encoding="utf-8")

        result = _run_generate(str(workflow_file), dry_run=True)
        assert result.exit_code == 0

        parsed = yaml.safe_load(result.output)
        steps = parsed["jobs"]["agentry"]["steps"]
        agentry_run_steps = [
            s for s in steps
            if "run" in s and "agentry run" in s.get("run", "")
        ]
        assert agentry_run_steps
        env_block = agentry_run_steps[0].get("env", {})
        assert "ANTHROPIC_API_KEY" in env_block, (
            f"Expected ANTHROPIC_API_KEY in run step env. Got: {env_block!r}"
        )


# ---------------------------------------------------------------------------
# End-to-end: permissions block matches expected values
# ---------------------------------------------------------------------------


class TestCIGeneratePermissions:
    """Verify that the permissions block in generated YAML is correct."""

    def test_no_pr_tools_yields_contents_read_only(self, tmp_path: Path) -> None:
        """Workflow with no PR tools should only have contents: read."""
        workflow_file = tmp_path / "workflow.yaml"
        workflow_file.write_text(_SIMPLE_WORKFLOW_YAML, encoding="utf-8")

        result = _run_generate(str(workflow_file), dry_run=True)
        assert result.exit_code == 0

        parsed = yaml.safe_load(result.output)
        perms = parsed["permissions"]
        assert perms.get("contents") == "read"
        assert "pull-requests" not in perms

    def test_pr_comment_tool_yields_pull_requests_write(self, tmp_path: Path) -> None:
        """Workflow with pr:comment must have pull-requests: write."""
        workflow_file = tmp_path / "pr-workflow.yaml"
        workflow_file.write_text(_PR_TOOLS_WORKFLOW_YAML, encoding="utf-8")

        result = _run_generate(str(workflow_file), dry_run=True)
        assert result.exit_code == 0

        parsed = yaml.safe_load(result.output)
        perms = parsed["permissions"]
        assert perms.get("pull-requests") == "write", (
            f"Expected pull-requests: write. Got: {perms!r}"
        )

    def test_pr_tools_still_include_contents_read(self, tmp_path: Path) -> None:
        """Even with PR tools, contents: read must be present."""
        workflow_file = tmp_path / "pr-workflow.yaml"
        workflow_file.write_text(_PR_TOOLS_WORKFLOW_YAML, encoding="utf-8")

        result = _run_generate(str(workflow_file), dry_run=True)
        assert result.exit_code == 0

        parsed = yaml.safe_load(result.output)
        assert parsed["permissions"].get("contents") == "read"

    def test_repository_write_tool_upgrades_contents_write(
        self, tmp_path: Path
    ) -> None:
        """Workflow with repository:write must have contents: write."""
        workflow_file = tmp_path / "write-workflow.yaml"
        workflow_file.write_text(_WRITE_WORKFLOW_YAML, encoding="utf-8")

        result = _run_generate(str(workflow_file), dry_run=True)
        assert result.exit_code == 0

        parsed = yaml.safe_load(result.output)
        assert parsed["permissions"].get("contents") == "write", (
            f"Expected contents: write for repository:write tool. Got: {parsed['permissions']!r}"
        )


# ---------------------------------------------------------------------------
# End-to-end: trigger configuration
# ---------------------------------------------------------------------------


class TestCIGenerateTriggers:
    """Verify that the on: trigger block is correct in generated YAML."""

    def test_default_trigger_is_pull_request(self, tmp_path: Path) -> None:
        """Without --triggers, default trigger must be pull_request."""
        workflow_file = tmp_path / "workflow.yaml"
        workflow_file.write_text(_SIMPLE_WORKFLOW_YAML, encoding="utf-8")

        result = _run_generate(str(workflow_file), dry_run=True)
        assert result.exit_code == 0

        parsed = yaml.safe_load(result.output)
        assert "pull_request" in parsed["on"], (
            f"Expected pull_request in on: block. Got: {parsed['on']!r}"
        )

    def test_push_trigger_is_set(self, tmp_path: Path) -> None:
        """--triggers push should produce push in on: block."""
        workflow_file = tmp_path / "workflow.yaml"
        workflow_file.write_text(_SIMPLE_WORKFLOW_YAML, encoding="utf-8")

        result = _run_generate(str(workflow_file), dry_run=True, triggers="push")
        assert result.exit_code == 0

        parsed = yaml.safe_load(result.output)
        assert "push" in parsed["on"]

    def test_multiple_triggers_all_present(self, tmp_path: Path) -> None:
        """Multiple triggers via comma-separated --triggers should all appear."""
        workflow_file = tmp_path / "workflow.yaml"
        workflow_file.write_text(_SIMPLE_WORKFLOW_YAML, encoding="utf-8")

        result = _run_generate(
            str(workflow_file), dry_run=True, triggers="pull_request,push"
        )
        assert result.exit_code == 0

        parsed = yaml.safe_load(result.output)
        assert "pull_request" in parsed["on"]
        assert "push" in parsed["on"]

    def test_schedule_trigger_with_cron(self, tmp_path: Path) -> None:
        """schedule trigger with --schedule cron expression appears in on: block."""
        workflow_file = tmp_path / "workflow.yaml"
        workflow_file.write_text(_SIMPLE_WORKFLOW_YAML, encoding="utf-8")

        result = _run_generate(
            str(workflow_file),
            dry_run=True,
            triggers="schedule",
            schedule="0 2 * * 1",
        )
        assert result.exit_code == 0

        parsed = yaml.safe_load(result.output)
        assert "schedule" in parsed["on"]
        schedules = parsed["on"]["schedule"]
        cron_exprs = [entry.get("cron") for entry in schedules]
        assert "0 2 * * 1" in cron_exprs, (
            f"Expected cron '0 2 * * 1'. Got schedules: {schedules!r}"
        )

    def test_issues_trigger_has_types(self, tmp_path: Path) -> None:
        """issues trigger should have opened and edited in types."""
        workflow_file = tmp_path / "workflow.yaml"
        workflow_file.write_text(_SIMPLE_WORKFLOW_YAML, encoding="utf-8")

        result = _run_generate(
            str(workflow_file), dry_run=True, triggers="issues"
        )
        assert result.exit_code == 0

        parsed = yaml.safe_load(result.output)
        assert "issues" in parsed["on"]
        issues_block = parsed["on"]["issues"]
        assert "types" in issues_block
        assert "opened" in issues_block["types"]
        assert "edited" in issues_block["types"]


# ---------------------------------------------------------------------------
# End-to-end: workflow name in generated YAML
# ---------------------------------------------------------------------------


class TestCIGenerateWorkflowName:
    """Verify that the workflow name is embedded correctly in the YAML."""

    def test_generated_name_includes_workflow_identity_name(
        self, tmp_path: Path
    ) -> None:
        """The workflow identity name should appear in the YAML name field."""
        workflow_file = tmp_path / "workflow.yaml"
        workflow_file.write_text(_SIMPLE_WORKFLOW_YAML, encoding="utf-8")

        result = _run_generate(str(workflow_file), dry_run=True)
        assert result.exit_code == 0

        parsed = yaml.safe_load(result.output)
        assert "code-review" in parsed["name"], (
            f"Expected 'code-review' in YAML name. Got: {parsed['name']!r}"
        )

    def test_generated_name_has_agentry_prefix(self, tmp_path: Path) -> None:
        """The YAML name should start with 'Agentry: '."""
        workflow_file = tmp_path / "workflow.yaml"
        workflow_file.write_text(_SIMPLE_WORKFLOW_YAML, encoding="utf-8")

        result = _run_generate(str(workflow_file), dry_run=True)
        assert result.exit_code == 0

        parsed = yaml.safe_load(result.output)
        assert parsed["name"].startswith("Agentry: "), (
            f"Expected name to start with 'Agentry: '. Got: {parsed['name']!r}"
        )
