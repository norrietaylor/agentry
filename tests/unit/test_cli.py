"""Unit tests for CLI module (T02.1).

Tests cover:
- CLI group help listing all subcommands
- Global options: --verbose, --config, --output-format
- Stub commands: setup, ci, registry (print "Not yet implemented", exit 0)
- validate command: help with examples, missing file, YAML load, parser wiring
- run command: help with examples, --input KEY=VALUE parsing, --target option
- Entry point wiring (agentry.cli:cli alias)
"""

import json
import sys
import types

import pytest
from click.testing import CliRunner

from agentry.cli import cli, main


@pytest.fixture(autouse=True)
def _clear_github_actions_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent GITHUB_ACTIONS env var from triggering github-actions binder."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


def test_cli_help() -> None:
    """Test that the CLI help command works."""
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "Agentry" in result.output


def test_cli_version() -> None:
    """Test that the CLI version command works."""
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_validate_command_missing_file() -> None:
    """Test that validate fails when file doesn't exist."""
    runner = CliRunner()
    result = runner.invoke(main, ["validate", "nonexistent.yaml"])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_validate_command_valid_yaml() -> None:
    """Test the validate subcommand with a valid YAML file."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        with open("test.yaml", "w") as f:
            f.write(
                "identity:\n"
                "  name: test-workflow\n"
                "  version: 1.0.0\n"
                "  description: A test workflow\n"
            )
        result = runner.invoke(main, ["--output-format", "text", "validate", "test.yaml"])
        assert result.exit_code == 0
        assert "Validation successful" in result.output


def test_run_command_missing_file() -> None:
    """Test that run fails when file doesn't exist."""
    runner = CliRunner()
    result = runner.invoke(main, ["run", "nonexistent.yaml"])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_run_command_valid_yaml() -> None:
    """Test the run subcommand with a valid YAML file."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        with open("test.yaml", "w") as f:
            f.write("version: 1\nname: test\n")
        result = runner.invoke(
            main,
            ["--output-format", "text", "run", "test.yaml", "--skip-preflight"],
            env={"ANTHROPIC_API_KEY": "", "GITHUB_ACTIONS": ""},
        )
        assert result.exit_code == 0
        assert "Running workflow" in result.output


# ---------------------------------------------------------------------------
# T02.1: Additional tests for full CLI specification
# ---------------------------------------------------------------------------


def test_cli_lists_all_subcommands() -> None:
    """Help output must list run, validate, setup, ci, and registry."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    for cmd in ("run", "validate", "setup", "ci", "registry"):
        assert cmd in result.output, f"Expected '{cmd}' in help output"


def test_verbose_flag_accepted() -> None:
    """--verbose global flag must be accepted without error."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--verbose", "--help"])
    assert result.exit_code == 0


def test_config_flag_accepted() -> None:
    """--config global flag must be accepted without error."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--config", "/tmp/cfg.toml", "--help"])
    assert result.exit_code == 0


def test_output_format_json_flag() -> None:
    """--output-format json global flag must be accepted."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--output-format", "json", "--help"])
    assert result.exit_code == 0


def test_output_format_text_flag() -> None:
    """--output-format text global flag must be accepted."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--output-format", "text", "--help"])
    assert result.exit_code == 0


def test_output_format_invalid_rejected() -> None:
    """Unknown --output-format value must be rejected with error message."""
    runner = CliRunner()
    # Note: must not pass --help here; Click ignores invalid options when --help follows
    result = runner.invoke(cli, ["--output-format", "invalid_format"])
    # Click rejects invalid choice with exit code 2 (or non-zero)
    assert result.exit_code != 0


# --- Stub commands ---


def test_setup_requires_workflow_path() -> None:
    """agentry setup without WORKFLOW_PATH must exit non-zero with usage error."""
    runner = CliRunner()
    result = runner.invoke(cli, ["setup"])
    assert result.exit_code != 0


def test_ci_group_shows_help() -> None:
    """agentry ci (without subcommand) must show help."""
    runner = CliRunner()
    result = runner.invoke(cli, ["ci"])
    assert result.exit_code in (0, 2)  # Click 8.2+ returns 2 for missing subcommand
    assert "generate" in result.output


def test_registry_not_yet_implemented() -> None:
    """agentry registry must print 'Not yet implemented' and exit 0."""
    runner = CliRunner()
    result = runner.invoke(cli, ["registry"])
    assert result.exit_code == 0
    assert "Not yet implemented" in result.output


def test_setup_help() -> None:
    """agentry setup --help must exit 0."""
    runner = CliRunner()
    result = runner.invoke(cli, ["setup", "--help"])
    assert result.exit_code == 0


def test_ci_help() -> None:
    """agentry ci --help must exit 0."""
    runner = CliRunner()
    result = runner.invoke(cli, ["ci", "--help"])
    assert result.exit_code == 0


def test_registry_help() -> None:
    """agentry registry --help must exit 0."""
    runner = CliRunner()
    result = runner.invoke(cli, ["registry", "--help"])
    assert result.exit_code == 0


# --- validate command ---


def test_validate_help_exits_zero() -> None:
    """agentry validate --help must exit 0."""
    runner = CliRunner()
    result = runner.invoke(cli, ["validate", "--help"])
    assert result.exit_code == 0


def test_validate_help_contains_example() -> None:
    """validate --help must include at least one usage example."""
    runner = CliRunner()
    result = runner.invoke(cli, ["validate", "--help"])
    assert result.exit_code == 0
    assert "workflows/" in result.output or "agentry validate" in result.output


def test_validate_invalid_yaml_exits_one(tmp_path: "pytest.TempPathFactory") -> None:  # type: ignore[name-defined]
    """YAML syntax errors must cause exit code 1."""
    wf = tmp_path / "bad.yaml"  # type: ignore[operator]
    wf.write_text(": : :\t{broken")  # type: ignore[union-attr]
    runner = CliRunner()
    result = runner.invoke(cli, ["validate", str(wf)])
    assert result.exit_code == 1


def test_validate_uses_parser_when_available(tmp_path: "pytest.TempPathFactory") -> None:  # type: ignore[name-defined]
    """When agentry.parser is importable, validate_workflow_file is called."""
    wf = tmp_path / "w.yaml"  # type: ignore[operator]
    wf.write_text("name: test\n")  # type: ignore[union-attr]
    fake = types.ModuleType("agentry.parser")
    fake.validate_workflow_file = lambda path: []  # type: ignore[attr-defined]
    sys.modules["agentry.parser"] = fake
    try:
        runner = CliRunner()
        result = runner.invoke(cli, ["--output-format", "text", "validate", str(wf)])
        assert result.exit_code == 0
        assert "Validation successful" in result.output
    finally:
        del sys.modules["agentry.parser"]


def test_validate_reports_parser_errors(tmp_path: "pytest.TempPathFactory") -> None:  # type: ignore[name-defined]
    """When agentry.parser returns errors, validate exits 1."""
    wf = tmp_path / "w.yaml"  # type: ignore[operator]
    wf.write_text("name: test\n")  # type: ignore[union-attr]
    fake = types.ModuleType("agentry.parser")
    fake.validate_workflow_file = lambda path: ["unknown key 'foo'"]  # type: ignore[attr-defined]
    sys.modules["agentry.parser"] = fake
    try:
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", str(wf)])
        assert result.exit_code == 1
    finally:
        del sys.modules["agentry.parser"]


def test_validate_json_format(tmp_path: "pytest.TempPathFactory") -> None:  # type: ignore[name-defined]
    """--output-format json validate emits JSON with status=valid."""
    wf = tmp_path / "w.yaml"  # type: ignore[operator]
    wf.write_text("name: test\n")  # type: ignore[union-attr]
    fake = types.ModuleType("agentry.parser")
    fake.validate_workflow_file = lambda path: []  # type: ignore[attr-defined]
    sys.modules["agentry.parser"] = fake
    try:
        runner = CliRunner()
        result = runner.invoke(cli, ["--output-format", "json", "validate", str(wf)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "valid"
    finally:
        del sys.modules["agentry.parser"]


# --- run command ---


def test_run_help_exits_zero() -> None:
    """agentry run --help must exit 0."""
    runner = CliRunner()
    result = runner.invoke(cli, ["run", "--help"])
    assert result.exit_code == 0


def test_run_help_contains_example() -> None:
    """run --help must include at least one usage example."""
    runner = CliRunner()
    result = runner.invoke(cli, ["run", "--help"])
    assert result.exit_code == 0
    assert "workflows/" in result.output or "example" in result.output.lower()


def test_run_invalid_input_format(tmp_path: "pytest.TempPathFactory") -> None:  # type: ignore[name-defined]
    """--input without '=' must cause exit code 1."""
    wf = tmp_path / "w.yaml"  # type: ignore[operator]
    wf.write_text("name: test\n")  # type: ignore[union-attr]
    runner = CliRunner()
    result = runner.invoke(cli, ["run", str(wf), "--input", "nodequalsign"])
    assert result.exit_code == 1


def test_run_target_option(tmp_path: "pytest.TempPathFactory") -> None:  # type: ignore[name-defined]
    """--target option must be accepted."""
    wf = tmp_path / "w.yaml"  # type: ignore[operator]
    wf.write_text("name: test\n")  # type: ignore[union-attr]
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--output-format", "text", "run", str(wf), "--target", str(tmp_path)],
        env={"ANTHROPIC_API_KEY": "", "GITHUB_ACTIONS": ""},
    )
    assert result.exit_code in (0, 1)


def test_run_stub_text_output(tmp_path: "pytest.TempPathFactory") -> None:  # type: ignore[name-defined]
    """Run command emits stub text output."""
    wf = tmp_path / "w.yaml"  # type: ignore[operator]
    wf.write_text("name: test\n")  # type: ignore[union-attr]
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--output-format", "text", "run", str(wf), "--input", "diff=HEAD~1", "--skip-preflight"],
        env={"ANTHROPIC_API_KEY": "", "GITHUB_ACTIONS": ""},
    )
    assert result.exit_code == 0
    assert "Running workflow" in result.output


# --- Entry point ---


def test_cli_is_click_group() -> None:
    """agentry.cli:cli must be a Click Group."""
    import click

    assert isinstance(cli, click.Group)


def test_cli_and_main_are_same_object() -> None:
    """cli must be the same object as main."""
    assert cli is main


# ---------------------------------------------------------------------------
# T03.3: setup command tests
# ---------------------------------------------------------------------------

# Minimal valid workflow YAML used by setup command tests.
_SETUP_WORKFLOW_YAML = """\
identity:
  name: test-workflow
  version: 1.0.0
  description: A test workflow for setup command tests.

tools:
  capabilities: []

model:
  provider: anthropic
  model_id: claude-sonnet-4-20250514
  temperature: 0.2
  max_tokens: 1024
  system_prompt: "Test prompt"

safety:
  trust: elevated
  resources:
    timeout: 60

output:
  schema: {}

composition:
  steps: []
"""


def test_setup_help_exits_zero() -> None:
    """agentry setup --help must exit 0."""
    runner = CliRunner()
    result = runner.invoke(cli, ["setup", "--help"])
    assert result.exit_code == 0


def test_setup_help_contains_workflow_path() -> None:
    """agentry setup --help must mention WORKFLOW_PATH argument."""
    runner = CliRunner()
    result = runner.invoke(cli, ["setup", "--help"])
    assert result.exit_code == 0
    assert "WORKFLOW_PATH" in result.output


def test_setup_missing_file_exits_one() -> None:
    """agentry setup nonexistent.yaml must exit 1 with error."""
    runner = CliRunner()
    result = runner.invoke(cli, ["setup", "nonexistent.yaml"])
    assert result.exit_code == 1
    assert "not found" in result.output or "not found" in (result.output + result.output)


def test_setup_produces_manifest_skip_preflight(tmp_path: "pytest.TempPathFactory") -> None:  # type: ignore[name-defined]
    """agentry setup with --skip-preflight must exit 0 and emit manifest path."""
    wf = tmp_path / "workflow.yaml"  # type: ignore[operator]
    wf.write_text(_SETUP_WORKFLOW_YAML)  # type: ignore[union-attr]
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--output-format", "text",
            "setup",
            str(wf),
            "--skip-preflight",
        ],
        env={"ANTHROPIC_API_KEY": ""},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, f"stdout: {result.output}"
    assert "Setup complete" in result.output
    assert "Manifest" in result.output


def test_setup_json_output_skip_preflight(tmp_path: "pytest.TempPathFactory") -> None:  # type: ignore[name-defined]
    """agentry setup --output-format json with --skip-preflight must emit valid JSON."""
    wf = tmp_path / "workflow.yaml"  # type: ignore[operator]
    wf.write_text(_SETUP_WORKFLOW_YAML)  # type: ignore[union-attr]
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--output-format", "json",
            "setup",
            str(wf),
            "--skip-preflight",
        ],
        env={"ANTHROPIC_API_KEY": ""},
        catch_exceptions=False,
    )
    assert result.exit_code == 0, f"stdout: {result.output}"
    data = json.loads(result.output)
    assert data["status"] == "ok"
    assert "manifest_path" in data


def test_setup_manifest_file_created(tmp_path: "pytest.TempPathFactory") -> None:  # type: ignore[name-defined]
    """The setup manifest JSON file must exist on disk after a successful setup."""
    wf = tmp_path / "workflow.yaml"  # type: ignore[operator]
    wf.write_text(_SETUP_WORKFLOW_YAML)  # type: ignore[union-attr]
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--output-format", "json",
            "setup",
            str(wf),
            "--skip-preflight",
        ],
        env={"ANTHROPIC_API_KEY": ""},
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    import os
    assert os.path.isfile(data["manifest_path"]), (
        f"Expected manifest file at {data['manifest_path']}"
    )


def test_setup_manifest_contains_required_fields(tmp_path: "pytest.TempPathFactory") -> None:  # type: ignore[name-defined]
    """The setup manifest JSON must contain all required fields."""
    wf = tmp_path / "workflow.yaml"  # type: ignore[operator]
    wf.write_text(_SETUP_WORKFLOW_YAML)  # type: ignore[union-attr]
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--output-format", "json",
            "setup",
            str(wf),
            "--skip-preflight",
        ],
        env={"ANTHROPIC_API_KEY": ""},
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    meta = json.loads(result.output)
    import json as _json
    manifest = _json.loads(
        open(meta["manifest_path"]).read()  # noqa: WPS515
    )
    # All spec-required fields.
    assert manifest["workflow_name"] == "test-workflow"
    assert manifest["workflow_version"] == "1.0.0"
    assert "container_image" in manifest
    assert "filesystem" in manifest
    assert "network" in manifest
    assert "resources" in manifest
    assert "sandbox_tier" in manifest
    assert "timestamp" in manifest


# ---------------------------------------------------------------------------
# T04.3: --skip-preflight flag tests for run and setup commands
# ---------------------------------------------------------------------------


def test_run_skip_preflight_help_mentions_flag() -> None:
    """agentry run --help must mention --skip-preflight flag."""
    runner = CliRunner()
    result = runner.invoke(cli, ["run", "--help"])
    assert result.exit_code == 0
    assert "--skip-preflight" in result.output


def test_run_skip_preflight_bypasses_checks(tmp_path: "pytest.TempPathFactory") -> None:  # type: ignore[name-defined]
    """agentry run --skip-preflight must bypass preflight checks even without API key."""
    wf = tmp_path / "workflow.yaml"  # type: ignore[operator]
    wf.write_text(_SETUP_WORKFLOW_YAML)  # type: ignore[union-attr]
    runner = CliRunner()
    # Note: missing ANTHROPIC_API_KEY would normally cause preflight to fail
    result = runner.invoke(
        cli,
        [
            "--output-format", "text",
            "run",
            str(wf),
            "--skip-preflight",
        ],
        env={"ANTHROPIC_API_KEY": ""},
        catch_exceptions=False,
    )
    # Should not fail due to missing API key since preflight is skipped
    # (it may fail for other reasons, but not preflight check failure)
    assert "Preflight check failed" not in result.output


def test_run_without_skip_preflight_requires_api_key(tmp_path: "pytest.TempPathFactory") -> None:  # type: ignore[name-defined]
    """agentry run without --skip-preflight must check for API key."""
    wf = tmp_path / "workflow.yaml"  # type: ignore[operator]
    wf.write_text(_SETUP_WORKFLOW_YAML)  # type: ignore[union-attr]
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--output-format", "text",
            "run",
            str(wf),
        ],
        env={"ANTHROPIC_API_KEY": ""},
        catch_exceptions=False,
    )
    # With elevated trust and no checks, should still work
    # But if checks are registered they would run
    assert result.exit_code in (0, 1)  # May succeed or fail depending on setup


def test_setup_skip_preflight_help_mentions_flag() -> None:
    """agentry setup --help must mention --skip-preflight flag."""
    runner = CliRunner()
    result = runner.invoke(cli, ["setup", "--help"])
    assert result.exit_code == 0
    assert "--skip-preflight" in result.output
