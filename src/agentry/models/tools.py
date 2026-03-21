"""Tools block model.

Declares the tool capabilities a workflow requires from its environment binder.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ToolsBlock(BaseModel):
    """Tool capability declarations for a workflow.

    Each entry is a tool identifier string such as ``repository:read`` or
    ``shell:execute``.  The environment binder is responsible for binding
    these identifiers to concrete implementations.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    capabilities: list[str] = []
