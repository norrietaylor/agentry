"""YAML parser and validation error reporting for workflow definitions.

Loads a workflow YAML file using ``yaml.safe_load()``, feeds the raw data into
the Pydantic v2 models, and converts any ``ValidationError`` into structured,
human-readable error messages with file path, field path, and a remediation
suggestion (Rust compiler style).

Public API
----------
- :func:`load_workflow_file` -- Load and return a parsed WorkflowDefinition.
- :func:`validate_workflow_file` -- Return a list of formatted error strings (empty = valid).
- :class:`WorkflowLoadError` -- Raised by load_workflow_file on any failure.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from agentry.models.workflow import WorkflowDefinition

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class WorkflowLoadError(Exception):
    """Raised when a workflow file cannot be loaded or validated."""

    def __init__(self, path: str, errors: list[str]) -> None:
        self.path = path
        self.errors = errors
        super().__init__(f"Failed to load workflow '{path}': {len(errors)} error(s)")


# ---------------------------------------------------------------------------
# Remediation suggestions
# ---------------------------------------------------------------------------

# Maps pydantic error type codes to concise remediation suggestions.
_REMEDIATION: dict[str, str] = {
    "missing": "Add the required field to the workflow definition.",
    "extra_forbidden": "Remove the unknown field — only documented keys are allowed.",
    "value_error": "Check the field value against the workflow specification.",
    "int_type": "Expected an integer value.",
    "float_type": "Expected a numeric (float) value.",
    "bool_type": "Expected a boolean value (true/false).",
    "string_type": "Expected a string value.",
    "list_type": "Expected a list (sequence) value.",
    "dict_type": "Expected a mapping (dict) value.",
    "literal_error": "The value must be one of the allowed literals.",
    "union_tag_invalid": (
        "The 'type' field must be one of: git-diff, repository-ref, document-ref, string."
    ),
    "union_tag_not_found": "Add a 'type' field to specify the input type.",
    "greater_than": "The value must be greater than the minimum.",
    "less_than_equal": "The value must not exceed the maximum.",
}

_DEFAULT_REMEDIATION = "Refer to the Agentry workflow specification for valid values."


def _remediation(error_type: str, msg: str) -> str:
    """Return a remediation hint for a given pydantic error type and message."""
    if error_type in _REMEDIATION:
        return _REMEDIATION[error_type]
    # Try substring matches on message for richer hints
    lower_msg = msg.lower()
    if "semantic version" in lower_msg or "semver" in lower_msg:
        return "Use semantic versioning format: MAJOR.MINOR.PATCH (e.g. '1.0.0')."
    if "unresolved variable" in lower_msg:
        return (
            "Declare the variable as an input or use a well-known runtime variable "
            "($output_dir, $codebase, $diff, $pr_url)."
        )
    if "extra" in lower_msg:
        return "Remove the unknown field — only documented keys are allowed."
    return _DEFAULT_REMEDIATION


# ---------------------------------------------------------------------------
# Field path formatting
# ---------------------------------------------------------------------------

_LOCATION_SUBSCRIPT_RE = re.compile(r"\[(\d+)\]")


def _format_loc(loc: tuple[int | str, ...]) -> str:
    """Convert a pydantic location tuple to a dotted path string.

    Examples::

        ("model", "extra_field") -> "model.extra_field"
        ("inputs", "diff", "ref")  -> "inputs.diff.ref"
    """
    parts: list[str] = []
    for segment in loc:
        if isinstance(segment, int):
            # Array index — append as [N] to last segment
            if parts:
                parts[-1] = f"{parts[-1]}[{segment}]"
            else:
                parts.append(f"[{segment}]")
        else:
            parts.append(str(segment))
    return ".".join(parts)


# ---------------------------------------------------------------------------
# Error formatting
# ---------------------------------------------------------------------------


def _format_errors(path: str, exc: ValidationError) -> list[str]:
    """Convert a pydantic ValidationError into a list of formatted error strings.

    Each string follows the pattern::

        error[<type>]: <file_path>: <field_path>
          <message>
          hint: <remediation>
    """
    formatted: list[str] = []
    for error in exc.errors(include_url=False):
        error_type: str = error.get("type", "unknown")
        msg: str = error.get("msg", "Validation error")
        loc: tuple[int | str, ...] = error.get("loc", ())

        field_path = _format_loc(loc) if loc else "<root>"
        hint = _remediation(error_type, msg)

        lines = [
            f"error[{error_type}]: {path}: {field_path}",
            f"  {msg}",
            f"  hint: {hint}",
        ]
        formatted.append("\n".join(lines))
    return formatted


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_workflow_file(path: str) -> WorkflowDefinition:
    """Load and validate a workflow YAML file, returning a WorkflowDefinition.

    Parameters
    ----------
    path:
        File path to the YAML workflow definition.

    Returns
    -------
    WorkflowDefinition
        A fully validated workflow definition model.

    Raises
    ------
    WorkflowLoadError
        If the file cannot be read, is not valid YAML, or fails Pydantic validation.
    FileNotFoundError
        If the file does not exist.
    """
    file_path = Path(path)

    # 1. Read the file
    try:
        raw_text = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise WorkflowLoadError(path, [f"Cannot read file: {exc}"]) from exc

    # 2. Parse YAML
    try:
        data: Any = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise WorkflowLoadError(path, [f"YAML parse error: {exc}"]) from exc

    if not isinstance(data, dict):
        raise WorkflowLoadError(
            path,
            [
                f"error[invalid_type]: {path}: <root>\n"
                "  Workflow file must be a YAML mapping (dict) at the top level.\n"
                "  hint: Ensure the file starts with 'identity:' at the top level."
            ],
        )

    # 3. Validate with Pydantic
    try:
        return WorkflowDefinition(**data)
    except ValidationError as exc:
        errors = _format_errors(path, exc)
        raise WorkflowLoadError(path, errors) from exc


def validate_workflow_file(path: str) -> list[str]:
    """Validate a workflow YAML file and return a list of error strings.

    Returns an empty list if the workflow is valid, or a list of structured
    error messages (one per validation failure) if invalid.

    Parameters
    ----------
    path:
        File path to the YAML workflow definition.

    Returns
    -------
    list[str]
        Empty list on success; list of error strings on failure.
    """
    try:
        load_workflow_file(path)
        return []
    except FileNotFoundError as exc:
        return [f"error[file_not_found]: {path}: <root>\n  {exc}\n  hint: Check the file path."]
    except WorkflowLoadError as exc:
        return exc.errors
