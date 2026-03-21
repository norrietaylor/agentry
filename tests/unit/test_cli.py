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

import pytest  # noqa: F401
from click.testing import CliRunner

from agentry.cli import cli, main


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
            f.write("version: 1\nname: test\n")
        result = runner.invoke(main, ["validate", "test.yaml"])
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
    runner = CliRunner(mix_stderr=False)
    with runner.isolated_filesystem():
        with open("test.yaml", "w") as f:
            f.write("version: 1\nname: test\n")
        result = runner.invoke(main, ["--output-format", "text", "run", "test.yaml"])
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


def test_setup_not_yet_implemented() -> None:
    """agentry setup must print 'Not yet implemented' and exit 0."""
    runner = CliRunner()
    result = runner.invoke(cli, ["setup"])
    assert result.exit_code == 0
    assert "Not yet implemented" in result.output


def test_ci_not_yet_implemented() -> None:
    """agentry ci must print 'Not yet implemented' and exit 0."""
    runner = CliRunner()
    result = runner.invoke(cli, ["ci"])
    assert result.exit_code == 0
    assert "Not yet implemented" in result.output


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
    sys.modules.pop("agentry.executor", None)
    runner = CliRunner()
    result = runner.invoke(cli, ["--output-format", "text", "run", str(wf), "--target", str(tmp_path)])
    assert result.exit_code in (0, 1)


def test_run_stub_json_output(tmp_path: "pytest.TempPathFactory") -> None:  # type: ignore[name-defined]
    """Without executor, --output-format json emits valid JSON."""
    wf = tmp_path / "w.yaml"  # type: ignore[operator]
    wf.write_text("name: test\n")  # type: ignore[union-attr]
    sys.modules.pop("agentry.executor", None)
    runner = CliRunner()
    result = runner.invoke(cli, ["--output-format", "json", "run", str(wf), "--input", "diff=HEAD~1"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "workflow" in data or "status" in data


# --- Entry point ---


def test_cli_is_click_group() -> None:
    """agentry.cli:cli must be a Click Group."""
    import click

    assert isinstance(cli, click.Group)


def test_cli_and_main_are_same_object() -> None:
    """cli must be the same object as main."""
    assert cli is main
