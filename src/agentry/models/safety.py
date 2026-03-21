"""Safety block model.

Resource constraints for workflow execution (timeout, etc.).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ResourceConfig(BaseModel):
    """Resource limits for a single workflow execution."""

    model_config = ConfigDict(strict=True, extra="forbid")

    timeout: int = Field(default=300, ge=1, description="Execution timeout in seconds.")


class SafetyBlock(BaseModel):
    """Safety configuration for a workflow.

    Parsed and validated in Phase 1 but not fully enforced at runtime
    (container isolation and network restrictions are Phase 2).
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    resources: ResourceConfig = ResourceConfig()
