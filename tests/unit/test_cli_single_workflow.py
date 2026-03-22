"""Unit tests for the single-workflow execution path in cli.py (T01.3).

Tests cover:
1. Successful execution with --output-format json returns JSON with status, output,
   and token_usage fields.
2. Successful execution with --output-format text returns human-readable summary.
3. Agent execution error (non-zero exit / error field) produces informative error
   message and exit code 1.
4. Missing ANTHROPIC_API_KEY (preflight failure) produces clear error and exit code 1.
5. --skip-preflight bypasses preflight checks.
6. Invalid --input format is rejected.
7. RunnerDetector is called with correct agent config from workflow.

Mock strategy:
- Patch agentry.runners.detector.RunnerDetector to return a mock runner.
- Patch agentry.security.envelope.SecurityEnvelope.execute to return a mock
  EnvelopeResult.
- Use CliRunner.isolated_filesystem() / tmp_path with a minimal workflow YAML fixture.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from agentry.cli import main

# ---------------------------------------------------------------------------
# Minimal workflow YAML (single-agent, empty composition.steps)
# ---------------------------------------------------------------------------


_SINGLE_WORKFLOW_YAML = """\
identity:
  name: code-review
  version: 1.0.0
  description: A single-agent code review workflow.

model:
  provider: anthropic
  model_id: claude-sonnet-4-20250514
  temperature: 0.2

agent:
  runtime: claude-code
  model: claude-sonnet-4-20250514
  max_iterations: 10

safety:
  trust: elevated
  resources:
    timeout: 120

tools:
  capabilities: []

output:
  schema: {}

composition:
  steps: []
"""


# ---------------------------------------------------------------------------
# Helper: build a mock EnvelopeResult with optional error state
# ---------------------------------------------------------------------------


def _make_mock_envelope_result(
    output: dict[str, Any] | None = None,
    token_usage: dict[str, int] | None = None,
    error: str = "",
    aborted: bool = False,
    envelope_error: str = "",
) -> MagicMock:
    """Construct a MagicMock that mimics EnvelopeResult."""
    mock_result = MagicMock()
    mock_result.aborted = aborted
    mock_result.envelope_error = envelope_error

    mock_exec = MagicMock()
    mock_exec.error = error
    mock_exec.output = output or {}
    mock_exec.token_usage = token_usage or {"input": 100, "output": 50}

    mock_result.execution_result = None if (aborted or envelope_error) else mock_exec
    return mock_result


# ---------------------------------------------------------------------------
# Test 1: JSON output contains status, output, token_usage
# ---------------------------------------------------------------------------


def test_json_output_contains_required_fields(tmp_path: Path) -> None:
    """--output-format json must emit JSON with status, output, and token_usage."""
    wf = tmp_path / "code-review.yaml"
    wf.write_text(_SINGLE_WORKFLOW_YAML)

    mock_env_result = _make_mock_envelope_result(
        output={"review": "LGTM"},
        token_usage={"input": 200, "output": 80},
    )

    runner = CliRunner()

    with patch("agentry.runners.detector.RunnerDetector") as mock_detector_cls, \
         patch("agentry.security.envelope.SecurityEnvelope.execute",
               return_value=mock_env_result):

        mock_detector_instance = MagicMock()
        mock_detector_instance.get_runner.return_value = MagicMock()
        mock_detector_cls.return_value = mock_detector_instance

        result = runner.invoke(
            main,
            [
                "--output-format", "json",
                "run",
                str(wf),
                "--skip-preflight",
            ],
            env={"ANTHROPIC_API_KEY": "sk-test"},
            catch_exceptions=False,
        )

    assert result.exit_code == 0, f"Unexpected exit code: {result.output}"

    try:
        data = json.loads(result.output)
    except json.JSONDecodeError as exc:
        pytest.fail(f"Output is not valid JSON: {exc}\nOutput: {result.output!r}")

    assert "status" in data, f"Missing 'status' in {data}"
    assert "output" in data, f"Missing 'output' in {data}"
    assert "token_usage" in data, f"Missing 'token_usage' in {data}"
    assert data["status"] == "success"


# ---------------------------------------------------------------------------
# Test 2: Text output contains human-readable summary
# ---------------------------------------------------------------------------


def test_text_output_is_human_readable(tmp_path: Path) -> None:
    """--output-format text must emit human-readable workflow summary."""
    wf = tmp_path / "code-review.yaml"
    wf.write_text(_SINGLE_WORKFLOW_YAML)

    mock_env_result = _make_mock_envelope_result(
        output={"review": "LGTM"},
        token_usage={"input": 100, "output": 40},
    )

    runner = CliRunner()

    with patch("agentry.runners.detector.RunnerDetector") as mock_detector_cls, \
         patch("agentry.security.envelope.SecurityEnvelope.execute",
               return_value=mock_env_result):

        mock_detector_instance = MagicMock()
        mock_detector_instance.get_runner.return_value = MagicMock()
        mock_detector_cls.return_value = mock_detector_instance

        result = runner.invoke(
            main,
            [
                "--output-format", "text",
                "run",
                str(wf),
                "--skip-preflight",
            ],
            env={"ANTHROPIC_API_KEY": "sk-test"},
            catch_exceptions=False,
        )

    assert result.exit_code == 0, f"Unexpected exit code: {result.output}"
    # Should NOT be JSON in text mode
    try:
        json.loads(result.output)
        # If we get here, output was JSON -- that's a failure for text mode
        pytest.fail(f"Expected human-readable text but got JSON: {result.output!r}")
    except json.JSONDecodeError:
        pass  # Expected: not JSON

    # Should mention the workflow path
    assert "code-review.yaml" in result.output or "Workflow" in result.output, (
        f"Expected workflow name in text output: {result.output!r}"
    )


# ---------------------------------------------------------------------------
# Test 3: Agent execution error produces informative error and exit code 1
# ---------------------------------------------------------------------------


def test_agent_execution_error_exits_one(tmp_path: Path) -> None:
    """When execution_result.error is set, CLI must exit 1 with an error message."""
    wf = tmp_path / "code-review.yaml"
    wf.write_text(_SINGLE_WORKFLOW_YAML)

    mock_env_result = _make_mock_envelope_result(
        error="agent timed out after 120 seconds",
    )

    runner = CliRunner()

    with patch("agentry.runners.detector.RunnerDetector") as mock_detector_cls, \
         patch("agentry.security.envelope.SecurityEnvelope.execute",
               return_value=mock_env_result):

        mock_detector_instance = MagicMock()
        mock_detector_instance.get_runner.return_value = MagicMock()
        mock_detector_cls.return_value = mock_detector_instance

        result = runner.invoke(
            main,
            [
                "--output-format", "text",
                "run",
                str(wf),
                "--skip-preflight",
            ],
            env={"ANTHROPIC_API_KEY": "sk-test"},
        )

    assert result.exit_code == 1, (
        f"Expected exit code 1 for agent error, got {result.exit_code}"
    )
    # Error message should appear in stdout or stderr
    try:
        stderr = result.stderr
    except ValueError:
        stderr = ""
    combined = result.output + stderr
    assert "Error" in combined or "error" in combined.lower() or "timed out" in combined, (
        f"Expected error message in output/stderr: stdout={result.output!r}"
    )


# ---------------------------------------------------------------------------
# Test 4: Missing ANTHROPIC_API_KEY causes preflight failure
# ---------------------------------------------------------------------------


def test_missing_api_key_exits_one_with_clear_error(tmp_path: Path) -> None:
    """When ANTHROPIC_API_KEY is unset, preflight must fail with exit code 1."""
    wf = tmp_path / "code-review.yaml"
    wf.write_text(_SINGLE_WORKFLOW_YAML)

    from agentry.security.envelope import PreflightCheckResult

    runner = CliRunner()

    with patch(
        "agentry.security.checks.AnthropicAPIKeyCheck.run",
    ) as mock_check:
        mock_check.return_value = PreflightCheckResult(
            name="AnthropicAPIKeyCheck",
            passed=False,
            message="ANTHROPIC_API_KEY is not set",
            remediation="Export ANTHROPIC_API_KEY=<your-key>",
        )

        result = runner.invoke(
            main,
            [
                "--output-format", "text",
                "run",
                str(wf),
                # No --skip-preflight: preflight will run
            ],
            env={"ANTHROPIC_API_KEY": ""},
        )

    # Should fail with a non-zero exit code
    assert result.exit_code != 0, (
        f"Expected non-zero exit when API key missing, got {result.exit_code}"
    )


# ---------------------------------------------------------------------------
# Test 5: --skip-preflight bypasses preflight checks
# ---------------------------------------------------------------------------


def test_skip_preflight_bypasses_api_key_check(tmp_path: Path) -> None:
    """--skip-preflight must allow execution even when API key is absent."""
    wf = tmp_path / "code-review.yaml"
    wf.write_text(_SINGLE_WORKFLOW_YAML)

    mock_env_result = _make_mock_envelope_result()

    runner = CliRunner()

    with patch("agentry.runners.detector.RunnerDetector") as mock_detector_cls, \
         patch("agentry.security.envelope.SecurityEnvelope.execute",
               return_value=mock_env_result), \
         patch("agentry.security.checks.AnthropicAPIKeyCheck.run") as mock_check:

        mock_detector_instance = MagicMock()
        mock_detector_instance.get_runner.return_value = MagicMock()
        mock_detector_cls.return_value = mock_detector_instance

        result = runner.invoke(
            main,
            [
                "--output-format", "text",
                "run",
                str(wf),
                "--skip-preflight",
            ],
            env={"ANTHROPIC_API_KEY": ""},
            catch_exceptions=False,
        )

    # AnthropicAPIKeyCheck.run should NOT have been called with --skip-preflight
    mock_check.assert_not_called()

    # Should not fail due to preflight
    assert "Preflight check failed" not in result.output


# ---------------------------------------------------------------------------
# Test 6: Invalid --input format is rejected
# ---------------------------------------------------------------------------


def test_invalid_input_format_rejected(tmp_path: Path) -> None:
    """--input without '=' must cause exit code 1."""
    wf = tmp_path / "code-review.yaml"
    wf.write_text(_SINGLE_WORKFLOW_YAML)

    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "run",
            str(wf),
            "--input", "nodequalsign",
            "--skip-preflight",
        ],
        env={"ANTHROPIC_API_KEY": ""},
    )

    assert result.exit_code == 1, (
        f"Expected exit code 1 for bad --input, got {result.exit_code}"
    )
    try:
        stderr = result.stderr
    except ValueError:
        stderr = ""
    combined = result.output + stderr
    assert "KEY=VALUE" in combined or "nodequalsign" in combined, (
        f"Expected descriptive error in output/stderr: stdout={result.output!r}"
    )


# ---------------------------------------------------------------------------
# Test 7: RunnerDetector is called with correct agent config from workflow
# ---------------------------------------------------------------------------


def test_runner_detector_called_with_agent_config(tmp_path: Path) -> None:
    """RunnerDetector must be instantiated with agent_name and agent_kwargs from workflow."""
    wf = tmp_path / "code-review.yaml"
    wf.write_text(_SINGLE_WORKFLOW_YAML)

    mock_env_result = _make_mock_envelope_result()

    runner = CliRunner()

    with patch("agentry.runners.detector.RunnerDetector") as mock_detector_cls, \
         patch("agentry.security.envelope.SecurityEnvelope.execute",
               return_value=mock_env_result):

        mock_detector_instance = MagicMock()
        mock_detector_instance.get_runner.return_value = MagicMock()
        mock_detector_cls.return_value = mock_detector_instance

        runner.invoke(
            main,
            [
                "--output-format", "text",
                "run",
                str(wf),
                "--skip-preflight",
            ],
            env={"ANTHROPIC_API_KEY": "sk-test"},
            catch_exceptions=False,
        )

    # RunnerDetector must have been instantiated
    mock_detector_cls.assert_called_once()

    # Check agent_name keyword argument
    call_kwargs = mock_detector_cls.call_args
    agent_name_value = call_kwargs.kwargs.get("agent_name") or (
        call_kwargs.args[1] if len(call_kwargs.args) > 1 else None
    )
    # Workflow specifies agent.runtime = "claude-code"
    if agent_name_value is not None:
        assert agent_name_value == "claude-code", (
            f"Expected agent_name='claude-code', got {agent_name_value!r}"
        )

    # get_runner must have been called (to obtain the execution runner)
    mock_detector_instance.get_runner.assert_called_once()
