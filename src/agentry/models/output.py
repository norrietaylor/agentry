"""Output block model.

Output schema declaration, side-effect allowlist, output paths, and budget.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SideEffect(BaseModel):
    """A declared side effect that the workflow is allowed to produce."""

    model_config = ConfigDict(strict=True, extra="forbid")

    type: str
    description: str = ""


class BudgetConfig(BaseModel):
    """Budget limits for agent output."""

    model_config = ConfigDict(strict=True, extra="forbid")

    max_findings: int | None = Field(default=None, ge=1)


class OutputBlock(BaseModel):
    """Output configuration for a workflow.

    Attributes:
        schema_def: JSON Schema object that the agent output must conform to.
        side_effects: Allowlist of side effects the workflow may produce.
        output_paths: List of allowed output file paths (relative to run dir).
        budget: Budget configuration for limiting output volume.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    schema_def: dict[str, Any] = Field(default_factory=dict, alias="schema")
    side_effects: list[SideEffect] = []
    output_paths: list[str] = []
    budget: BudgetConfig = BudgetConfig()
