"""Layer 2: Side-Effect Allowlist.

Extracts side effects attempted by the agent (tool invocations that produce
external state changes) and verifies each against the ``output.side_effects``
allowlist declared in the workflow definition.

Any undeclared side effect is blocked and reported with:
- The side-effect identifier that was attempted.
- A note that the effect is not in the allowlist.
- A remediation suggestion.
"""

from __future__ import annotations

from typing import Any

from agentry.validation.result import LayerResult

# Tool invocations are classified as side-effect-producing if their name
# is not a pure read operation. This mapping covers Phase 1 tool names.
# Read-only tools do NOT produce side effects.
_READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "repository:read",
        "shell:execute",  # shell:execute is read-only in Phase 1 (allowlist enforced by binder)
    }
)


def validate_side_effects(
    tool_invocations: list[dict[str, Any]],
    side_effects_allowlist: list[str],
) -> LayerResult:
    """Verify that all side-effect-producing tool invocations are declared.

    Args:
        tool_invocations: The list of tool invocations recorded during agent
            execution. Each entry must have at minimum a ``"tool"`` key with
            the tool identifier string (e.g., ``"file:write"``).
        side_effects_allowlist: The list of allowed side-effect identifiers
            from the workflow's ``output.side_effects`` block.

    Returns:
        A :class:`~agentry.validation.result.LayerResult` with ``layer=2``,
        ``passed=True`` when all side effects are declared, or ``passed=False``
        with ``error`` describing the first undeclared side effect.
    """
    allowlist_set = set(side_effects_allowlist)

    for invocation in tool_invocations:
        tool_name: str = invocation.get("tool", "")
        # Only non-read-only tools are side-effect-producing.
        if tool_name in _READ_ONLY_TOOLS:
            continue
        if tool_name not in allowlist_set:
            allowed = (
                ", ".join(repr(s) for s in side_effects_allowlist)
                if side_effects_allowlist
                else "(none)"
            )
            return LayerResult(
                layer=2,
                passed=False,
                error={
                    "side_effect": tool_name,
                    "allowlist": side_effects_allowlist,
                    "message": (
                        f"Side effect {tool_name!r} was attempted but is not in the "
                        f"declared allowlist. Allowed: {allowed}. "
                        f"Add {tool_name!r} to output.side_effects in your workflow definition."
                    ),
                },
            )

    return LayerResult(layer=2, passed=True)
