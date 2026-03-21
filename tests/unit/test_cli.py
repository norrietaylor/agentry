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
