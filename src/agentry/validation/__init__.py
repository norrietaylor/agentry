"""Output validation pipeline.

Implements the three-layer output validation that gates all agent output before
emission. Layers execute in sequence; failure at any layer halts processing.

Public API
----------
- :class:`~agentry.validation.result.LayerResult` — Result from one layer.
- :class:`~agentry.validation.result.ValidationResult` — Aggregated pipeline result.
- :func:`~agentry.validation.layer1.validate_schema` — Layer 1: JSON Schema validation.
- :func:`~agentry.validation.layer2.validate_side_effects` — Layer 2: Side-effect allowlist.
- :func:`~agentry.validation.layer3.validate_output_paths` — Layer 3: Output path enforcement.
- :func:`~agentry.validation.pipeline.run_pipeline` — Full sequential pipeline.
- :func:`~agentry.validation.pipeline.apply_budget` — Budget enforcement (max_findings truncation).
- :class:`~agentry.validation.pipeline.BudgetResult` — Budget enforcement result.
- :exc:`~agentry.validation.exceptions.SchemaValidationError` — Layer 1 error.
- :exc:`~agentry.validation.exceptions.UndeclaredSideEffectError` — Layer 2 error.
- :exc:`~agentry.validation.exceptions.UndeclaredOutputPathError` — Layer 3 error.
"""

from agentry.validation.exceptions import (
    SchemaValidationError,
    UndeclaredOutputPathError,
    UndeclaredSideEffectError,
)
from agentry.validation.layer1 import validate_schema
from agentry.validation.layer2 import validate_side_effects
from agentry.validation.layer3 import validate_output_paths
from agentry.validation.pipeline import BudgetResult, apply_budget, run_pipeline
from agentry.validation.result import LayerResult, ValidationResult

__all__ = [
    "LayerResult",
    "ValidationResult",
    "BudgetResult",
    "SchemaValidationError",
    "UndeclaredSideEffectError",
    "UndeclaredOutputPathError",
    "validate_schema",
    "validate_side_effects",
    "validate_output_paths",
    "run_pipeline",
    "apply_budget",
]
