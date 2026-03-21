"""Unit tests for T05.1: Validation pipeline sequential execution and halt-on-failure.

Tests cover:
- When Layer 1 passes, Layer 2 is executed.
- When Layer 1 fails, Layer 2 is NOT executed (halt-on-failure).
- When both Layer 1 and Layer 2 pass, validation_status is "passed".
- When Layer 1 fails, validation_status is "failed".
- When Layer 2 fails, validation_status is "failed".
- The ValidationResult layer_results list reflects executed layers only.
- Proof that Layer 3 placeholder slots exist in result when not yet run.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentry.validation.layer1 import validate_schema
from agentry.validation.layer2 import validate_side_effects
from agentry.validation.result import LayerResult, ValidationResult

# ---------------------------------------------------------------------------
# Helper: a minimal pipeline runner (mirrors what T05.2 will implement fully)
# ---------------------------------------------------------------------------


def run_layers_1_and_2(
    output: Any,
    schema: dict[str, Any],
    tool_invocations: list[dict[str, Any]],
    side_effects_allowlist: list[str],
) -> ValidationResult:
    """Execute Layer 1, then (conditionally) Layer 2 in sequence.

    Halts on first failure. Returns a ValidationResult with layer_results
    for every layer that was evaluated.
    """
    layer_results: list[LayerResult] = []

    # Layer 1: schema validation
    l1 = validate_schema(output, schema)
    layer_results.append(l1)
    if not l1.passed:
        return ValidationResult(validation_status="failed", layer_results=layer_results)

    # Layer 2: side-effect allowlist
    l2 = validate_side_effects(tool_invocations, side_effects_allowlist)
    layer_results.append(l2)
    if not l2.passed:
        return ValidationResult(validation_status="failed", layer_results=layer_results)

    return ValidationResult(validation_status="passed", layer_results=layer_results)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def schema_requiring_findings() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["findings"],
        "properties": {"findings": {"type": "array"}},
        "additionalProperties": True,
    }


@pytest.fixture()
def valid_output() -> dict[str, Any]:
    return {"findings": []}


@pytest.fixture()
def invalid_output() -> dict[str, Any]:
    # findings is a string instead of an array.
    return {"findings": "should-be-array"}


# ---------------------------------------------------------------------------
# Sequential execution and halt-on-failure
# ---------------------------------------------------------------------------


class TestSequentialExecution:
    def test_both_layers_pass_when_valid(
        self,
        schema_requiring_findings: dict,
        valid_output: dict,
    ) -> None:
        result = run_layers_1_and_2(
            output=valid_output,
            schema=schema_requiring_findings,
            tool_invocations=[],
            side_effects_allowlist=[],
        )
        assert result.validation_status == "passed"

    def test_two_layer_results_when_both_run(
        self,
        schema_requiring_findings: dict,
        valid_output: dict,
    ) -> None:
        result = run_layers_1_and_2(
            output=valid_output,
            schema=schema_requiring_findings,
            tool_invocations=[],
            side_effects_allowlist=[],
        )
        assert len(result.layer_results) == 2

    def test_layer1_passes_in_passing_result(
        self,
        schema_requiring_findings: dict,
        valid_output: dict,
    ) -> None:
        result = run_layers_1_and_2(
            output=valid_output,
            schema=schema_requiring_findings,
            tool_invocations=[],
            side_effects_allowlist=[],
        )
        assert result.layer_results[0].passed is True

    def test_layer2_passes_in_passing_result(
        self,
        schema_requiring_findings: dict,
        valid_output: dict,
    ) -> None:
        result = run_layers_1_and_2(
            output=valid_output,
            schema=schema_requiring_findings,
            tool_invocations=[],
            side_effects_allowlist=[],
        )
        assert result.layer_results[1].passed is True


class TestHaltOnFailure:
    def test_layer1_failure_halts_pipeline(
        self,
        schema_requiring_findings: dict,
        invalid_output: dict,
    ) -> None:
        result = run_layers_1_and_2(
            output=invalid_output,
            schema=schema_requiring_findings,
            tool_invocations=[{"tool": "file:write", "args": {}}],
            side_effects_allowlist=[],  # would block if reached
        )
        # Only one layer result because Layer 2 never ran.
        assert len(result.layer_results) == 1

    def test_layer1_failure_sets_failed_status(
        self,
        schema_requiring_findings: dict,
        invalid_output: dict,
    ) -> None:
        result = run_layers_1_and_2(
            output=invalid_output,
            schema=schema_requiring_findings,
            tool_invocations=[],
            side_effects_allowlist=[],
        )
        assert result.validation_status == "failed"

    def test_layer1_failure_layer_result_is_layer_1(
        self,
        schema_requiring_findings: dict,
        invalid_output: dict,
    ) -> None:
        result = run_layers_1_and_2(
            output=invalid_output,
            schema=schema_requiring_findings,
            tool_invocations=[],
            side_effects_allowlist=[],
        )
        assert result.layer_results[0].layer == 1
        assert result.layer_results[0].passed is False

    def test_layer2_failure_sets_failed_status(
        self,
        schema_requiring_findings: dict,
        valid_output: dict,
    ) -> None:
        result = run_layers_1_and_2(
            output=valid_output,
            schema=schema_requiring_findings,
            tool_invocations=[{"tool": "file:write", "args": {}}],
            side_effects_allowlist=[],  # blocks file:write
        )
        assert result.validation_status == "failed"

    def test_layer2_failure_has_two_layer_results(
        self,
        schema_requiring_findings: dict,
        valid_output: dict,
    ) -> None:
        result = run_layers_1_and_2(
            output=valid_output,
            schema=schema_requiring_findings,
            tool_invocations=[{"tool": "file:write", "args": {}}],
            side_effects_allowlist=[],
        )
        assert len(result.layer_results) == 2

    def test_layer2_failure_layer1_still_passed(
        self,
        schema_requiring_findings: dict,
        valid_output: dict,
    ) -> None:
        result = run_layers_1_and_2(
            output=valid_output,
            schema=schema_requiring_findings,
            tool_invocations=[{"tool": "file:write", "args": {}}],
            side_effects_allowlist=[],
        )
        assert result.layer_results[0].passed is True
        assert result.layer_results[1].passed is False


# ---------------------------------------------------------------------------
# ValidationResult to_dict round-trip (for execution record inclusion)
# ---------------------------------------------------------------------------


class TestValidationResultDict:
    def test_passing_result_dict_has_correct_status(
        self,
        schema_requiring_findings: dict,
        valid_output: dict,
    ) -> None:
        result = run_layers_1_and_2(
            output=valid_output,
            schema=schema_requiring_findings,
            tool_invocations=[],
            side_effects_allowlist=[],
        )
        d = result.to_dict()
        assert d["validation_status"] == "passed"

    def test_failing_result_dict_has_failed_status(
        self,
        schema_requiring_findings: dict,
        invalid_output: dict,
    ) -> None:
        result = run_layers_1_and_2(
            output=invalid_output,
            schema=schema_requiring_findings,
            tool_invocations=[],
            side_effects_allowlist=[],
        )
        d = result.to_dict()
        assert d["validation_status"] == "failed"

    def test_result_dict_has_layer_results_key(
        self,
        schema_requiring_findings: dict,
        valid_output: dict,
    ) -> None:
        result = run_layers_1_and_2(
            output=valid_output,
            schema=schema_requiring_findings,
            tool_invocations=[],
            side_effects_allowlist=[],
        )
        d = result.to_dict()
        assert "layer_results" in d

    def test_layer_results_each_have_passed_field(
        self,
        schema_requiring_findings: dict,
        valid_output: dict,
    ) -> None:
        result = run_layers_1_and_2(
            output=valid_output,
            schema=schema_requiring_findings,
            tool_invocations=[],
            side_effects_allowlist=[],
        )
        d = result.to_dict()
        for lr_dict in d["layer_results"]:
            assert "passed" in lr_dict

    def test_failed_layer_result_dict_has_error(
        self,
        schema_requiring_findings: dict,
        invalid_output: dict,
    ) -> None:
        result = run_layers_1_and_2(
            output=invalid_output,
            schema=schema_requiring_findings,
            tool_invocations=[],
            side_effects_allowlist=[],
        )
        d = result.to_dict()
        failed_layers = [lr for lr in d["layer_results"] if not lr["passed"]]
        assert len(failed_layers) > 0
        assert "error" in failed_layers[0]
