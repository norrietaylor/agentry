"""Workflow definition models.

Pydantic v2 models for the seven workflow definition blocks:
identity, inputs, tools, model, safety, output, and composition.

Public API
----------
- :class:`~agentry.models.workflow.WorkflowDefinition` -- Top-level model.
- :class:`~agentry.models.identity.IdentityBlock` -- Name, version, description.
- :class:`~agentry.models.inputs.GitDiffInput` -- Git-diff input type.
- :class:`~agentry.models.inputs.RepositoryRefInput` -- Repository-ref input type.
- :class:`~agentry.models.inputs.DocumentRefInput` -- Document-ref input type.
- :class:`~agentry.models.tools.ToolsBlock` -- Tool capability declarations.
- :class:`~agentry.models.model.ModelBlock` -- LLM provider configuration.
- :class:`~agentry.models.safety.SafetyBlock` -- Resource constraints.
- :class:`~agentry.models.output.OutputBlock` -- Output schema and validation config.
- :class:`~agentry.models.composition.CompositionBlock` -- Multi-agent composition (parsed only).
"""

from agentry.models.composition import CompositionBlock, CompositionStep
from agentry.models.identity import IdentityBlock
from agentry.models.inputs import (
    DocumentRefInput,
    GitDiffInput,
    InputType,
    RepositoryRefInput,
)
from agentry.models.model import ModelBlock, RetryConfig
from agentry.models.output import BudgetConfig, OutputBlock, SideEffect
from agentry.models.safety import ResourceConfig, SafetyBlock
from agentry.models.tools import ToolsBlock
from agentry.models.workflow import WorkflowDefinition

__all__ = [
    "BudgetConfig",
    "CompositionBlock",
    "CompositionStep",
    "DocumentRefInput",
    "GitDiffInput",
    "IdentityBlock",
    "InputType",
    "ModelBlock",
    "OutputBlock",
    "RepositoryRefInput",
    "ResourceConfig",
    "RetryConfig",
    "SafetyBlock",
    "SideEffect",
    "ToolsBlock",
    "WorkflowDefinition",
]
