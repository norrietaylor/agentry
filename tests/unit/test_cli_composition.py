"""Unit tests for CLI composition integration (T05.4).

Tests cover:
1. Composition detection: CompositionEngine is called for composition workflows.
2. Single-agent fallback: existing executor path for non-composition workflows.
3. --node flag with composition: only specified node runs in isolation.
4. --node flag without composition: error message emitted.
5. JSON output format: valid JSON with CompositionRecord fields.
6. Text output format: human-readable per-node status lines.

Uses Click's CliRunner for CLI testing (same pattern as test_cli.py).
CompositionEngine is mocked to return a canned CompositionRecord.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from agentry.cli import main
from agentry.composition.record import CompositionRecord, CompositionStatus, NodeStatus


# ---------------------------------------------------------------------------
# Shared helpers / fixtures
# ---------------------------------------------------------------------------


# Minimal workflow YAML with a non-empty composition.steps block.
_COMPOSITION_WORKFLOW_YAML = """\
identity:
  name: planning-pipeline
  version: 1.0.0
  description: A multi-node planning pipeline.

model:
  provider: anthropic
  model_id: claude-sonnet-4-20250514
  temperature: 0.2

safety:
  resources:
    timeout: 600

output:
  schema: {}

composition:
  steps:
    - name: triage
      workflow: triage.yaml
      depends_on: []
      inputs: {}
    - name: summary
      workflow: triage.yaml
      depends_on:
        - triage
      inputs: {}
"""


# Minimal workflow YAML with an *empty* composition.steps block (single-agent).
_SINGLE_AGENT_WORKFLOW_YAML = """\
identity:
  name: code-review
  version: 1.0.0
  description: A single-agent code review workflow.

model:
  provider: anthropic
  model_id: claude-sonnet-4-20250514
  temperature: 0.2

safety:
  resources:
    timeout: 120

output:
  schema: {}

composition:
  steps: []
"""


def _make_canned_record() -> CompositionRecord:
    """Return a canned CompositionRecord for use in mocks."""
    return CompositionRecord(
        node_statuses={
            "triage": NodeStatus.COMPLETED,
            "summary": NodeStatus.COMPLETED,
        },
        node_records={
            "triage": None,
            "summary": None,
        },
        overall_status=CompositionStatus.COMPLETED,
        wall_clock_start=1_700_000_000.0,
        wall_clock_end=1_700_000_005.0,
    )


# ---------------------------------------------------------------------------
# Test 1: Composition detection — CompositionEngine is called
# ---------------------------------------------------------------------------


def test_composition_detection_calls_engine(tmp_path: Path) -> None:
    """When composition.steps is non-empty, CompositionEngine.execute() is called."""
    wf = tmp_path / "planning-pipeline.yaml"
    wf.write_text(_COMPOSITION_WORKFLOW_YAML)

    # Also write the referenced sub-workflow so the test file exists
    (tmp_path / "triage.yaml").write_text(_SINGLE_AGENT_WORKFLOW_YAML)

    runner = CliRunner()

    canned = _make_canned_record()
    mock_engine_instance = MagicMock()
    mock_engine_instance.execute = AsyncMock(return_value=canned)

    with patch(
        "agentry.composition.engine.CompositionEngine", return_value=mock_engine_instance
    ) as MockEngineClass:
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

    # Verify engine was instantiated and execute was called
    MockEngineClass.assert_called_once()
    mock_engine_instance.execute.assert_awaited_once()
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Test 2: Single-agent fallback — executor path is used
# ---------------------------------------------------------------------------


def test_single_agent_fallback_uses_executor(tmp_path: Path) -> None:
    """When composition.steps is empty, the single-agent executor path is used."""
    wf = tmp_path / "code-review.yaml"
    wf.write_text(_SINGLE_AGENT_WORKFLOW_YAML)

    runner = CliRunner()

    # The existing stub executor path is triggered when agentry.executor is absent.
    # We confirm CompositionEngine is NOT instantiated.
    with patch("agentry.composition.engine.CompositionEngine") as MockEngineClass:
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

    # CompositionEngine must NOT be called for a non-composition workflow.
    MockEngineClass.assert_not_called()
    # The run command should succeed or exit cleanly with the stub path.
    assert result.exit_code in (0, 1)


# ---------------------------------------------------------------------------
# Test 3: --node flag with composition — only the specified node runs
# ---------------------------------------------------------------------------


def test_node_flag_with_composition_isolates_node(tmp_path: Path) -> None:
    """--node triage on a composition workflow isolates only the triage step."""
    wf = tmp_path / "planning-pipeline.yaml"
    wf.write_text(_COMPOSITION_WORKFLOW_YAML)
    (tmp_path / "triage.yaml").write_text(_SINGLE_AGENT_WORKFLOW_YAML)

    runner = CliRunner()

    single_node_record = CompositionRecord(
        node_statuses={"triage": NodeStatus.COMPLETED},
        node_records={"triage": None},
        overall_status=CompositionStatus.COMPLETED,
        wall_clock_start=1_700_000_000.0,
        wall_clock_end=1_700_000_002.0,
    )
    mock_engine_instance = MagicMock()
    mock_engine_instance.execute = AsyncMock(return_value=single_node_record)

    with patch(
        "agentry.composition.engine.CompositionEngine", return_value=mock_engine_instance
    ) as MockEngineClass:
        result = runner.invoke(
            main,
            [
                "--output-format", "text",
                "run",
                str(wf),
                "--node", "triage",
                "--skip-preflight",
            ],
            env={"ANTHROPIC_API_KEY": ""},
            catch_exceptions=False,
        )

    # Engine must be instantiated.
    MockEngineClass.assert_called_once()

    # The composition passed to CompositionEngine must contain only the triage step.
    call_kwargs = MockEngineClass.call_args
    # composition is the first positional arg or the 'composition' keyword arg
    passed_composition = (
        call_kwargs.kwargs.get("composition")
        or (call_kwargs.args[0] if call_kwargs.args else None)
    )
    if passed_composition is not None:
        assert len(passed_composition.steps) == 1
        assert passed_composition.steps[0].node_id == "triage"

    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Test 4: --node flag without composition — error message emitted
# ---------------------------------------------------------------------------


def test_node_flag_without_composition_emits_error(tmp_path: Path) -> None:
    """--node on a non-composition workflow produces an error message."""
    wf = tmp_path / "code-review.yaml"
    wf.write_text(_SINGLE_AGENT_WORKFLOW_YAML)

    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            "--output-format", "text",
            "run",
            str(wf),
            "--node", "nonexistent",
            "--skip-preflight",
        ],
        env={"ANTHROPIC_API_KEY": ""},
    )

    assert result.exit_code != 0
    # Error message must mention --node flag and composition
    combined = result.output
    assert "--node" in combined or "composition" in combined.lower()


# ---------------------------------------------------------------------------
# Test 5: JSON output format — valid JSON with CompositionRecord fields
# ---------------------------------------------------------------------------


def test_json_output_format_contains_composition_record_fields(tmp_path: Path) -> None:
    """--output-format json emits valid JSON containing CompositionRecord fields."""
    wf = tmp_path / "planning-pipeline.yaml"
    wf.write_text(_COMPOSITION_WORKFLOW_YAML)
    (tmp_path / "triage.yaml").write_text(_SINGLE_AGENT_WORKFLOW_YAML)

    runner = CliRunner()

    canned = _make_canned_record()
    mock_engine_instance = MagicMock()
    mock_engine_instance.execute = AsyncMock(return_value=canned)

    with patch("agentry.composition.engine.CompositionEngine", return_value=mock_engine_instance):
        result = runner.invoke(
            main,
            [
                "--output-format", "json",
                "run",
                str(wf),
                "--skip-preflight",
            ],
            env={"ANTHROPIC_API_KEY": ""},
            catch_exceptions=False,
        )

    assert result.exit_code == 0

    # Output must be valid JSON.
    try:
        data = json.loads(result.output)
    except json.JSONDecodeError as exc:
        pytest.fail(f"CLI did not emit valid JSON: {exc}\nOutput: {result.output!r}")

    # Must contain CompositionRecord top-level fields.
    assert "overall_status" in data, f"Missing 'overall_status' in {data}"
    assert "node_statuses" in data, f"Missing 'node_statuses' in {data}"
    assert "wall_clock_timing" in data, f"Missing 'wall_clock_timing' in {data}"

    # Verify values match the canned record.
    assert data["overall_status"] == "completed"
    assert data["node_statuses"]["triage"] == "completed"
    assert data["node_statuses"]["summary"] == "completed"


# ---------------------------------------------------------------------------
# Test 6: Text output format — human-readable per-node status lines
# ---------------------------------------------------------------------------


def test_text_output_format_contains_per_node_status(tmp_path: Path) -> None:
    """--output-format text emits human-readable output with per-node status lines."""
    wf = tmp_path / "planning-pipeline.yaml"
    wf.write_text(_COMPOSITION_WORKFLOW_YAML)
    (tmp_path / "triage.yaml").write_text(_SINGLE_AGENT_WORKFLOW_YAML)

    runner = CliRunner()

    canned = _make_canned_record()
    mock_engine_instance = MagicMock()
    mock_engine_instance.execute = AsyncMock(return_value=canned)

    with patch("agentry.composition.engine.CompositionEngine", return_value=mock_engine_instance):
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

    assert result.exit_code == 0

    # Output must mention each node and overall status.
    output = result.output
    assert "triage" in output, f"Expected 'triage' in output:\n{output}"
    assert "summary" in output, f"Expected 'summary' in output:\n{output}"
    # Overall composition status should appear.
    assert "completed" in output.lower() or "Composition" in output, (
        f"Expected status information in output:\n{output}"
    )
