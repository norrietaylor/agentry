"""Output validation pipeline.

Implements the three-layer output validation that gates all agent output before
emission. Layers execute in sequence; failure at any layer halts processing.

Public API
----------
- :class:`~agentry.validation.result.LayerResult` — Result from one layer.
- :class:`~agentry.validation.result.ValidationResult` — Aggregated pipeline result.
- :func:`~agentry.validation.layer1.validate_schema` — Layer 1: JSON Schema validation.
- :func:`~agentry.validation.layer2.validate_side_effects` — Layer 2: Side-effect allowlist.
- :exc:`~agentry.validation.exceptions.SchemaValidationError` — Layer 1 error.
- :exc:`~agentry.validation.exceptions.UndeclaredSideEffectError` — Layer 2 error.
"""

from agentry.validation.exceptions import SchemaValidationError, UndeclaredSideEffectError
from agentry.validation.layer1 import validate_schema
from agentry.validation.layer2 import validate_side_effects
from agentry.validation.result import LayerResult, ValidationResult

__all__ = [
    "LayerResult",
    "ValidationResult",
    "SchemaValidationError",
    "UndeclaredSideEffectError",
    "validate_schema",
    "validate_side_effects",
]
