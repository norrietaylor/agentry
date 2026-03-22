"""Unit tests for agentry ci generate CLI command (T04.1).

Tests cover:
- ci group: --help, subcommand listing
- ci generate: --help, argument/option validation
- ci generate: --target validation (only 'github' supported)
- ci generate: --triggers parsing and validation
- ci generate: --schedule required when 'schedule' in triggers
- ci generate: workflow file not found
- ci generate: composed workflow rejection
- ci generate: valid simple workflow scaffold
- ci generate: --dry-run flag
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from agentry.cli import cli


# ---------------------------------------------------------------------------
# Minimal valid workflow YAML (no composition)
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

# Workflow YAML with non-empty composition.steps (should be rejected)
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
# ci group tests
# ---------------------------------------------------------------------------


def test_ci_help_exits_zero() -> None:
    """agentry ci --help must exit 0."""
    runner = CliRunner()
    result = runner.invoke(cli, ["ci", "--help"])
    assert result.exit_code == 0


def test_ci_help_lists_generate() -> None:
    """agentry ci --help must list 'generate' subcommand."""
    runner = CliRunner()
    result = runner.invoke(cli, ["ci", "--help"])
    assert result.exit_code == 0
    assert "generate" in result.output


def test_ci_invoked_without_subcommand_shows_help() -> None:
    """agentry ci (no subcommand) must display help."""
    runner = CliRunner()
    result = runner.invoke(cli, ["ci"])
    assert result.exit_code in (0, 2)  # Click 8.2+ returns 2 for missing subcommand
    assert "generate" in result.output


# ---------------------------------------------------------------------------
# ci generate --help
# ---------------------------------------------------------------------------


def test_ci_generate_help_exits_zero() -> None:
    """agentry ci generate --help must exit 0."""
    runner = CliRunner()
    result = runner.invoke(cli, ["ci", "generate", "--help"])
    assert result.exit_code == 0


def test_ci_generate_help_mentions_workflow_path() -> None:
    """agentry ci generate --help must mention WORKFLOW_PATH argument."""
    runner = CliRunner()
    result = runner.invoke(cli, ["ci", "generate", "--help"])
    assert result.exit_code == 0
    assert "WORKFLOW_PATH" in result.output


def test_ci_generate_help_mentions_target() -> None:
    """agentry ci generate --help must mention --target option."""
    runner = CliRunner()
    result = runner.invoke(cli, ["ci", "generate", "--help"])
    assert result.exit_code == 0
    assert "--target" in result.output


def test_ci_generate_help_mentions_triggers() -> None:
    """agentry ci generate --help must mention --triggers option."""
    runner = CliRunner()
    result = runner.invoke(cli, ["ci", "generate", "--help"])
    assert result.exit_code == 0
    assert "--triggers" in result.output


def test_ci_generate_help_mentions_schedule() -> None:
    """agentry ci generate --help must mention --schedule option."""
    runner = CliRunner()
    result = runner.invoke(cli, ["ci", "generate", "--help"])
    assert result.exit_code == 0
    assert "--schedule" in result.output


def test_ci_generate_help_mentions_output_dir() -> None:
    """agentry ci generate --help must mention --output-dir option."""
    runner = CliRunner()
    result = runner.invoke(cli, ["ci", "generate", "--help"])
    assert result.exit_code == 0
    assert "--output-dir" in result.output


def test_ci_generate_help_mentions_dry_run() -> None:
    """agentry ci generate --help must mention --dry-run flag."""
    runner = CliRunner()
    result = runner.invoke(cli, ["ci", "generate", "--help"])
    assert result.exit_code == 0
    assert "--dry-run" in result.output


# ---------------------------------------------------------------------------
# --target validation
# ---------------------------------------------------------------------------


def test_ci_generate_unsupported_target_exits_one(tmp_path: pytest.TempPathFactory) -> None:
    """agentry ci generate --target bitbucket must exit 1 with clear error."""
    wf = tmp_path / "workflow.yaml"  # type: ignore[operator]
    wf.write_text(_SIMPLE_WORKFLOW_YAML)  # type: ignore[union-attr]
    runner = CliRunner()
    result = runner.invoke(cli, ["ci", "generate", "--target", "bitbucket", str(wf)])
    assert result.exit_code == 1
    assert "bitbucket" in result.output or "bitbucket" in (result.output + str(result.exception))
    # Error should mention only 'github' is supported
    assert "github" in result.output.lower()


def test_ci_generate_target_required() -> None:
    """agentry ci generate without --target must exit non-zero."""
    runner = CliRunner()
    result = runner.invoke(cli, ["ci", "generate", "workflow.yaml"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# --triggers validation
# ---------------------------------------------------------------------------


def test_ci_generate_invalid_trigger_exits_one(tmp_path: pytest.TempPathFactory) -> None:
    """agentry ci generate with an invalid trigger must exit 1 with clear error."""
    wf = tmp_path / "workflow.yaml"  # type: ignore[operator]
    wf.write_text(_SIMPLE_WORKFLOW_YAML)  # type: ignore[union-attr]
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["ci", "generate", "--target", "github", "--triggers", "deploy", str(wf)],
    )
    assert result.exit_code == 1
    assert "deploy" in result.output


def test_ci_generate_multiple_triggers_valid(tmp_path: pytest.TempPathFactory) -> None:
    """agentry ci generate with comma-separated valid triggers must not error on triggers."""
    wf = tmp_path / "workflow.yaml"  # type: ignore[operator]
    wf.write_text(_SIMPLE_WORKFLOW_YAML)  # type: ignore[union-attr]
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["ci", "generate", "--target", "github", "--triggers", "pull_request,push", str(wf)],
    )
    # Should not fail due to trigger validation
    assert "unsupported trigger" not in result.output


# ---------------------------------------------------------------------------
# --schedule requirement
# ---------------------------------------------------------------------------


def test_ci_generate_schedule_trigger_without_schedule_flag_exits_one(
    tmp_path: pytest.TempPathFactory,
) -> None:
    """agentry ci generate --triggers schedule without --schedule must exit 1."""
    wf = tmp_path / "workflow.yaml"  # type: ignore[operator]
    wf.write_text(_SIMPLE_WORKFLOW_YAML)  # type: ignore[union-attr]
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["ci", "generate", "--target", "github", "--triggers", "schedule", str(wf)],
    )
    assert result.exit_code == 1
    assert "--schedule" in result.output or "schedule" in result.output


def test_ci_generate_schedule_trigger_with_schedule_flag_passes_validation(
    tmp_path: pytest.TempPathFactory,
) -> None:
    """agentry ci generate --triggers schedule --schedule CRON must pass schedule validation."""
    wf = tmp_path / "workflow.yaml"  # type: ignore[operator]
    wf.write_text(_SIMPLE_WORKFLOW_YAML)  # type: ignore[union-attr]
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
            "0 2 * * 1",
            str(wf),
        ],
    )
    # Should not fail due to schedule validation
    assert "--schedule is required" not in result.output


# ---------------------------------------------------------------------------
# Workflow file not found
# ---------------------------------------------------------------------------


def test_ci_generate_missing_workflow_exits_one() -> None:
    """agentry ci generate with nonexistent workflow file must exit 1."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["ci", "generate", "--target", "github", "nonexistent.yaml"],
    )
    assert result.exit_code == 1
    assert "not found" in result.output


# ---------------------------------------------------------------------------
# Composed workflow rejection
# ---------------------------------------------------------------------------


def test_ci_generate_composed_workflow_rejected(tmp_path: pytest.TempPathFactory) -> None:
    """agentry ci generate must reject composed workflows with clear error message."""
    wf = tmp_path / "composed.yaml"  # type: ignore[operator]
    wf.write_text(_COMPOSED_WORKFLOW_YAML)  # type: ignore[union-attr]
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["ci", "generate", "--target", "github", str(wf)],
    )
    assert result.exit_code == 1
    assert "Composed workflow CI generation is not yet supported" in result.output
    assert "Generate CI config for each component workflow individually" in result.output


# ---------------------------------------------------------------------------
# Valid workflow scaffolding
# ---------------------------------------------------------------------------


def test_ci_generate_valid_workflow_exits_zero(tmp_path: pytest.TempPathFactory) -> None:
    """agentry ci generate with valid simple workflow must exit 0."""
    wf = tmp_path / "workflow.yaml"  # type: ignore[operator]
    wf.write_text(_SIMPLE_WORKFLOW_YAML)  # type: ignore[union-attr]
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["ci", "generate", "--target", "github", str(wf)],
    )
    assert result.exit_code == 0


def test_ci_generate_dry_run_exits_zero(tmp_path: pytest.TempPathFactory) -> None:
    """agentry ci generate --dry-run with valid simple workflow must exit 0."""
    wf = tmp_path / "workflow.yaml"  # type: ignore[operator]
    wf.write_text(_SIMPLE_WORKFLOW_YAML)  # type: ignore[union-attr]
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["ci", "generate", "--target", "github", "--dry-run", str(wf)],
    )
    assert result.exit_code == 0


def test_ci_generate_dry_run_mentions_workflow(tmp_path: pytest.TempPathFactory) -> None:
    """agentry ci generate --dry-run output must reference the workflow."""
    wf = tmp_path / "workflow.yaml"  # type: ignore[operator]
    wf.write_text(_SIMPLE_WORKFLOW_YAML)  # type: ignore[union-attr]
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["ci", "generate", "--target", "github", "--dry-run", str(wf)],
    )
    assert result.exit_code == 0
    # Output should reference the workflow path or workflow name
    assert "workflow" in result.output.lower() or str(wf) in result.output
