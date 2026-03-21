"""Security audit functionality for workflow definition diffing.

Provides :func:`security_audit` which compares security-relevant fields between
two workflow definition versions and reports differences.  The security-relevant
fields are:

- ``safety.trust`` -- execution trust level
- ``safety.network.allow`` -- network domain allowlist
- ``safety.filesystem`` -- filesystem read/write access patterns
- ``output.side_effects`` -- declared side effects
- ``output.output_paths`` -- allowed output paths
- ``signature`` presence -- whether the workflow is signed

Usage::

    from agentry.security.audit import security_audit, SecurityAuditReport

    report = security_audit("workflows/v1.yaml", "workflows/v2.yaml")
    if report.has_differences:
        print(report.format_text())
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Security-relevant field extractors
# ---------------------------------------------------------------------------

#: Ordered list of security-relevant field paths to compare.
SECURITY_FIELDS: list[str] = [
    "safety.trust",
    "safety.network.allow",
    "safety.filesystem.read",
    "safety.filesystem.write",
    "output.side_effects",
    "output.output_paths",
]


def _get_nested(data: dict[str, Any], path: str) -> Any:
    """Retrieve a nested value by dot-separated path.

    Args:
        data: The root dictionary to traverse.
        path: Dot-separated path string, e.g. ``"safety.network.allow"``.

    Returns:
        The value at the path, or ``None`` if any segment is missing.
    """
    parts = path.split(".")
    current: Any = data
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


# ---------------------------------------------------------------------------
# Audit report data structures
# ---------------------------------------------------------------------------


@dataclass
class FieldDiff:
    """A single changed security-relevant field."""

    field: str
    old_value: Any
    new_value: Any

    def format_text(self) -> str:
        """Return a human-readable single-line diff description."""
        return f"  {self.field}:\n    - {self.old_value!r}\n    + {self.new_value!r}"


@dataclass
class SecurityAuditReport:
    """Result of a security audit comparison between two workflow versions.

    Attributes:
        path1: Path to the first (old) workflow file.
        path2: Path to the second (new) workflow file.
        diffs: List of changed security-relevant fields.
        unsigned_warning: Non-empty string if either workflow lacks a signature.
        warnings: Additional advisory messages.
    """

    path1: str
    path2: str
    diffs: list[FieldDiff] = field(default_factory=list)
    unsigned_warning: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def has_differences(self) -> bool:
        """Return True if any security-relevant fields differ."""
        return bool(self.diffs)

    @property
    def has_warnings(self) -> bool:
        """Return True if there are any warnings."""
        return bool(self.unsigned_warning) or bool(self.warnings)

    def format_text(self) -> str:
        """Format the audit report as human-readable text.

        Returns:
            A multi-line string suitable for terminal output.
        """
        lines: list[str] = []
        lines.append(f"Security audit: {self.path1} -> {self.path2}")
        lines.append("")

        if self.unsigned_warning:
            lines.append(f"WARNING: {self.unsigned_warning}")
            lines.append("")

        for w in self.warnings:
            lines.append(f"WARNING: {w}")

        if self.diffs:
            lines.append("Security-relevant field changes:")
            for diff in self.diffs:
                lines.append(diff.format_text())
        else:
            lines.append("No security-relevant field changes detected.")

        return "\n".join(lines)

    def format_json(self) -> dict[str, Any]:
        """Return the audit report as a JSON-serialisable dictionary."""
        return {
            "path1": self.path1,
            "path2": self.path2,
            "has_differences": self.has_differences,
            "diffs": [
                {
                    "field": d.field,
                    "old_value": d.old_value,
                    "new_value": d.new_value,
                }
                for d in self.diffs
            ],
            "unsigned_warning": self.unsigned_warning,
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# Signature warning helpers
# ---------------------------------------------------------------------------


def _check_signature_warnings(
    workflow1: dict[str, Any],
    workflow2: dict[str, Any],
    path1: str,
    path2: str,
) -> str:
    """Build a warning string if either workflow lacks a signature block.

    Args:
        workflow1: Parsed YAML of the first workflow.
        workflow2: Parsed YAML of the second workflow.
        path1: Display path for the first workflow.
        path2: Display path for the second workflow.

    Returns:
        A warning string, or an empty string if both are signed.
    """
    missing: list[str] = []
    if "signature" not in workflow1:
        missing.append(path1)
    if "signature" not in workflow2:
        missing.append(path2)

    if not missing:
        return ""
    if len(missing) == 1:
        return f"Workflow {missing[0]!r} is not signed. Consider running 'agentry sign' to sign it."
    return (
        f"Neither workflow is signed ({path1!r}, {path2!r}). "
        "Consider running 'agentry sign' to sign them."
    )


def _check_single_signature_warning(
    workflow: dict[str, Any],
    path: str,
) -> str:
    """Build a warning string if a single workflow lacks a signature block.

    Args:
        workflow: Parsed YAML of the workflow.
        path: Display path for the workflow.

    Returns:
        A warning string, or an empty string if it is signed.
    """
    if "signature" not in workflow:
        return f"Workflow {path!r} is not signed. Consider running 'agentry sign' to sign it."
    return ""


# ---------------------------------------------------------------------------
# Main audit entry point
# ---------------------------------------------------------------------------


def security_audit(
    path1: Path | str,
    path2: Path | str,
) -> SecurityAuditReport:
    """Compare security-relevant fields between two workflow YAML versions.

    Compares the following fields between *path1* (old) and *path2* (new):

    - ``safety.trust``
    - ``safety.network.allow``
    - ``safety.filesystem.read``
    - ``safety.filesystem.write``
    - ``output.side_effects``
    - ``output.output_paths``

    Also warns when either workflow lacks a ``signature`` block.

    Args:
        path1: Path to the first (old) workflow YAML file.
        path2: Path to the second (new) workflow YAML file.

    Returns:
        A :class:`SecurityAuditReport` containing any detected differences
        and warnings.

    Raises:
        FileNotFoundError: If either workflow file is not found.
        ValueError: If either file cannot be parsed as valid YAML.
    """
    path1 = Path(path1)
    path2 = Path(path2)

    if not path1.exists():
        raise FileNotFoundError(f"Workflow file not found: {path1}")
    if not path2.exists():
        raise FileNotFoundError(f"Workflow file not found: {path2}")

    try:
        with path1.open() as fh:
            workflow1: dict[str, Any] = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Failed to parse {path1}: {exc}") from exc

    try:
        with path2.open() as fh:
            workflow2: dict[str, Any] = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Failed to parse {path2}: {exc}") from exc

    diffs: list[FieldDiff] = []
    for field_path in SECURITY_FIELDS:
        old_val = _get_nested(workflow1, field_path)
        new_val = _get_nested(workflow2, field_path)
        if old_val != new_val:
            diffs.append(FieldDiff(field=field_path, old_value=old_val, new_value=new_val))

    unsigned_warning = _check_signature_warnings(
        workflow1, workflow2, str(path1), str(path2)
    )

    return SecurityAuditReport(
        path1=str(path1),
        path2=str(path2),
        diffs=diffs,
        unsigned_warning=unsigned_warning,
    )


def security_audit_single(
    path: Path | str,
) -> SecurityAuditReport:
    """Audit a single workflow for security warnings (e.g. missing signature).

    Unlike :func:`security_audit`, this function does not compare two versions.
    It only checks whether the workflow lacks a signature block.

    Args:
        path: Path to the workflow YAML file.

    Returns:
        A :class:`SecurityAuditReport` with ``path1 == path2`` and no diffs,
        but possibly with an ``unsigned_warning``.

    Raises:
        FileNotFoundError: If the workflow file is not found.
        ValueError: If the file cannot be parsed as valid YAML.
    """
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Workflow file not found: {path}")

    try:
        with path.open() as fh:
            workflow: dict[str, Any] = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Failed to parse {path}: {exc}") from exc

    unsigned_warning = _check_single_signature_warning(workflow, str(path))

    return SecurityAuditReport(
        path1=str(path),
        path2=str(path),
        diffs=[],
        unsigned_warning=unsigned_warning,
    )
