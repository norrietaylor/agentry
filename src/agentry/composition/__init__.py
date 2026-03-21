"""Composition package for multi-node DAG workflow execution.

Exports the key dataclasses and enums needed by higher-level modules
such as :mod:`agentry.composition.engine`.

Typical usage::

    from agentry.composition import (
        CompositionRecord,
        CompositionStatus,
        NodeStatus,
        make_composition_record,
    )

    from agentry.composition import CompositionDisplay
"""

from agentry.composition.display import CompositionDisplay
from agentry.composition.record import (
    CompositionRecord,
    CompositionStatus,
    NodeStatus,
    make_composition_record,
)

__all__ = [
    "CompositionDisplay",
    "CompositionRecord",
    "CompositionStatus",
    "NodeStatus",
    "make_composition_record",
]
