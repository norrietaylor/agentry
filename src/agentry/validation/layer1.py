"""Layer 1: JSON Schema Validation.

Validates agent output against the JSON Schema declared in the workflow's
``output.schema`` block using the ``jsonschema`` library.

On failure, produces a structured error indicating:
- The schema path where validation failed.
- The JSON Schema keyword that triggered the failure.
- A human-readable message explaining the problem.
"""

from __future__ import annotations

from typing import Any

import jsonschema
import jsonschema.exceptions

from agentry.validation.result import LayerResult


def validate_schema(
    output: Any,
    schema: dict[str, Any],
) -> LayerResult:
    """Validate *output* against *schema* using jsonschema.

    Args:
        output: The agent output to validate (any JSON-serialisable value).
        schema: The JSON Schema dict from the workflow's ``output.schema`` block.

    Returns:
        A :class:`~agentry.validation.result.LayerResult` with ``layer=1``,
        ``passed=True`` on success, or ``passed=False`` with ``error`` populated
        on failure.
    """
    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(output), key=lambda e: e.path)

    if not errors:
        return LayerResult(layer=1, passed=True)

    # Report the first (deepest) error with structured details.
    first_error: jsonschema.exceptions.ValidationError = errors[0]

    # Build a dot-notation schema path from the deque of path parts.
    if first_error.absolute_path:
        path_parts = list(first_error.absolute_path)
        schema_path = "$." + ".".join(str(p) for p in path_parts)
    else:
        schema_path = "$"

    failed_keyword = first_error.validator or "unknown"
    human_message = first_error.message

    return LayerResult(
        layer=1,
        passed=False,
        error={
            "schema_path": schema_path,
            "failed_keyword": str(failed_keyword),
            "message": human_message,
        },
    )
