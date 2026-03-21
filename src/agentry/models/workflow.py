"""Top-level WorkflowDefinition model.

Composes all seven blocks and provides cross-block validation (e.g. $variable
reference resolution).
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentry.models.composition import CompositionBlock
from agentry.models.identity import IdentityBlock
from agentry.models.inputs import InputType
from agentry.models.model import ModelBlock
from agentry.models.output import OutputBlock
from agentry.models.safety import SafetyBlock
from agentry.models.tools import ToolsBlock

# Well-known runtime variables that need not be declared as inputs.
WELL_KNOWN_VARIABLES: frozenset[str] = frozenset(
    {"$output_dir", "$codebase", "$diff", "$pr_url"}
)

_VARIABLE_RE = re.compile(r"\$[a-zA-Z_][a-zA-Z0-9_]*")


def _collect_variable_refs(obj: Any) -> set[str]:
    """Recursively collect all ``$variable`` references from an object tree."""
    refs: set[str] = set()
    if isinstance(obj, str):
        refs.update(_VARIABLE_RE.findall(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            refs.update(_collect_variable_refs(v))
    elif isinstance(obj, list):
        for item in obj:
            refs.update(_collect_variable_refs(item))
    return refs


class WorkflowDefinition(BaseModel):
    """Top-level workflow definition composed of all seven blocks.

    Cross-block validation ensures that every ``$variable`` reference in the
    definition resolves to either a declared input name or a well-known
    runtime variable (``$output_dir``, ``$codebase``, ``$diff``, ``$pr_url``).
    """

    model_config = ConfigDict(strict=True, extra="forbid", populate_by_name=True)

    identity: IdentityBlock
    inputs: dict[str, InputType] = Field(default_factory=dict)
    tools: ToolsBlock = ToolsBlock()
    model: ModelBlock = ModelBlock()
    safety: SafetyBlock = SafetyBlock()
    output: OutputBlock = OutputBlock()
    composition: CompositionBlock = CompositionBlock()

    @model_validator(mode="after")
    def validate_variable_references(self) -> WorkflowDefinition:
        """Ensure all $variable references resolve to declared inputs or well-knowns."""
        # Build the set of declared input names (prefixed with $).
        declared: set[str] = {f"${name}" for name in self.inputs}

        # Collect all $variable references from the full model dump.
        # Exclude the inputs block itself so we only validate references in
        # other blocks (model, output, composition, etc.).
        data = self.model_dump(by_alias=True)
        data.pop("inputs", None)
        refs = _collect_variable_refs(data)

        unresolved = refs - declared - WELL_KNOWN_VARIABLES
        if unresolved:
            sorted_vars = sorted(unresolved)
            msg = (
                f"Unresolved variable reference(s): {', '.join(sorted_vars)}. "
                "Each $variable must be declared as an input or be a well-known "
                f"runtime variable ({', '.join(sorted(WELL_KNOWN_VARIABLES))})."
            )
            raise ValueError(msg)
        return self
