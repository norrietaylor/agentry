"""AnthropicProvider: LLMClient implementation using the Anthropic Python SDK.

Reads the API key from the ``ANTHROPIC_API_KEY`` environment variable and
raises a clear, actionable error if it is not set.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any

import anthropic

from agentry.llm.exceptions import LLMAuthError, LLMProviderError, LLMTimeoutError
from agentry.llm.models import LLMConfig, LLMMessage, LLMResponse, LLMToolDefinition, TokenUsage

if TYPE_CHECKING:
    pass

_API_KEY_ENV_VAR = "ANTHROPIC_API_KEY"


def _get_api_key() -> str:
    """Read the Anthropic API key from the environment.

    Returns:
        The API key string.

    Raises:
        LLMAuthError: If ``ANTHROPIC_API_KEY`` is not set in the environment.
    """
    key = os.environ.get(_API_KEY_ENV_VAR, "").strip()
    if not key:
        raise LLMAuthError(
            f"Environment variable {_API_KEY_ENV_VAR!r} is not set.",
            suggestion=(
                f"Export your Anthropic API key before running agentry:\n"
                f"  export {_API_KEY_ENV_VAR}=sk-ant-..."
            ),
        )
    return key


def _messages_to_sdk_params(
    messages: list[LLMMessage],
) -> list[dict[str, str]]:
    """Convert :class:`~agentry.llm.models.LLMMessage` objects to Anthropic SDK dicts.

    Args:
        messages: List of agentry message objects.

    Returns:
        List of ``{"role": ..., "content": ...}`` dicts accepted by the SDK.
    """
    return [{"role": msg.role, "content": msg.content} for msg in messages]


def _tools_to_sdk_params(
    tools: list[LLMToolDefinition],
) -> list[dict[str, Any]]:
    """Convert :class:`~agentry.llm.models.LLMToolDefinition` objects to SDK tool dicts.

    Args:
        tools: List of agentry tool definitions.

    Returns:
        List of tool definition dicts accepted by the Anthropic SDK.
    """
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
        }
        for tool in tools
    ]


def _extract_response(sdk_message: anthropic.types.Message) -> LLMResponse:
    """Extract an :class:`~agentry.llm.models.LLMResponse` from an SDK Message object.

    Args:
        sdk_message: The ``Message`` object returned by ``client.messages.create``.

    Returns:
        A provider-agnostic :class:`~agentry.llm.models.LLMResponse`.
    """
    content_text = ""
    tool_calls: list[dict[str, Any]] = []

    for block in sdk_message.content:
        if block.type == "text":
            content_text += block.text
        elif block.type == "tool_use":
            tool_calls.append(
                {
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                }
            )

    usage = TokenUsage(
        input_tokens=sdk_message.usage.input_tokens,
        output_tokens=sdk_message.usage.output_tokens,
    )

    return LLMResponse(
        content=content_text,
        tool_calls=tool_calls,
        usage=usage,
        model=sdk_message.model,
        stop_reason=sdk_message.stop_reason or "",
    )


class AnthropicProvider:
    """LLM client implementation backed by the Anthropic Python SDK.

    Reads the API key from ``ANTHROPIC_API_KEY`` at construction time.

    Raises:
        LLMAuthError: If ``ANTHROPIC_API_KEY`` is not set.
    """

    def __init__(self) -> None:
        api_key = _get_api_key()
        self._client = anthropic.Anthropic(api_key=api_key)
        self._async_client = anthropic.AsyncAnthropic(api_key=api_key)

    # ------------------------------------------------------------------
    # LLMClient protocol
    # ------------------------------------------------------------------

    def call(
        self,
        system_prompt: str,
        messages: list[LLMMessage],
        tools: list[LLMToolDefinition],
        config: LLMConfig,
    ) -> LLMResponse:
        """Send a synchronous request to the Anthropic Messages API.

        Args:
            system_prompt: The system prompt to prepend to the conversation.
            messages: The conversation messages (user/assistant turns).
            tools: Tool definitions to make available to the model.
            config: Model configuration (model, temperature, max_tokens, timeout).

        Returns:
            The model's response as an :class:`~agentry.llm.models.LLMResponse`.

        Raises:
            LLMAuthError: If the API key is invalid or missing.
            LLMTimeoutError: If the call exceeds ``config.timeout`` seconds.
            LLMProviderError: For other API errors.
        """
        sdk_messages = _messages_to_sdk_params(messages)
        sdk_tools = _tools_to_sdk_params(tools)
        timeout: float | None = config.timeout

        create_kwargs: dict[str, Any] = {
            "model": config.model,
            "max_tokens": config.max_tokens,
            "system": system_prompt,
            "messages": sdk_messages,
            "temperature": config.temperature,
        }
        if sdk_tools:
            create_kwargs["tools"] = sdk_tools
        if timeout is not None:
            create_kwargs["timeout"] = timeout

        try:
            response = self._client.messages.create(**create_kwargs)
        except anthropic.AuthenticationError as exc:
            raise LLMAuthError(
                str(exc),
                suggestion=(
                    f"Ensure {_API_KEY_ENV_VAR!r} is set to a valid Anthropic API key."
                ),
            ) from exc
        except anthropic.APITimeoutError as exc:
            timed_out = timeout if timeout is not None else 0.0
            raise LLMTimeoutError(timed_out) from exc
        except anthropic.APIError as exc:
            status = getattr(exc, "status_code", None)
            raise LLMProviderError(str(exc), status_code=status) from exc

        return _extract_response(response)

    async def call_async(
        self,
        system_prompt: str,
        messages: list[LLMMessage],
        tools: list[LLMToolDefinition],
        config: LLMConfig,
    ) -> LLMResponse:
        """Send an asynchronous request to the Anthropic Messages API.

        Args:
            system_prompt: The system prompt to prepend to the conversation.
            messages: The conversation messages (user/assistant turns).
            tools: Tool definitions to make available to the model.
            config: Model configuration (model, temperature, max_tokens, timeout).

        Returns:
            The model's response as an :class:`~agentry.llm.models.LLMResponse`.

        Raises:
            LLMAuthError: If the API key is invalid or missing.
            LLMTimeoutError: If the call exceeds ``config.timeout`` seconds.
            LLMProviderError: For other API errors.
        """
        sdk_messages = _messages_to_sdk_params(messages)
        sdk_tools = _tools_to_sdk_params(tools)
        timeout = config.timeout

        create_kwargs: dict[str, Any] = {
            "model": config.model,
            "max_tokens": config.max_tokens,
            "system": system_prompt,
            "messages": sdk_messages,
            "temperature": config.temperature,
        }
        if sdk_tools:
            create_kwargs["tools"] = sdk_tools
        if timeout is not None:
            create_kwargs["timeout"] = timeout

        try:
            if timeout is not None:
                response = await asyncio.wait_for(
                    self._async_client.messages.create(**create_kwargs),
                    timeout=timeout,
                )
            else:
                response = await self._async_client.messages.create(**create_kwargs)
        except asyncio.TimeoutError as exc:
            raise LLMTimeoutError(timeout if timeout is not None else 0.0) from exc
        except anthropic.AuthenticationError as exc:
            raise LLMAuthError(
                str(exc),
                suggestion=(
                    f"Ensure {_API_KEY_ENV_VAR!r} is set to a valid Anthropic API key."
                ),
            ) from exc
        except anthropic.APITimeoutError as exc:
            raise LLMTimeoutError(timeout if timeout is not None else 0.0) from exc
        except anthropic.APIError as exc:
            status = getattr(exc, "status_code", None)
            raise LLMProviderError(str(exc), status_code=status) from exc

        return _extract_response(response)
