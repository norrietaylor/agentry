"""File-based data passing between composition nodes.

Provides utilities for resolving node input expressions to file paths,
writing node outputs to disk, and extracting named fields from JSON result
files.

Usage::

    from agentry.composition.data_passing import (
        DataPassingError,
        extract_field,
        resolve_node_inputs,
        write_node_output,
    )

    # Write a node's output after execution.
    output_path = write_node_output("step-a", {"summary": "done"}, run_dir)

    # Resolve inputs for a downstream step.
    resolved = resolve_node_inputs(step, node_outputs, node_failures)
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentry.models.composition import CompositionStep

logger = logging.getLogger(__name__)


class DataPassingError(Exception):
    """Raised when data passing between composition nodes fails.

    Covers missing upstream outputs, field extraction failures, and
    malformed source expressions.
    """


def resolve_node_inputs(
    step: CompositionStep,
    node_outputs: dict[str, Path],
    node_failures: dict[str, Path],
) -> dict[str, str]:
    """Resolve input expressions for a composition step to file paths.

    For each entry in ``step.inputs``, the source expression is parsed
    and the appropriate file path is returned:

    - ``"<node_id>.output"`` -- returns the absolute path to the upstream
      node's ``result.json`` file as recorded in *node_outputs*.  If the
      upstream node failed under the ``skip`` policy, the path to its
      :class:`~agentry.composition.failure.NodeFailure` JSON is returned
      instead (taken from *node_failures*).
    - ``"<node_id>.output.<field>"`` -- reads the upstream ``result.json``,
      extracts the named top-level field, writes the value to a temporary
      file, and returns the path to that temp file.

    Args:
        step: The composition step whose inputs should be resolved.
        node_outputs: Mapping from node ID to the absolute ``Path`` of its
            ``result.json`` file (populated for successfully completed nodes).
        node_failures: Mapping from node ID to the absolute ``Path`` of its
            failure JSON (populated when the node failed with skip policy).

    Returns:
        A mapping from input key to absolute file path string.

    Raises:
        DataPassingError: If a referenced upstream node has no output and
            is not present in *node_failures*.
    """
    resolved: dict[str, str] = {}

    for key, source_expr in step.inputs.items():
        resolved[key] = _resolve_single_input(
            step_node_id=step.node_id,
            key=key,
            source_expr=source_expr,
            node_outputs=node_outputs,
            node_failures=node_failures,
        )

    return resolved


def _resolve_single_input(
    step_node_id: str,
    key: str,
    source_expr: str,
    node_outputs: dict[str, Path],
    node_failures: dict[str, Path],
) -> str:
    """Resolve a single input source expression to an absolute file path.

    Args:
        step_node_id: Node ID of the step owning this input (for error messages).
        key: The input key name (for error messages).
        source_expr: The source expression (e.g. ``"node-a.output"`` or
            ``"node-a.output.summary"``).
        node_outputs: Mapping from node ID to completed result path.
        node_failures: Mapping from node ID to failure JSON path.

    Returns:
        Absolute path string for the resolved input.

    Raises:
        DataPassingError: If the upstream node is neither in *node_outputs*
            nor in *node_failures*, or the expression is malformed.
    """
    # Split on '.' to determine the expression form.
    parts = source_expr.split(".")

    # Minimum valid form is "<node_id>.output" -> 2 parts.
    if len(parts) < 2 or parts[1] != "output":
        raise DataPassingError(
            f"Step '{step_node_id}' input '{key}' has malformed source "
            f"expression '{source_expr}'. "
            f"Expected '<node_id>.output' or '<node_id>.output.<field>'."
        )

    upstream_node_id = parts[0]

    # Check for skip-policy failure path first.
    if upstream_node_id in node_failures:
        failure_path = node_failures[upstream_node_id]
        logger.debug(
            "Step '%s' input '%s': upstream '%s' failed with skip policy; "
            "returning failure path '%s'.",
            step_node_id,
            key,
            upstream_node_id,
            failure_path,
        )
        return str(failure_path.resolve())

    if upstream_node_id not in node_outputs:
        raise DataPassingError(
            f"Step '{step_node_id}' input '{key}' references upstream node "
            f"'{upstream_node_id}', but that node has no output available "
            f"and is not in the failures map.  Ensure '{upstream_node_id}' "
            f"is listed in depends_on and has completed before this step runs."
        )

    output_path = node_outputs[upstream_node_id]

    # Simple form: <node_id>.output -- return the result.json path directly.
    if len(parts) == 2:
        return str(output_path.resolve())

    # Field extraction form: <node_id>.output.<field>
    field_name = parts[2]
    value = extract_field(output_path, field_name)

    # Write the extracted value to a temporary file so callers receive a
    # consistent file-path interface regardless of extraction depth.
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".txt",
        prefix=f"agentry-field-{upstream_node_id}-{field_name}-",
        delete=False,
    ) as tmp:
        tmp.write(str(value))
        tmp_name = tmp.name

    logger.debug(
        "Step '%s' input '%s': extracted field '%s' from '%s' -> '%s'.",
        step_node_id,
        key,
        field_name,
        output_path,
        tmp_name,
    )
    return tmp_name


def write_node_output(
    node_id: str,
    output: dict[str, Any],
    run_dir: Path,
) -> Path:
    """Write a node's output dictionary to ``<run_dir>/<node_id>/result.json``.

    Creates the node directory if it does not already exist.

    Args:
        node_id: Identifier of the composition node.
        output: JSON-serialisable dictionary to persist.
        run_dir: Root directory for the composition run.

    Returns:
        Absolute path to the written ``result.json`` file.
    """
    node_dir = run_dir / node_id
    node_dir.mkdir(parents=True, exist_ok=True)

    result_path = node_dir / "result.json"
    result_path.write_text(json.dumps(output, indent=2))

    logger.debug(
        "Wrote output for node '%s' to '%s'.", node_id, result_path
    )
    return result_path.resolve()


def extract_field(json_path: Path, field_name: str) -> Any:
    """Extract a named top-level field from a JSON file.

    Args:
        json_path: Path to the JSON file to read.
        field_name: Top-level key to extract.

    Returns:
        The value associated with *field_name*.

    Raises:
        DataPassingError: If the file cannot be read, is not valid JSON, or
            the field is absent.
    """
    try:
        raw = json_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DataPassingError(
            f"Cannot read JSON file '{json_path}': {exc}"
        ) from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DataPassingError(
            f"File '{json_path}' is not valid JSON: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise DataPassingError(
            f"File '{json_path}' does not contain a JSON object at the top "
            f"level; cannot extract field '{field_name}'."
        )

    if field_name not in data:
        raise DataPassingError(
            f"Field '{field_name}' not found in '{json_path}'. "
            f"Available fields: {sorted(data.keys())}"
        )

    return data[field_name]
