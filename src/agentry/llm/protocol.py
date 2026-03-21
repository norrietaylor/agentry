"""LLMClient protocol definition.

Defines the interface that all LLM provider implementations must satisfy.
Providers are interchangeable: swapping AnthropicProvider for a future
OpenAI provider requires no changes to the agent execution engine.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agentry.llm.models import LLMConfig, LLMMessage, LLMResponse, LLMToolDefinition


@runtime_checkable
class LLMClient(Protocol):
    """Protocol for LLM provider clients.

    An LLMClient sends a prompt to a language model and returns a structured
    response. Implementations must support both synchronous and asynchronous
    invocation via ``call()`` and ``call_async()``.
    """

    def call(
        self,
        system_prompt: str,
        messages: list[LLMMessage],
        tools: list[LLMToolDefinition],
        config: LLMConfig,
    ) -> LLMResponse:
        """Send a synchronous request to the LLM.

        Args:
            system_prompt: The system prompt text (already loaded from disk).
            messages: Ordered list of conversation messages to send.
            tools: Tool definitions to expose to the model for tool-use.
            config: Call-level configuration (model, temperature, max_tokens,
                timeout).

        Returns:
            The model's response, including content, any tool calls, and token
            usage statistics.

        Raises:
            LLMAuthError: If the API key is missing or invalid.
            LLMTimeoutError: If the call exceeds ``config.timeout`` seconds.
            LLMProviderError: For other provider-level errors.
        """
        ...

    async def call_async(
        self,
        system_prompt: str,
        messages: list[LLMMessage],
        tools: list[LLMToolDefinition],
        config: LLMConfig,
    ) -> LLMResponse:
        """Send an asynchronous request to the LLM.

        Identical semantics to :meth:`call` but returns a coroutine suitable
        for use with ``await`` in an async context.

        Args:
            system_prompt: The system prompt text (already loaded from disk).
            messages: Ordered list of conversation messages to send.
            tools: Tool definitions to expose to the model for tool-use.
            config: Call-level configuration (model, temperature, max_tokens,
                timeout).

        Returns:
            The model's response, including content, any tool calls, and token
            usage statistics.

        Raises:
            LLMAuthError: If the API key is missing or invalid.
            LLMTimeoutError: If the call exceeds ``config.timeout`` seconds.
            LLMProviderError: For other provider-level errors.
        """
        ...


def build_tool_definitions(tool_names: list[str]) -> list[dict[str, Any]]:
    """Convert abstract tool names into Anthropic tool-use definition dicts.

    Returns minimal tool definitions that describe each built-in tool. Callers
    that need richer schemas can override by providing full
    :class:`LLMToolDefinition` objects directly.

    Args:
        tool_names: Tool identifiers, e.g. ``["repository:read", "shell:execute"]``.

    Returns:
        A list of dicts suitable for passing directly to the Anthropic
        ``messages.create(tools=...)`` parameter.
    """
    _builtin: dict[str, dict[str, Any]] = {
        "repository:read": {
            "name": "repository__read",
            "description": (
                "Read the contents of a file from the target repository. "
                "Access is restricted to files within the repository root."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to the file within the repository.",
                    }
                },
                "required": ["path"],
            },
        },
        "shell:execute": {
            "name": "shell__execute",
            "description": (
                "Execute a read-only shell command from the allowed list "
                "(git log, git diff, git show, git blame, ls, find, grep, "
                "cat, head, tail, wc)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute.",
                    }
                },
                "required": ["command"],
            },
        },
    }

    result: list[dict[str, Any]] = []
    for name in tool_names:
        if name in _builtin:
            result.append(_builtin[name])
        else:
            # Unknown tool: produce a pass-through definition so the model
            # at least knows the tool exists. The executor will validate
            # actual invocations against the allowlist.
            result.append(
                {
                    "name": name.replace(":", "__"),
                    "description": f"Tool: {name}",
                    "input_schema": {"type": "object", "properties": {}},
                }
            )
    return result
