"""Layer 3: Output Path Enforcement.

Verifies that all file writes performed by the agent target paths within the
declared ``output.output_paths`` list from the workflow definition.  Any write
to an undeclared path is blocked and reported with:

- The attempted path that was not declared.
- The list of allowed output paths.
- A remediation suggestion.

Path matching uses a prefix check: a declared path ``".agentry/runs/"`` allows
writes to any path that starts with that prefix.
"""

from __future__ import annotations

from typing import Any

from agentry.validation.result import LayerResult


def validate_output_paths(
    file_writes: list[dict[str, Any]],
    output_paths: list[str],
) -> LayerResult:
    """Verify that all file writes target declared output paths.

    Args:
        file_writes: The list of file write operations recorded during agent
            execution.  Each entry must have at minimum a ``"path"`` key with
            the target file path string (e.g., ``".agentry/runs/output.json"``).
        output_paths: The list of allowed output path prefixes from the
            workflow's ``output.output_paths`` block.

    Returns:
        A :class:`~agentry.validation.result.LayerResult` with ``layer=3``,
        ``passed=True`` when all writes target declared paths, or
        ``passed=False`` with ``error`` describing the first undeclared write.
    """
    for write_op in file_writes:
        path: str = write_op.get("path", "")
        if not _path_is_allowed(path, output_paths):
            allowed = (
                ", ".join(repr(p) for p in output_paths)
                if output_paths
                else "(none)"
            )
            return LayerResult(
                layer=3,
                passed=False,
                error={
                    "path": path,
                    "output_paths": output_paths,
                    "message": (
                        f"File write to {path!r} is not within any declared output path. "
                        f"Allowed prefixes: {allowed}. "
                        f"Add an entry to output.output_paths in your workflow definition."
                    ),
                },
            )

    return LayerResult(layer=3, passed=True)


def _path_is_allowed(path: str, output_paths: list[str]) -> bool:
    """Return True if *path* starts with any of the declared *output_paths*."""
    return any(path.startswith(allowed_prefix) for allowed_prefix in output_paths)
