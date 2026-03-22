"""Agent block model.

Agent runtime configuration: runtime identifier, model, system prompt,
maximum iterations, and optional runtime-specific config dict.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Known runtime identifiers.  Any value outside this set will cause
# ``agentry validate`` to report a warning (not a hard error) so that
# custom or third-party runtimes can still be used without forcing a
# schema update.
KNOWN_RUNTIMES: frozenset[str] = frozenset({"claude-code"})


class AgentBlock(BaseModel):
    """Agent runtime configuration.

    Attributes:
        runtime: The agent runtime to use (e.g. ``"claude-code"``).
        model: The model identifier forwarded to the runtime (e.g.
            ``"claude-sonnet-4-20250514"``).  Optional; the runtime uses
            its own default when omitted.
        system_prompt: Path to the system prompt file relative to the
            workflow file.  Optional; empty string means no system prompt.
        max_iterations: Maximum number of agent iterations.  None means
            the runtime uses its own default.
        config: Runtime-specific configuration dict forwarded verbatim to
            the runtime factory.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    runtime: str
    model: str = "claude-sonnet-4-20250514"
    system_prompt: str = ""
    max_iterations: int | None = Field(default=None, ge=1)
    config: dict[str, Any] = Field(default_factory=dict)
