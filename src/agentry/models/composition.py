"""Composition block model.

Multi-agent composition steps.  Parsed but not executed in Phase 1.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class FailurePolicy(BaseModel):
    """Failure handling policy for a composition step."""

    model_config = ConfigDict(strict=True, extra="forbid")

    mode: Literal["abort", "skip", "retry"] = "abort"
    max_retries: int = 1
    fallback: Literal["abort", "skip"] = "abort"


class CompositionStep(BaseModel):
    """A single step in a composition DAG."""

    model_config = ConfigDict(strict=True, extra="forbid")

    name: str
    workflow: str
    depends_on: list[str] = []
    id: str | None = None
    failure: FailurePolicy = FailurePolicy()
    inputs: dict[str, str] = {}

    @property
    def node_id(self) -> str:
        """Return the resolved node ID (id if set, else name)."""
        return self.id if self.id is not None else self.name


class CompositionBlock(BaseModel):
    """Composition block: defines a DAG of workflow steps.

    Parsed and validated in Phase 1 but execution is deferred to Phase 2.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    steps: list[CompositionStep] = []

    @property
    def node_ids(self) -> list[str]:
        """Return resolved IDs for all steps (id if set, else name)."""
        return [step.node_id for step in self.steps]
