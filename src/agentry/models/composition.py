"""Composition block model.

Multi-agent composition steps.  Parsed but not executed in Phase 1.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class CompositionStep(BaseModel):
    """A single step in a composition DAG."""

    model_config = ConfigDict(strict=True, extra="forbid")

    name: str
    workflow: str
    depends_on: list[str] = []


class CompositionBlock(BaseModel):
    """Composition block: defines a DAG of workflow steps.

    Parsed and validated in Phase 1 but execution is deferred to Phase 2.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    steps: list[CompositionStep] = []
