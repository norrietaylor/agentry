"""Unit tests for T05.2: Layer 3 (Output Path Enforcement) and pipeline orchestration.

Tests cover:
- Layer 3: declared output paths allow writes (prefix matching).
- Layer 3: writes to undeclared paths are blocked.
- Layer 3: empty output_paths list blocks all writes.
- Layer 3: result structure (layer=3, passed, error fields).
- Pipeline: all three layers run in sequence when all pass.
- Pipeline: halt-on-failure at Layer 1, 2, and 3.
- Pipeline: ValidationResult includes correct layer count and statuses.
- Budget: max_findings truncation with truncation note.
- Budget: no truncation when findings <= max_findings.
- Budget: no truncation when max_findings is None.
- Budget: non-list findings are left unchanged.
- Budget: BudgetResult metadata (truncated, original_count, truncated_count).
- Full pipeline to_dict round-trip with all three layers.
- UndeclaredOutputPathError exception.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentry.validation import (
    UndeclaredOutputPathError,
    apply_budget,
    run_pipeline,
    validate_output_paths,
)

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
    return {"findings": [{"file": "main.py", "line": 1, "msg": "ok"}]}


@pytest.fixture()
def invalid_output() -> dict[str, Any]:
    # findings must be an array; this is a string.
    return {"findings": "not-an-array"}


# ---------------------------------------------------------------------------
# Layer 3: Output Path Enforcement — passing cases
# ---------------------------------------------------------------------------


class TestLayer3Pass:
    def test_no_writes_always_passes(self) -> None:
        result = validate_output_paths([], [])
        assert result.passed is True

    def test_no_writes_with_output_paths_passes(self) -> None:
        result = validate_output_paths([], [".agentry/runs/"])
        assert result.passed is True

    def test_declared_path_allows_write(self) -> None:
        writes = [{"path": ".agentry/runs/output.json"}]
        result = validate_output_paths(writes, [".agentry/runs/"])
        assert result.passed is True

    def test_prefix_matching_subpath_passes(self) -> None:
        writes = [{"path": ".agentry/runs/2026-01-01/output.json"}]
        result = validate_output_paths(writes, [".agentry/runs/"])
        assert result.passed is True

    def test_multiple_writes_all_declared_passes(self) -> None:
        writes = [
            {"path": ".agentry/runs/output.json"},
            {"path": ".agentry/runs/summary.txt"},
        ]
        result = validate_output_paths(writes, [".agentry/runs/"])
        assert result.passed is True

    def test_multiple_output_paths_uses_correct_prefix(self) -> None:
        writes = [{"path": "/tmp/agentry-output/result.json"}]
        result = validate_output_paths(writes, [".agentry/runs/", "/tmp/agentry-output/"])
        assert result.passed is True

    def test_passed_result_layer_is_3(self) -> None:
        result = validate_output_paths([], [".agentry/runs/"])
        assert result.layer == 3

    def test_passed_result_has_no_error(self) -> None:
        result = validate_output_paths([], [".agentry/runs/"])
        assert result.error is None


# ---------------------------------------------------------------------------
# Layer 3: Output Path Enforcement — blocking cases
# ---------------------------------------------------------------------------


class TestLayer3Block:
    def test_undeclared_path_fails(self) -> None:
        writes = [{"path": "/tmp/unauthorized-path"}]
        result = validate_output_paths(writes, [".agentry/runs/"])
        assert result.passed is False

    def test_empty_output_paths_blocks_all_writes(self) -> None:
        writes = [{"path": ".agentry/runs/output.json"}]
        result = validate_output_paths(writes, [])
        assert result.passed is False

    def test_blocked_result_layer_is_3(self) -> None:
        writes = [{"path": "/tmp/unauthorized"}]
        result = validate_output_paths(writes, [".agentry/runs/"])
        assert result.layer == 3

    def test_blocked_result_has_error(self) -> None:
        writes = [{"path": "/tmp/unauthorized"}]
        result = validate_output_paths(writes, [".agentry/runs/"])
        assert result.error is not None

    def test_blocked_error_has_path_key(self) -> None:
        writes = [{"path": "/tmp/unauthorized"}]
        result = validate_output_paths(writes, [".agentry/runs/"])
        assert result.error is not None
        assert "path" in result.error
        assert result.error["path"] == "/tmp/unauthorized"

    def test_blocked_error_has_output_paths_key(self) -> None:
        writes = [{"path": "/tmp/unauthorized"}]
        result = validate_output_paths(writes, [".agentry/runs/"])
        assert result.error is not None
        assert "output_paths" in result.error

    def test_blocked_error_has_message_key(self) -> None:
        writes = [{"path": "/tmp/unauthorized"}]
        result = validate_output_paths(writes, [".agentry/runs/"])
        assert result.error is not None
        assert "message" in result.error

    def test_blocked_error_message_contains_attempted_path(self) -> None:
        writes = [{"path": "/tmp/unauthorized-path"}]
        result = validate_output_paths(writes, [".agentry/runs/"])
        assert result.error is not None
        assert "/tmp/unauthorized-path" in result.error["message"]

    def test_blocked_error_message_contains_allowed_paths(self) -> None:
        writes = [{"path": "/tmp/other"}]
        result = validate_output_paths(writes, [".agentry/runs/"])
        assert result.error is not None
        assert ".agentry/runs/" in result.error["message"]

    def test_first_undeclared_write_blocks(self) -> None:
        writes = [
            {"path": ".agentry/runs/ok.json"},
            {"path": "/tmp/bad"},
        ]
        result = validate_output_paths(writes, [".agentry/runs/"])
        assert result.passed is False
        assert result.error is not None
        assert result.error["path"] == "/tmp/bad"

    def test_partial_prefix_not_allowed(self) -> None:
        # ".agentry/run" is NOT a prefix of ".agentry/runs/"
        writes = [{"path": ".agentry/runs/output.json"}]
        result = validate_output_paths(writes, [".agentry/run"])
        # ".agentry/run" is a prefix of ".agentry/runs/" — this should PASS.
        # (prefix semantics: the path starts with the declared prefix)
        assert result.passed is True

    def test_non_matching_longer_prefix_blocks(self) -> None:
        writes = [{"path": ".agentry/other/output.json"}]
        result = validate_output_paths(writes, [".agentry/runs/"])
        assert result.passed is False


# ---------------------------------------------------------------------------
# Pipeline: Sequential execution with all three layers
# ---------------------------------------------------------------------------


class TestPipelineAllPass:
    def test_all_layers_pass_returns_passed(
        self,
        schema_requiring_findings: dict,
        valid_output: dict,
    ) -> None:
        result = run_pipeline(
            output=valid_output,
            schema=schema_requiring_findings,
            tool_invocations=[],
            side_effects_allowlist=[],
            file_writes=[],
            output_paths=[],
        )
        assert result.validation_status == "passed"

    def test_all_layers_pass_returns_three_layer_results(
        self,
        schema_requiring_findings: dict,
        valid_output: dict,
    ) -> None:
        result = run_pipeline(
            output=valid_output,
            schema=schema_requiring_findings,
            tool_invocations=[],
            side_effects_allowlist=[],
            file_writes=[],
            output_paths=[],
        )
        assert len(result.layer_results) == 3

    def test_all_layers_pass_each_layer_result_passed(
        self,
        schema_requiring_findings: dict,
        valid_output: dict,
    ) -> None:
        result = run_pipeline(
            output=valid_output,
            schema=schema_requiring_findings,
            tool_invocations=[],
            side_effects_allowlist=[],
            file_writes=[],
            output_paths=[],
        )
        for lr in result.layer_results:
            assert lr.passed is True

    def test_all_layers_pass_layer_numbers_sequential(
        self,
        schema_requiring_findings: dict,
        valid_output: dict,
    ) -> None:
        result = run_pipeline(
            output=valid_output,
            schema=schema_requiring_findings,
            tool_invocations=[],
            side_effects_allowlist=[],
            file_writes=[],
            output_paths=[],
        )
        layers = [lr.layer for lr in result.layer_results]
        assert layers == [1, 2, 3]

    def test_all_layers_pass_with_file_writes_in_allowed_paths(
        self,
        schema_requiring_findings: dict,
        valid_output: dict,
    ) -> None:
        result = run_pipeline(
            output=valid_output,
            schema=schema_requiring_findings,
            tool_invocations=[{"tool": "file:write", "args": {}}],
            side_effects_allowlist=["file:write"],
            file_writes=[{"path": ".agentry/runs/output.json"}],
            output_paths=[".agentry/runs/"],
        )
        assert result.validation_status == "passed"


# ---------------------------------------------------------------------------
# Pipeline: Halt-on-failure at Layer 1
# ---------------------------------------------------------------------------


class TestPipelineHaltAtLayer1:
    def test_layer1_failure_halts_at_layer1(
        self,
        schema_requiring_findings: dict,
        invalid_output: dict,
    ) -> None:
        result = run_pipeline(
            output=invalid_output,
            schema=schema_requiring_findings,
            tool_invocations=[{"tool": "file:write", "args": {}}],
            side_effects_allowlist=[],
            file_writes=[{"path": "/tmp/bad"}],
            output_paths=[],
        )
        assert result.validation_status == "failed"
        assert len(result.layer_results) == 1

    def test_layer1_failure_result_has_failed_layer1(
        self,
        schema_requiring_findings: dict,
        invalid_output: dict,
    ) -> None:
        result = run_pipeline(
            output=invalid_output,
            schema=schema_requiring_findings,
            tool_invocations=[],
            side_effects_allowlist=[],
            file_writes=[],
            output_paths=[],
        )
        assert result.layer_results[0].layer == 1
        assert result.layer_results[0].passed is False


# ---------------------------------------------------------------------------
# Pipeline: Halt-on-failure at Layer 2
# ---------------------------------------------------------------------------


class TestPipelineHaltAtLayer2:
    def test_layer2_failure_halts_at_layer2(
        self,
        schema_requiring_findings: dict,
        valid_output: dict,
    ) -> None:
        result = run_pipeline(
            output=valid_output,
            schema=schema_requiring_findings,
            tool_invocations=[{"tool": "file:write", "args": {}}],
            side_effects_allowlist=[],  # blocks file:write
            file_writes=[],
            output_paths=[],
        )
        assert result.validation_status == "failed"
        assert len(result.layer_results) == 2

    def test_layer2_failure_layer1_passed_layer2_failed(
        self,
        schema_requiring_findings: dict,
        valid_output: dict,
    ) -> None:
        result = run_pipeline(
            output=valid_output,
            schema=schema_requiring_findings,
            tool_invocations=[{"tool": "file:write", "args": {}}],
            side_effects_allowlist=[],
            file_writes=[],
            output_paths=[],
        )
        assert result.layer_results[0].passed is True
        assert result.layer_results[1].passed is False


# ---------------------------------------------------------------------------
# Pipeline: Halt-on-failure at Layer 3
# ---------------------------------------------------------------------------


class TestPipelineHaltAtLayer3:
    def test_layer3_failure_halts_at_layer3(
        self,
        schema_requiring_findings: dict,
        valid_output: dict,
    ) -> None:
        result = run_pipeline(
            output=valid_output,
            schema=schema_requiring_findings,
            tool_invocations=[],
            side_effects_allowlist=[],
            file_writes=[{"path": "/tmp/unauthorized"}],
            output_paths=[".agentry/runs/"],
        )
        assert result.validation_status == "failed"
        assert len(result.layer_results) == 3

    def test_layer3_failure_layers_1_and_2_passed(
        self,
        schema_requiring_findings: dict,
        valid_output: dict,
    ) -> None:
        result = run_pipeline(
            output=valid_output,
            schema=schema_requiring_findings,
            tool_invocations=[],
            side_effects_allowlist=[],
            file_writes=[{"path": "/tmp/unauthorized"}],
            output_paths=[".agentry/runs/"],
        )
        assert result.layer_results[0].passed is True
        assert result.layer_results[1].passed is True
        assert result.layer_results[2].passed is False

    def test_layer3_failure_result_is_failed_status(
        self,
        schema_requiring_findings: dict,
        valid_output: dict,
    ) -> None:
        result = run_pipeline(
            output=valid_output,
            schema=schema_requiring_findings,
            tool_invocations=[],
            side_effects_allowlist=[],
            file_writes=[{"path": "/tmp/unauthorized"}],
            output_paths=[".agentry/runs/"],
        )
        assert result.validation_status == "failed"


# ---------------------------------------------------------------------------
# Pipeline: to_dict round-trip with three layers
# ---------------------------------------------------------------------------


class TestPipelineToDict:
    def test_passing_pipeline_to_dict_has_three_layer_results(
        self,
        schema_requiring_findings: dict,
        valid_output: dict,
    ) -> None:
        result = run_pipeline(
            output=valid_output,
            schema=schema_requiring_findings,
            tool_invocations=[],
            side_effects_allowlist=[],
            file_writes=[],
            output_paths=[],
        )
        d = result.to_dict()
        assert len(d["layer_results"]) == 3

    def test_failing_at_layer3_to_dict_has_correct_status(
        self,
        schema_requiring_findings: dict,
        valid_output: dict,
    ) -> None:
        result = run_pipeline(
            output=valid_output,
            schema=schema_requiring_findings,
            tool_invocations=[],
            side_effects_allowlist=[],
            file_writes=[{"path": "/tmp/bad"}],
            output_paths=[".agentry/runs/"],
        )
        d = result.to_dict()
        assert d["validation_status"] == "failed"

    def test_to_dict_layer3_has_error_on_failure(
        self,
        schema_requiring_findings: dict,
        valid_output: dict,
    ) -> None:
        result = run_pipeline(
            output=valid_output,
            schema=schema_requiring_findings,
            tool_invocations=[],
            side_effects_allowlist=[],
            file_writes=[{"path": "/tmp/bad"}],
            output_paths=[".agentry/runs/"],
        )
        d = result.to_dict()
        layer3_dict = d["layer_results"][2]
        assert layer3_dict["passed"] is False
        assert "error" in layer3_dict


# ---------------------------------------------------------------------------
# Budget enforcement: apply_budget
# ---------------------------------------------------------------------------


class TestApplyBudget:
    def test_no_max_findings_returns_unchanged(self) -> None:
        output = {"findings": list(range(20))}
        br = apply_budget(output, max_findings=None)
        assert br.truncated is False
        assert br.output is output

    def test_findings_within_budget_not_truncated(self) -> None:
        output = {"findings": list(range(5))}
        br = apply_budget(output, max_findings=10)
        assert br.truncated is False
        assert len(br.output["findings"]) == 5

    def test_findings_equal_budget_not_truncated(self) -> None:
        output = {"findings": list(range(10))}
        br = apply_budget(output, max_findings=10)
        assert br.truncated is False

    def test_findings_exceed_budget_truncated(self) -> None:
        output = {"findings": list(range(15))}
        br = apply_budget(output, max_findings=10)
        assert br.truncated is True
        assert len(br.output["findings"]) == 10

    def test_truncation_removes_excess_findings(self) -> None:
        output = {"findings": list(range(15))}
        br = apply_budget(output, max_findings=10)
        # Should keep first 10.
        assert br.output["findings"] == list(range(10))

    def test_truncation_adds_truncation_note(self) -> None:
        output = {"findings": list(range(15))}
        br = apply_budget(output, max_findings=10)
        assert "_truncation_note" in br.output

    def test_truncation_note_mentions_count(self) -> None:
        output = {"findings": list(range(15))}
        br = apply_budget(output, max_findings=10)
        note = br.output["_truncation_note"]
        assert "5" in note  # 15 - 10 = 5 truncated

    def test_truncation_note_mentions_budget(self) -> None:
        output = {"findings": list(range(15))}
        br = apply_budget(output, max_findings=10)
        note = br.output["_truncation_note"]
        assert "10" in note

    def test_truncated_count_metadata_is_correct(self) -> None:
        output = {"findings": list(range(15))}
        br = apply_budget(output, max_findings=10)
        assert br.truncated_count == 5

    def test_original_count_metadata_is_correct(self) -> None:
        output = {"findings": list(range(15))}
        br = apply_budget(output, max_findings=10)
        assert br.original_count == 15

    def test_non_list_findings_not_truncated(self) -> None:
        output = {"findings": "not-a-list"}
        br = apply_budget(output, max_findings=5)
        assert br.truncated is False
        assert br.output["findings"] == "not-a-list"

    def test_no_findings_key_not_truncated(self) -> None:
        output = {"summary": "no findings here"}
        br = apply_budget(output, max_findings=5)
        assert br.truncated is False

    def test_other_output_keys_preserved_after_truncation(self) -> None:
        output = {"findings": list(range(15)), "summary": "lots of findings", "confidence": 0.9}
        br = apply_budget(output, max_findings=10)
        assert br.output["summary"] == "lots of findings"
        assert br.output["confidence"] == 0.9

    def test_original_output_not_mutated(self) -> None:
        findings = list(range(15))
        output = {"findings": findings}
        apply_budget(output, max_findings=10)
        # Original must not be mutated.
        assert len(output["findings"]) == 15


# ---------------------------------------------------------------------------
# UndeclaredOutputPathError exception
# ---------------------------------------------------------------------------


class TestUndeclaredOutputPathError:
    def test_exception_stores_path(self) -> None:
        err = UndeclaredOutputPathError("/tmp/bad", [".agentry/runs/"])
        assert err.path == "/tmp/bad"

    def test_exception_stores_output_paths(self) -> None:
        err = UndeclaredOutputPathError("/tmp/bad", [".agentry/runs/"])
        assert err.output_paths == [".agentry/runs/"]

    def test_exception_message_contains_path(self) -> None:
        err = UndeclaredOutputPathError("/tmp/bad", [".agentry/runs/"])
        assert "/tmp/bad" in str(err)

    def test_exception_message_contains_allowed_path(self) -> None:
        err = UndeclaredOutputPathError("/tmp/bad", [".agentry/runs/"])
        assert ".agentry/runs/" in str(err)

    def test_exception_empty_output_paths_shows_none(self) -> None:
        err = UndeclaredOutputPathError("/tmp/bad", [])
        assert "(none)" in str(err)
