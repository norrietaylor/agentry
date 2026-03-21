"""Data structures for validation pipeline results.

Defines the structured result types that are included in the execution record
and consumed by the CLI output formatter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LayerResult:
    """Result from a single validation layer.

    Attributes:
        layer: The layer number (1, 2, or 3).
        passed: True when the layer check succeeded.
        error: Structured error details when ``passed=False``, else None.
    """

    layer: int
    passed: bool
    error: dict[str, Any] | None = field(default=None)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary."""
        result: dict[str, Any] = {
            "layer": self.layer,
            "passed": self.passed,
        }
        if self.error is not None:
            result["error"] = self.error
        return result


@dataclass
class ValidationResult:
    """Aggregated result from the full validation pipeline.

    Attributes:
        validation_status: ``"passed"`` when all layers succeeded, else ``"failed"``.
        layer_results: One entry per executed layer, in execution order.
    """

    validation_status: str
    layer_results: list[LayerResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary (for inclusion in execution record)."""
        return {
            "validation_status": self.validation_status,
            "layer_results": [lr.to_dict() for lr in self.layer_results],
        }
