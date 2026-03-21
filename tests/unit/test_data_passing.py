"""Unit tests for T04.3: File-based data passing between composition nodes.

Tests cover:
- Full output reference: Node B input ``{"plan": "A.output"}`` resolves to the
  path of A's result.json after A completes.
- Field extraction: Node B input ``{"severity": "A.output.severity"}`` extracts
  the field from A's JSON output into a temp file.
- Failure object propagation: A fails with skip policy; B's input referencing
  A.output resolves to the NodeFailure JSON path (with ``_failure: true``
  sentinel).
- Missing upstream error: B references C.output but C never executed; raises
  DataPassingError.
- write_node_output: Verifies directory creation and JSON file writing.
- extract_field: Verifies field extraction and missing field error.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentry.composition.data_passing import (
    DataPassingError,
    extract_field,
    resolve_node_inputs,
    write_node_output,
)
from agentry.models.composition import CompositionStep


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_step(
    name: str,
    inputs: dict[str, str] | None = None,
    depends_on: list[str] | None = None,
) -> CompositionStep:
    """Build a minimal CompositionStep for testing."""
    return CompositionStep(
        name=name,
        workflow=f"{name.lower()}.yaml",
        inputs=inputs or {},
        depends_on=depends_on or [],
    )


def _write_json(path: Path, data: dict) -> None:
    """Write *data* as JSON to *path*, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# resolve_node_inputs: full output reference
# ---------------------------------------------------------------------------


class TestResolveFullOutputReference:
    """``A.output`` resolves to the path of A's result.json."""

    def test_resolves_to_absolute_path(self, tmp_path: Path) -> None:
        result_json = tmp_path / "A" / "result.json"
        _write_json(result_json, {"summary": "done"})

        step_b = _make_step("B", inputs={"plan": "A.output"}, depends_on=["A"])
        node_outputs = {"A": result_json}
        resolved = resolve_node_inputs(step_b, node_outputs, {})

        assert Path(resolved["plan"]) == result_json.resolve()

    def test_resolved_path_is_string(self, tmp_path: Path) -> None:
        result_json = tmp_path / "A" / "result.json"
        _write_json(result_json, {"status": "ok"})

        step_b = _make_step("B", inputs={"plan": "A.output"}, depends_on=["A"])
        resolved = resolve_node_inputs(step_b, {"A": result_json}, {})

        assert isinstance(resolved["plan"], str)

    def test_result_json_exists_at_resolved_path(self, tmp_path: Path) -> None:
        result_json = tmp_path / "A" / "result.json"
        _write_json(result_json, {"value": 42})

        step_b = _make_step("B", inputs={"plan": "A.output"}, depends_on=["A"])
        resolved = resolve_node_inputs(step_b, {"A": result_json}, {})

        assert Path(resolved["plan"]).exists()

    def test_empty_inputs_returns_empty_dict(self, tmp_path: Path) -> None:
        step = _make_step("B")
        resolved = resolve_node_inputs(step, {}, {})
        assert resolved == {}

    def test_multiple_output_inputs_resolved(self, tmp_path: Path) -> None:
        result_a = tmp_path / "A" / "result.json"
        result_c = tmp_path / "C" / "result.json"
        _write_json(result_a, {"x": 1})
        _write_json(result_c, {"y": 2})

        step = _make_step(
            "D",
            inputs={"from_a": "A.output", "from_c": "C.output"},
            depends_on=["A", "C"],
        )
        resolved = resolve_node_inputs(step, {"A": result_a, "C": result_c}, {})

        assert Path(resolved["from_a"]) == result_a.resolve()
        assert Path(resolved["from_c"]) == result_c.resolve()


# ---------------------------------------------------------------------------
# resolve_node_inputs: field extraction
# ---------------------------------------------------------------------------


class TestResolveFieldExtraction:
    """``A.output.field`` extracts the named field into a temp file."""

    def test_extracted_value_written_to_temp_file(self, tmp_path: Path) -> None:
        result_json = tmp_path / "A" / "result.json"
        _write_json(result_json, {"severity": "high"})

        step_b = _make_step(
            "B", inputs={"severity": "A.output.severity"}, depends_on=["A"]
        )
        resolved = resolve_node_inputs(step_b, {"A": result_json}, {})

        temp_path = Path(resolved["severity"])
        assert temp_path.exists()
        assert temp_path.read_text() == "high"

    def test_extracted_integer_field(self, tmp_path: Path) -> None:
        result_json = tmp_path / "A" / "result.json"
        _write_json(result_json, {"count": 7})

        step_b = _make_step(
            "B", inputs={"num": "A.output.count"}, depends_on=["A"]
        )
        resolved = resolve_node_inputs(step_b, {"A": result_json}, {})

        temp_path = Path(resolved["num"])
        assert temp_path.read_text() == "7"

    def test_missing_field_raises_data_passing_error(
        self, tmp_path: Path
    ) -> None:
        result_json = tmp_path / "A" / "result.json"
        _write_json(result_json, {"other_field": "value"})

        step_b = _make_step(
            "B", inputs={"missing": "A.output.nonexistent"}, depends_on=["A"]
        )
        with pytest.raises(DataPassingError, match="nonexistent"):
            resolve_node_inputs(step_b, {"A": result_json}, {})


# ---------------------------------------------------------------------------
# resolve_node_inputs: failure object propagation
# ---------------------------------------------------------------------------


class TestResolveFailurePropagation:
    """Skip-policy failure path returned when upstream node failed."""

    def test_returns_failure_json_path(self, tmp_path: Path) -> None:
        failure_json = tmp_path / "A" / "result.json"
        _write_json(
            failure_json,
            {"node_id": "A", "error": "boom", "_failure": True},
        )

        step_b = _make_step("B", inputs={"plan": "A.output"}, depends_on=["A"])
        resolved = resolve_node_inputs(step_b, {}, {"A": failure_json})

        assert Path(resolved["plan"]) == failure_json.resolve()

    def test_failure_json_contains_sentinel(self, tmp_path: Path) -> None:
        failure_json = tmp_path / "A" / "result.json"
        failure_data = {"node_id": "A", "error": "failure!", "_failure": True}
        _write_json(failure_json, failure_data)

        step_b = _make_step("B", inputs={"plan": "A.output"}, depends_on=["A"])
        resolved = resolve_node_inputs(step_b, {}, {"A": failure_json})

        data = json.loads(Path(resolved["plan"]).read_text())
        assert data["_failure"] is True

    def test_failure_path_preferred_over_output_path(
        self, tmp_path: Path
    ) -> None:
        """If a node is in both outputs and failures, failures take priority."""
        output_json = tmp_path / "A-output" / "result.json"
        failure_json = tmp_path / "A-failure" / "result.json"
        _write_json(output_json, {"status": "success"})
        _write_json(failure_json, {"_failure": True, "error": "oops"})

        step_b = _make_step("B", inputs={"plan": "A.output"}, depends_on=["A"])
        # Both maps contain 'A'; failure should win per implementation.
        resolved = resolve_node_inputs(
            step_b, {"A": output_json}, {"A": failure_json}
        )

        assert Path(resolved["plan"]) == failure_json.resolve()


# ---------------------------------------------------------------------------
# resolve_node_inputs: missing upstream error
# ---------------------------------------------------------------------------


class TestResolveMissingUpstream:
    """DataPassingError raised when upstream node has no output or failure."""

    def test_raises_when_upstream_missing(self) -> None:
        step_b = _make_step("B", inputs={"plan": "C.output"}, depends_on=["C"])
        with pytest.raises(DataPassingError, match="C"):
            resolve_node_inputs(step_b, {}, {})

    def test_error_message_includes_node_id(self) -> None:
        step_b = _make_step(
            "B", inputs={"x": "missing_node.output"}, depends_on=["missing_node"]
        )
        with pytest.raises(DataPassingError, match="missing_node"):
            resolve_node_inputs(step_b, {}, {})

    def test_error_message_includes_input_key(self) -> None:
        step_b = _make_step(
            "B", inputs={"my_key": "X.output"}, depends_on=["X"]
        )
        with pytest.raises(DataPassingError, match="my_key"):
            resolve_node_inputs(step_b, {}, {})


# ---------------------------------------------------------------------------
# write_node_output
# ---------------------------------------------------------------------------


class TestWriteNodeOutput:
    """write_node_output creates directory and writes JSON file."""

    def test_creates_node_directory(self, tmp_path: Path) -> None:
        write_node_output("my-node", {"result": "ok"}, tmp_path)
        assert (tmp_path / "my-node").is_dir()

    def test_creates_result_json(self, tmp_path: Path) -> None:
        write_node_output("node-a", {"value": 1}, tmp_path)
        assert (tmp_path / "node-a" / "result.json").exists()

    def test_writes_correct_json_content(self, tmp_path: Path) -> None:
        data = {"summary": "done", "count": 3}
        write_node_output("node-b", data, tmp_path)

        content = json.loads((tmp_path / "node-b" / "result.json").read_text())
        assert content == data

    def test_returns_absolute_path(self, tmp_path: Path) -> None:
        result_path = write_node_output("node-c", {}, tmp_path)
        assert result_path.is_absolute()

    def test_returns_path_to_result_json(self, tmp_path: Path) -> None:
        result_path = write_node_output("node-d", {"x": 1}, tmp_path)
        assert result_path.name == "result.json"

    def test_creates_parent_directories_if_missing(self, tmp_path: Path) -> None:
        """write_node_output should create intermediate directories."""
        deep_run_dir = tmp_path / "deep" / "nested" / "run"
        write_node_output("node-e", {}, deep_run_dir)
        assert (deep_run_dir / "node-e" / "result.json").exists()


# ---------------------------------------------------------------------------
# extract_field
# ---------------------------------------------------------------------------


class TestExtractField:
    """extract_field reads a field from a JSON file."""

    def test_extracts_string_field(self, tmp_path: Path) -> None:
        json_path = tmp_path / "result.json"
        _write_json(json_path, {"severity": "critical"})
        assert extract_field(json_path, "severity") == "critical"

    def test_extracts_integer_field(self, tmp_path: Path) -> None:
        json_path = tmp_path / "result.json"
        _write_json(json_path, {"count": 42})
        assert extract_field(json_path, "count") == 42

    def test_extracts_list_field(self, tmp_path: Path) -> None:
        json_path = tmp_path / "result.json"
        _write_json(json_path, {"items": [1, 2, 3]})
        assert extract_field(json_path, "items") == [1, 2, 3]

    def test_extracts_nested_dict_field(self, tmp_path: Path) -> None:
        json_path = tmp_path / "result.json"
        _write_json(json_path, {"meta": {"key": "value"}})
        assert extract_field(json_path, "meta") == {"key": "value"}

    def test_raises_for_missing_field(self, tmp_path: Path) -> None:
        json_path = tmp_path / "result.json"
        _write_json(json_path, {"other": "value"})
        with pytest.raises(DataPassingError, match="missing_key"):
            extract_field(json_path, "missing_key")

    def test_raises_for_missing_file(self, tmp_path: Path) -> None:
        json_path = tmp_path / "nonexistent.json"
        with pytest.raises(DataPassingError):
            extract_field(json_path, "any_field")

    def test_raises_for_invalid_json(self, tmp_path: Path) -> None:
        json_path = tmp_path / "bad.json"
        json_path.write_text("not valid json")
        with pytest.raises(DataPassingError):
            extract_field(json_path, "field")

    def test_raises_for_non_object_json(self, tmp_path: Path) -> None:
        """JSON arrays at the top level should raise DataPassingError."""
        json_path = tmp_path / "list.json"
        json_path.write_text("[1, 2, 3]")
        with pytest.raises(DataPassingError):
            extract_field(json_path, "field")

    def test_error_message_includes_field_name(self, tmp_path: Path) -> None:
        json_path = tmp_path / "result.json"
        _write_json(json_path, {"other": "x"})
        with pytest.raises(DataPassingError, match="my_field"):
            extract_field(json_path, "my_field")
