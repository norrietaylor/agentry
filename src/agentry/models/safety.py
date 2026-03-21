"""Safety block model.

Resource constraints for workflow execution (timeout, etc.) plus Phase 2
sandbox configuration (trust level, filesystem access, network allow-list,
sandbox image).
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


class TrustLevel(str, Enum):
    """Trust level that determines how strictly the sandbox is enforced."""

    sandboxed = "sandboxed"
    elevated = "elevated"


def _coerce_trust_level(v: object) -> object:
    """Pre-validate coercion of plain strings to TrustLevel (for YAML parsing)."""
    if isinstance(v, str):
        return TrustLevel(v)
    return v


# Annotated type that accepts a bare string and coerces it to TrustLevel before
# strict validation runs.  This is necessary because strict=True normally
# forbids str→Enum coercion, but workflow YAML always produces string values.
_TrustLevelField = Annotated[TrustLevel, BeforeValidator(_coerce_trust_level)]


class ResourceConfig(BaseModel):
    """Resource limits for a single workflow execution."""

    model_config = ConfigDict(strict=True, extra="forbid")

    timeout: int = Field(default=300, ge=1, description="Execution timeout in seconds.")
    cpu: float = Field(default=1.0, gt=0, description="CPU cores available to the runner.")
    memory: str = Field(default="2GB", description="Memory limit (e.g. '2GB', '512MB').")


class FilesystemConfig(BaseModel):
    """Filesystem path access patterns for the sandboxed runner."""

    model_config = ConfigDict(strict=True, extra="forbid")

    read: list[str] = Field(
        default_factory=list,
        description="Glob patterns for paths the runner may read.",
    )
    write: list[str] = Field(
        default_factory=list,
        description="Glob patterns for paths the runner may write.",
    )


class NetworkConfig(BaseModel):
    """Network allow-list for the sandboxed runner."""

    model_config = ConfigDict(strict=True, extra="forbid")

    allow: list[str] = Field(
        default_factory=list,
        description="Domain names the runner is permitted to reach.",
    )


class SandboxConfig(BaseModel):
    """Docker sandbox image configuration."""

    model_config = ConfigDict(strict=True, extra="forbid")

    base: str = Field(
        default="agentry/sandbox:1.0",
        description="Base Docker image used to provision the sandbox container.",
    )


class SafetyBlock(BaseModel):
    """Safety configuration for a workflow.

    Phase 1 fields (``resources.timeout``) remain fully backward-compatible.
    Phase 2 fields (``trust``, extended ``resources``, ``filesystem``,
    ``network``, ``sandbox``) are optional with safe defaults so existing
    workflows that omit them continue to parse without modification.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    trust: _TrustLevelField = Field(
        default=TrustLevel.sandboxed,
        description="Execution trust level; 'sandboxed' enforces all restrictions.",
    )
    resources: ResourceConfig = Field(default_factory=ResourceConfig)
    filesystem: FilesystemConfig = Field(default_factory=FilesystemConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
