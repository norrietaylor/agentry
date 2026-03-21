"""Unit tests for T05.1: Layer 1 (Schema Validation) and Layer 2 (Side-Effect Allowlist).

Tests cover:
- Layer 1: valid output passes schema validation.
- Layer 1: invalid output fails with schema_path, failed_keyword, and message.
- Layer 1: missing required field is reported.
- Layer 1: wrong type is reported.
- Layer 2: declared side effects are allowed.
- Layer 2: undeclared side effects are blocked and reported.
- Layer 2: empty allowlist blocks all side-effect-producing tools.
- Layer 2: read-only tools are never blocked regardless of allowlist.
- Result dataclasses serialise correctly.
"""

from __future__ import annotations

import pytest

from agentry.validation import (
    LayerResult,
    ValidationResult,
    validate_schema,
    validate_side_effects,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def full_schema() -> dict:
    """JSON Schema requiring findings (array), summary (string), confidence (number)."""
    return {
        "type": "object",
        "required": ["findings", "summary", "confidence"],
        "properties": {
            "findings": {"type": "array"},
            "summary": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "additionalProperties": False,
    }


@pytest.fixture()
def valid_output() -> dict:
    """Output that satisfies full_schema."""
    return {
        "findings": [{"file": "main.py", "line": 10, "message": "unused import"}],
        "summary": "One issue found.",
        "confidence": 0.9,
    }


# ---------------------------------------------------------------------------
# Layer 1: Schema Validation — passing cases
# ---------------------------------------------------------------------------


class TestLayer1Pass:
    def test_valid_output_passes(self, full_schema: dict, valid_output: dict) -> None:
        result = validate_schema(valid_output, full_schema)
        assert result.passed is True

    def test_passed_result_has_no_error(
        self, full_schema: dict, valid_output: dict
    ) -> None:
        result = validate_schema(valid_output, full_schema)
        assert result.error is None

    def test_passed_result_layer_is_1(
        self, full_schema: dict, valid_output: dict
    ) -> None:
        result = validate_schema(valid_output, full_schema)
        assert result.layer == 1

    def test_empty_schema_always_passes(self) -> None:
        result = validate_schema({"anything": True}, {})
        assert result.passed is True


# ---------------------------------------------------------------------------
# Layer 1: Schema Validation — failure cases
# ---------------------------------------------------------------------------


class TestLayer1Fail:
    def test_missing_required_field_fails(self, full_schema: dict) -> None:
        output = {"findings": [], "summary": "ok"}  # missing confidence
        result = validate_schema(output, full_schema)
        assert result.passed is False

    def test_failure_has_error_dict(self, full_schema: dict) -> None:
        output = {"findings": [], "summary": "ok"}
        result = validate_schema(output, full_schema)
        assert result.error is not None
        assert isinstance(result.error, dict)

    def test_failure_error_has_schema_path(self, full_schema: dict) -> None:
        output = {"findings": [], "summary": "ok"}
        result = validate_schema(output, full_schema)
        assert "schema_path" in result.error  # type: ignore[index]

    def test_failure_error_has_failed_keyword(self, full_schema: dict) -> None:
        output = {"findings": [], "summary": "ok"}
        result = validate_schema(output, full_schema)
        assert "failed_keyword" in result.error  # type: ignore[index]

    def test_failure_error_has_message(self, full_schema: dict) -> None:
        output = {"findings": [], "summary": "ok"}
        result = validate_schema(output, full_schema)
        assert "message" in result.error  # type: ignore[index]

    def test_wrong_type_fails(self, full_schema: dict) -> None:
        # findings must be array; passing a string instead.
        output = {"findings": "not-an-array", "summary": "ok", "confidence": 0.5}
        result = validate_schema(output, full_schema)
        assert result.passed is False

    def test_wrong_type_error_has_type_keyword(self, full_schema: dict) -> None:
        output = {"findings": "not-an-array", "summary": "ok", "confidence": 0.5}
        result = validate_schema(output, full_schema)
        assert result.error is not None
        assert result.error["failed_keyword"] == "type"

    def test_wrong_type_schema_path_includes_field_name(self, full_schema: dict) -> None:
        output = {"findings": "not-an-array", "summary": "ok", "confidence": 0.5}
        result = validate_schema(output, full_schema)
        assert result.error is not None
        # The schema path should reference the "findings" field.
        assert "findings" in result.error["schema_path"]

    def test_missing_required_keyword_is_required(self, full_schema: dict) -> None:
        output = {"findings": [], "summary": "ok"}  # missing confidence
        result = validate_schema(output, full_schema)
        assert result.error is not None
        assert result.error["failed_keyword"] == "required"

    def test_failure_error_message_is_nonempty_string(self, full_schema: dict) -> None:
        output = {"findings": [], "summary": "ok"}
        result = validate_schema(output, full_schema)
        assert result.error is not None
        assert isinstance(result.error["message"], str)
        assert len(result.error["message"]) > 0

    def test_additional_property_forbidden(self, full_schema: dict) -> None:
        output = {
            "findings": [],
            "summary": "ok",
            "confidence": 0.5,
            "unexpected": "field",
        }
        result = validate_schema(output, full_schema)
        assert result.passed is False


# ---------------------------------------------------------------------------
# Layer 2: Side-Effect Allowlist — passing cases
# ---------------------------------------------------------------------------


class TestLayer2Pass:
    def test_empty_invocations_always_passes(self) -> None:
        result = validate_side_effects([], [])
        assert result.passed is True

    def test_declared_side_effect_passes(self) -> None:
        invocations = [{"tool": "file:write", "args": {}}]
        result = validate_side_effects(invocations, ["file:write"])
        assert result.passed is True

    def test_multiple_declared_effects_pass(self) -> None:
        invocations = [
            {"tool": "file:write", "args": {}},
            {"tool": "pr:comment", "args": {}},
        ]
        result = validate_side_effects(invocations, ["file:write", "pr:comment"])
        assert result.passed is True

    def test_passed_result_layer_is_2(self) -> None:
        result = validate_side_effects([], [])
        assert result.layer == 2

    def test_passed_result_has_no_error(self) -> None:
        result = validate_side_effects([], [])
        assert result.error is None

    def test_read_only_tool_not_blocked_with_empty_allowlist(self) -> None:
        invocations = [{"tool": "repository:read", "args": {}}]
        result = validate_side_effects(invocations, [])
        assert result.passed is True

    def test_shell_execute_not_blocked_with_empty_allowlist(self) -> None:
        # shell:execute is read-only in Phase 1.
        invocations = [{"tool": "shell:execute", "args": {}}]
        result = validate_side_effects(invocations, [])
        assert result.passed is True


# ---------------------------------------------------------------------------
# Layer 2: Side-Effect Allowlist — blocking cases
# ---------------------------------------------------------------------------


class TestLayer2Block:
    def test_undeclared_side_effect_fails(self) -> None:
        invocations = [{"tool": "file:write", "args": {}}]
        result = validate_side_effects(invocations, [])
        assert result.passed is False

    def test_blocked_result_layer_is_2(self) -> None:
        invocations = [{"tool": "file:write", "args": {}}]
        result = validate_side_effects(invocations, [])
        assert result.layer == 2

    def test_blocked_result_has_error(self) -> None:
        invocations = [{"tool": "file:write", "args": {}}]
        result = validate_side_effects(invocations, [])
        assert result.error is not None

    def test_blocked_error_includes_side_effect_name(self) -> None:
        invocations = [{"tool": "file:write", "args": {}}]
        result = validate_side_effects(invocations, [])
        assert result.error is not None
        assert "file:write" in result.error["message"]

    def test_blocked_error_includes_allowlist_info(self) -> None:
        invocations = [{"tool": "pr:comment", "args": {}}]
        result = validate_side_effects(invocations, ["file:write"])
        assert result.error is not None
        # Message should mention the allowed list.
        assert "file:write" in result.error["message"]

    def test_blocked_error_side_effect_key_present(self) -> None:
        invocations = [{"tool": "file:write", "args": {}}]
        result = validate_side_effects(invocations, [])
        assert result.error is not None
        assert "side_effect" in result.error
        assert result.error["side_effect"] == "file:write"

    def test_blocked_error_allowlist_key_present(self) -> None:
        invocations = [{"tool": "file:write", "args": {}}]
        result = validate_side_effects(invocations, [])
        assert result.error is not None
        assert "allowlist" in result.error

    def test_first_undeclared_effect_blocks(self) -> None:
        invocations = [
            {"tool": "repository:read", "args": {}},  # allowed (read-only)
            {"tool": "file:write", "args": {}},  # undeclared
        ]
        result = validate_side_effects(invocations, [])
        assert result.passed is False


# ---------------------------------------------------------------------------
# LayerResult and ValidationResult serialisation
# ---------------------------------------------------------------------------


class TestResultSerialisation:
    def test_layer_result_to_dict_passed(self) -> None:
        lr = LayerResult(layer=1, passed=True)
        d = lr.to_dict()
        assert d["layer"] == 1
        assert d["passed"] is True
        assert "error" not in d

    def test_layer_result_to_dict_failed(self) -> None:
        lr = LayerResult(layer=1, passed=False, error={"schema_path": "$", "failed_keyword": "type", "message": "wrong"})
        d = lr.to_dict()
        assert d["passed"] is False
        assert "error" in d
        assert d["error"]["schema_path"] == "$"

    def test_validation_result_to_dict_passed(self) -> None:
        vr = ValidationResult(
            validation_status="passed",
            layer_results=[LayerResult(layer=1, passed=True)],
        )
        d = vr.to_dict()
        assert d["validation_status"] == "passed"
        assert len(d["layer_results"]) == 1
        assert d["layer_results"][0]["layer"] == 1

    def test_validation_result_to_dict_failed(self) -> None:
        vr = ValidationResult(
            validation_status="failed",
            layer_results=[
                LayerResult(
                    layer=1,
                    passed=False,
                    error={"schema_path": "$", "failed_keyword": "required", "message": "msg"},
                )
            ],
        )
        d = vr.to_dict()
        assert d["validation_status"] == "failed"
