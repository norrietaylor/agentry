"""Unit tests for T01.3: Composition model extensions and DAG validation.

Tests cover:
- Model parsing with new fields (id, failure policy, inputs mapping).
- Backward compatibility: CompositionStep with only name, workflow, depends_on.
- FailurePolicy defaults: mode, max_retries, fallback.
- DAG validation - valid linear chain A->B->C.
- DAG validation - cycle detection with cycle path in error message.
- DAG validation - unknown depends_on reference raises ValidationError.
- DAG validation - input source expression referencing unknown node.
- DAG validation - input referencing a node not in depends_on.
- CompositionBlock.node_ids helper returns correct list.
- Fan-out DAG: A->[B,C]->D.
- Single node composition.
- Empty composition.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentry.models import CompositionBlock, CompositionStep
from agentry.models.composition import FailurePolicy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _step(
    name: str,
    workflow: str = "stub.yaml",
    depends_on: list[str] | None = None,
    **kwargs,
) -> CompositionStep:
    """Return a CompositionStep with sensible defaults."""
    return CompositionStep(
        name=name,
        workflow=workflow,
        depends_on=depends_on or [],
        **kwargs,
    )


# ---------------------------------------------------------------------------
# FailurePolicy defaults
# ---------------------------------------------------------------------------


class TestFailurePolicyDefaults:
    def test_mode_defaults_to_abort(self) -> None:
        policy = FailurePolicy()
        assert policy.mode == "abort"

    def test_max_retries_defaults_to_1(self) -> None:
        policy = FailurePolicy()
        assert policy.max_retries == 1

    def test_fallback_defaults_to_abort(self) -> None:
        policy = FailurePolicy()
        assert policy.fallback == "abort"

    def test_mode_skip(self) -> None:
        policy = FailurePolicy(mode="skip")
        assert policy.mode == "skip"

    def test_mode_retry(self) -> None:
        policy = FailurePolicy(mode="retry", max_retries=3)
        assert policy.mode == "retry"
        assert policy.max_retries == 3

    def test_fallback_skip(self) -> None:
        policy = FailurePolicy(mode="retry", fallback="skip")
        assert policy.fallback == "skip"

    def test_invalid_mode_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FailurePolicy(mode="explode")  # type: ignore[arg-type]

    def test_invalid_fallback_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FailurePolicy(fallback="retry")  # type: ignore[arg-type]

    def test_unknown_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FailurePolicy(extra="bad")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# CompositionStep - new fields
# ---------------------------------------------------------------------------


class TestCompositionStepNewFields:
    def test_parse_with_id(self) -> None:
        step = _step("review", id="step-a")
        assert step.id == "step-a"

    def test_node_id_uses_id_when_set(self) -> None:
        step = _step("review", id="step-a")
        assert step.node_id == "step-a"

    def test_node_id_falls_back_to_name(self) -> None:
        step = _step("review")
        assert step.node_id == "review"

    def test_parse_failure_policy_all_three_modes(self) -> None:
        for mode in ("abort", "skip", "retry"):
            step = _step("s", failure={"mode": mode, "max_retries": 2, "fallback": "abort"})
            assert step.failure.mode == mode

    def test_parse_inputs_mapping(self) -> None:
        step = _step(
            "fix",
            depends_on=["review"],
            inputs={"review_output": "review.output"},
        )
        assert step.inputs["review_output"] == "review.output"

    def test_id_defaults_to_none(self) -> None:
        step = _step("review")
        assert step.id is None


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


class TestCompositionStepBackwardCompatibility:
    def test_minimal_step_parses(self) -> None:
        step = CompositionStep(name="review", workflow="code-review.yaml")
        assert step.name == "review"
        assert step.workflow == "code-review.yaml"
        assert step.depends_on == []

    def test_id_defaults_to_none(self) -> None:
        step = CompositionStep(name="review", workflow="code-review.yaml")
        assert step.id is None

    def test_node_id_equals_name_when_no_id(self) -> None:
        step = CompositionStep(name="review", workflow="code-review.yaml")
        assert step.node_id == "review"

    def test_failure_policy_has_defaults(self) -> None:
        step = CompositionStep(name="s", workflow="w.yaml")
        assert step.failure.mode == "abort"

    def test_inputs_defaults_to_empty_dict(self) -> None:
        step = CompositionStep(name="s", workflow="w.yaml")
        assert step.inputs == {}


# ---------------------------------------------------------------------------
# CompositionBlock.node_ids helper
# ---------------------------------------------------------------------------


class TestCompositionBlockNodeIds:
    def test_node_ids_uses_id_when_present(self) -> None:
        block = CompositionBlock(
            steps=[
                CompositionStep(name="review", workflow="r.yaml", id="step-a"),
                CompositionStep(name="fix", workflow="f.yaml", id="step-b", depends_on=["step-a"]),
            ]
        )
        assert block.node_ids == ["step-a", "step-b"]

    def test_node_ids_falls_back_to_name(self) -> None:
        block = CompositionBlock(
            steps=[
                CompositionStep(name="review", workflow="r.yaml"),
                CompositionStep(name="fix", workflow="f.yaml", depends_on=["review"]),
            ]
        )
        assert block.node_ids == ["review", "fix"]

    def test_node_ids_mixed(self) -> None:
        block = CompositionBlock(
            steps=[
                CompositionStep(name="review", workflow="r.yaml", id="a"),
                CompositionStep(name="fix", workflow="f.yaml", depends_on=["a"]),
            ]
        )
        assert block.node_ids == ["a", "fix"]

    def test_node_ids_empty_when_no_steps(self) -> None:
        block = CompositionBlock()
        assert block.node_ids == []


# ---------------------------------------------------------------------------
# DAG validation - valid DAGs
# ---------------------------------------------------------------------------


class TestDagValidationValid:
    def test_linear_chain_a_b_c(self) -> None:
        block = CompositionBlock(
            steps=[
                CompositionStep(name="A", workflow="a.yaml"),
                CompositionStep(name="B", workflow="b.yaml", depends_on=["A"]),
                CompositionStep(name="C", workflow="c.yaml", depends_on=["B"]),
            ]
        )
        assert len(block.steps) == 3

    def test_fan_out_a_to_b_c_then_d(self) -> None:
        """A->[B,C]->D is a valid diamond DAG."""
        block = CompositionBlock(
            steps=[
                CompositionStep(name="A", workflow="a.yaml"),
                CompositionStep(name="B", workflow="b.yaml", depends_on=["A"]),
                CompositionStep(name="C", workflow="c.yaml", depends_on=["A"]),
                CompositionStep(name="D", workflow="d.yaml", depends_on=["B", "C"]),
            ]
        )
        assert len(block.steps) == 4

    def test_single_node(self) -> None:
        block = CompositionBlock(
            steps=[CompositionStep(name="only", workflow="only.yaml")]
        )
        assert len(block.steps) == 1

    def test_empty_steps(self) -> None:
        block = CompositionBlock(steps=[])
        assert block.steps == []

    def test_empty_composition_block_default(self) -> None:
        block = CompositionBlock()
        assert block.steps == []


# ---------------------------------------------------------------------------
# DAG validation - cycle detection
# ---------------------------------------------------------------------------


class TestDagValidationCycles:
    def test_simple_cycle_a_b_a(self) -> None:
        with pytest.raises(ValidationError, match="Cycle detected"):
            CompositionBlock(
                steps=[
                    CompositionStep(name="A", workflow="a.yaml", depends_on=["B"]),
                    CompositionStep(name="B", workflow="b.yaml", depends_on=["A"]),
                ]
            )

    def test_three_node_cycle_a_b_c_a(self) -> None:
        with pytest.raises(ValidationError, match="Cycle detected"):
            CompositionBlock(
                steps=[
                    CompositionStep(name="A", workflow="a.yaml", depends_on=["C"]),
                    CompositionStep(name="B", workflow="b.yaml", depends_on=["A"]),
                    CompositionStep(name="C", workflow="c.yaml", depends_on=["B"]),
                ]
            )

    def test_self_dependency(self) -> None:
        with pytest.raises(ValidationError, match="Cycle detected"):
            CompositionBlock(
                steps=[
                    CompositionStep(name="A", workflow="a.yaml", depends_on=["A"]),
                ]
            )


# ---------------------------------------------------------------------------
# DAG validation - unknown depends_on references
# ---------------------------------------------------------------------------


class TestDagValidationUnknownDependency:
    def test_unknown_depends_on_raises(self) -> None:
        with pytest.raises(ValidationError, match="unknown depends_on"):
            CompositionBlock(
                steps=[
                    CompositionStep(name="A", workflow="a.yaml"),
                    CompositionStep(name="B", workflow="b.yaml", depends_on=["GHOST"]),
                ]
            )

    def test_typo_in_depends_on_raises(self) -> None:
        with pytest.raises(ValidationError, match="unknown depends_on"):
            CompositionBlock(
                steps=[
                    CompositionStep(name="review", workflow="r.yaml"),
                    CompositionStep(name="fix", workflow="f.yaml", depends_on=["reviw"]),
                ]
            )


# ---------------------------------------------------------------------------
# DAG validation - input source expressions
# ---------------------------------------------------------------------------


class TestDagValidationInputs:
    def test_valid_input_source_expression(self) -> None:
        block = CompositionBlock(
            steps=[
                CompositionStep(name="A", workflow="a.yaml"),
                CompositionStep(
                    name="B",
                    workflow="b.yaml",
                    depends_on=["A"],
                    inputs={"data": "A.output"},
                ),
            ]
        )
        assert block.steps[1].inputs["data"] == "A.output"

    def test_valid_input_source_with_field(self) -> None:
        block = CompositionBlock(
            steps=[
                CompositionStep(name="A", workflow="a.yaml"),
                CompositionStep(
                    name="B",
                    workflow="b.yaml",
                    depends_on=["A"],
                    inputs={"result": "A.output.findings"},
                ),
            ]
        )
        assert block.steps[1].inputs["result"] == "A.output.findings"

    def test_input_referencing_unknown_node_raises(self) -> None:
        with pytest.raises(ValidationError, match="unknown node"):
            CompositionBlock(
                steps=[
                    CompositionStep(name="A", workflow="a.yaml"),
                    CompositionStep(
                        name="B",
                        workflow="b.yaml",
                        depends_on=["A"],
                        inputs={"data": "GHOST.output"},
                    ),
                ]
            )

    def test_input_source_not_in_depends_on_raises(self) -> None:
        """Input references a known node but it is not in depends_on."""
        with pytest.raises(ValidationError, match="not listed in depends_on"):
            CompositionBlock(
                steps=[
                    CompositionStep(name="A", workflow="a.yaml"),
                    CompositionStep(name="B", workflow="b.yaml", depends_on=["A"]),
                    CompositionStep(
                        name="C",
                        workflow="c.yaml",
                        depends_on=["B"],
                        # References A but A is not in C's depends_on
                        inputs={"data": "A.output"},
                    ),
                ]
            )

    def test_invalid_input_expression_format_raises(self) -> None:
        """Source expression must match <node_id>.output[.<field>]."""
        with pytest.raises(ValidationError, match="invalid input source expression"):
            CompositionBlock(
                steps=[
                    CompositionStep(name="A", workflow="a.yaml"),
                    CompositionStep(
                        name="B",
                        workflow="b.yaml",
                        depends_on=["A"],
                        inputs={"data": "A.result"},  # 'result' not 'output'
                    ),
                ]
            )
