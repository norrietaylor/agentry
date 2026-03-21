"""Output validation pipeline orchestrator.

Runs all three validation layers in sequence, halting on the first failure.
Also enforces the budget (``output.budget.max_findings``) by truncating the
findings list and appending a truncation note.

Usage::

    from agentry.validation.pipeline import run_pipeline, BudgetResult

    budget_result = apply_budget(output, max_findings=10)
    result = run_pipeline(
        output=budget_result.output,
        schema=workflow.output.schema_def,
        tool_invocations=tool_invocations,
        side_effects_allowlist=[se.type for se in workflow.output.side_effects],
        file_writes=file_writes,
        output_paths=workflow.output.output_paths,
    )
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentry.validation.layer1 import validate_schema
from agentry.validation.layer2 import validate_side_effects
from agentry.validation.layer3 import validate_output_paths
from agentry.validation.result import LayerResult, ValidationResult

_TRUNCATION_NOTE_KEY = "_truncation_note"


@dataclass
class BudgetResult:
    """Result of applying budget enforcement to agent output.

    Attributes:
        output: The (possibly truncated) output dictionary.
        truncated: True when findings were removed due to the budget.
        original_count: Number of findings before truncation.
        truncated_count: Number of findings removed (0 when not truncated).
    """

    output: dict[str, Any]
    truncated: bool = False
    original_count: int = 0
    truncated_count: int = 0


def apply_budget(
    output: dict[str, Any],
    max_findings: int | None,
) -> BudgetResult:
    """Enforce the ``max_findings`` budget limit on *output*.

    If ``max_findings`` is ``None`` or the output does not contain a
    ``"findings"`` key, the output is returned unchanged.

    When truncation occurs the returned output dict contains the truncated
    findings list plus a ``"_truncation_note"`` key describing how many
    items were removed.

    Args:
        output: The raw agent output dictionary.
        max_findings: The maximum number of findings allowed, or ``None`` for
            no limit.

    Returns:
        A :class:`BudgetResult` with the (possibly truncated) output and
        metadata about whether truncation occurred.
    """
    if max_findings is None:
        return BudgetResult(output=output)

    findings = output.get("findings")
    if not isinstance(findings, list):
        return BudgetResult(output=output)

    original_count = len(findings)
    if original_count <= max_findings:
        return BudgetResult(output=output)

    truncated_count = original_count - max_findings
    truncated_output = dict(output)
    truncated_output["findings"] = findings[:max_findings]
    truncated_output[_TRUNCATION_NOTE_KEY] = (
        f"{truncated_count} finding(s) truncated "
        f"(budget max_findings={max_findings}, original count={original_count})."
    )

    return BudgetResult(
        output=truncated_output,
        truncated=True,
        original_count=original_count,
        truncated_count=truncated_count,
    )


def run_pipeline(
    output: Any,
    schema: dict[str, Any],
    tool_invocations: list[dict[str, Any]],
    side_effects_allowlist: list[str],
    file_writes: list[dict[str, Any]],
    output_paths: list[str],
) -> ValidationResult:
    """Execute Layers 1, 2, and 3 in sequence, halting on the first failure.

    Args:
        output: The agent output to validate (any JSON-serialisable value).
        schema: The JSON Schema dict from the workflow's ``output.schema`` block.
        tool_invocations: Tool invocations recorded during agent execution.
            Each entry must have at minimum a ``"tool"`` key.
        side_effects_allowlist: Allowed side-effect identifiers from
            ``output.side_effects``.
        file_writes: File write operations recorded during execution.
            Each entry must have at minimum a ``"path"`` key.
        output_paths: Allowed output path prefixes from ``output.output_paths``.

    Returns:
        A :class:`~agentry.validation.result.ValidationResult` with
        ``validation_status`` set to ``"passed"`` or ``"failed"`` and
        ``layer_results`` for every layer that was evaluated.
    """
    layer_results: list[LayerResult] = []

    # Layer 1: JSON Schema validation.
    l1 = validate_schema(output, schema)
    layer_results.append(l1)
    if not l1.passed:
        return ValidationResult(validation_status="failed", layer_results=layer_results)

    # Layer 2: Side-effect allowlist.
    l2 = validate_side_effects(tool_invocations, side_effects_allowlist)
    layer_results.append(l2)
    if not l2.passed:
        return ValidationResult(validation_status="failed", layer_results=layer_results)

    # Layer 3: Output path enforcement.
    l3 = validate_output_paths(file_writes, output_paths)
    layer_results.append(l3)
    if not l3.passed:
        return ValidationResult(validation_status="failed", layer_results=layer_results)

    return ValidationResult(validation_status="passed", layer_results=layer_results)
