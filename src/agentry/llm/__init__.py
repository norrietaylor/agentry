"""LLM client protocol and provider implementations.

Public API for the llm package:

- :class:`LLMClient` — the protocol that all providers must satisfy.
- :class:`LLMMessage`, :class:`LLMToolDefinition`, :class:`LLMConfig`,
  :class:`LLMResponse`, :class:`TokenUsage` — shared data models.
- :class:`AnthropicProvider` — Anthropic Claude backend.
- :exc:`LLMAuthError`, :exc:`LLMTimeoutError`, :exc:`LLMProviderError`
  — provider-agnostic error hierarchy.
"""

from agentry.llm.exceptions import LLMAuthError, LLMError, LLMProviderError, LLMTimeoutError
from agentry.llm.models import LLMConfig, LLMMessage, LLMResponse, LLMToolDefinition, TokenUsage
from agentry.llm.protocol import LLMClient, build_tool_definitions
from agentry.llm.providers.anthropic import AnthropicProvider

__all__ = [
    "LLMClient",
    "LLMMessage",
    "LLMToolDefinition",
    "LLMConfig",
    "LLMResponse",
    "TokenUsage",
    "LLMError",
    "LLMAuthError",
    "LLMTimeoutError",
    "LLMProviderError",
    "AnthropicProvider",
    "build_tool_definitions",
]
