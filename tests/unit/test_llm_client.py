"""Unit tests for T04.1: LLMClient protocol and AnthropicProvider implementation.

All Anthropic SDK calls are mocked — no network traffic is made.

Tests cover:
- AnthropicProvider satisfies the LLMClient protocol.
- _get_api_key() raises LLMAuthError when ANTHROPIC_API_KEY is not set.
- AnthropicProvider raises LLMAuthError at construction when ANTHROPIC_API_KEY
  is absent.
- call() builds the correct SDK parameters (model, temperature, max_tokens,
  system prompt, messages, tools).
- call() maps text content from the response correctly.
- call() maps tool-use content blocks to tool_calls correctly.
- call() maps token usage correctly.
- call() raises LLMAuthError for SDK AuthenticationError.
- call() raises LLMTimeoutError for SDK APITimeoutError.
- call() raises LLMProviderError for other APIError.
- call_async() performs an equivalent async call (mocked).
- call_async() raises LLMTimeoutError on asyncio.TimeoutError.
- build_tool_definitions() produces correct dicts for known tool names.
- build_tool_definitions() handles unknown tool names gracefully.
- LLMResponse defaults are correct.
- TokenUsage.total_tokens sums correctly.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentry.llm import (
    AnthropicProvider,
    LLMAuthError,
    LLMClient,
    LLMConfig,
    LLMMessage,
    LLMProviderError,
    LLMResponse,
    LLMTimeoutError,
    LLMToolDefinition,
    TokenUsage,
    build_tool_definitions,
)
from agentry.llm.providers.anthropic import _get_api_key

# ---------------------------------------------------------------------------
# Helpers: build fake Anthropic SDK response objects
# ---------------------------------------------------------------------------


def _make_sdk_message(
    content_blocks: list[dict[str, Any]],
    *,
    model: str = "claude-sonnet-4-5",
    stop_reason: str = "end_turn",
    input_tokens: int = 10,
    output_tokens: int = 20,
) -> MagicMock:
    """Build a MagicMock that looks like an Anthropic SDK Message object."""
    msg = MagicMock()
    msg.model = model
    msg.stop_reason = stop_reason

    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens
    msg.usage = usage

    blocks = []
    for block_data in content_blocks:
        block = MagicMock()
        block.type = block_data["type"]
        if block_data["type"] == "text":
            block.text = block_data["text"]
        elif block_data["type"] == "tool_use":
            block.id = block_data["id"]
            block.name = block_data["name"]
            block.input = block_data["input"]
        blocks.append(block)

    msg.content = blocks
    return msg


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def api_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure ANTHROPIC_API_KEY is set to a dummy value for all tests."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")


@pytest.fixture()
def no_api_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure ANTHROPIC_API_KEY is absent from the environment."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture()
def provider(api_key_env: None) -> AnthropicProvider:
    """Return an AnthropicProvider with a dummy API key and mocked SDK clients."""
    with (
        patch("agentry.llm.providers.anthropic.anthropic.Anthropic"),
        patch("agentry.llm.providers.anthropic.anthropic.AsyncAnthropic"),
    ):
        return AnthropicProvider()


@pytest.fixture()
def base_config() -> LLMConfig:
    return LLMConfig(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        temperature=0.2,
    )


@pytest.fixture()
def single_user_message() -> list[LLMMessage]:
    return [LLMMessage(role="user", content="Review this diff.")]


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    """AnthropicProvider satisfies the LLMClient protocol."""

    def test_anthropic_provider_is_llm_client(self, provider: AnthropicProvider) -> None:
        assert isinstance(provider, LLMClient)

    def test_anthropic_provider_has_call(self, provider: AnthropicProvider) -> None:
        assert callable(provider.call)

    def test_anthropic_provider_has_call_async(self, provider: AnthropicProvider) -> None:
        assert callable(provider.call_async)


# ---------------------------------------------------------------------------
# API key handling
# ---------------------------------------------------------------------------


class TestApiKeyHandling:
    """API key reading and error messages."""

    def test_get_api_key_reads_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-my-key")
        assert _get_api_key() == "sk-ant-my-key"

    def test_get_api_key_raises_when_missing(self, no_api_key_env: None) -> None:
        with pytest.raises(LLMAuthError):
            _get_api_key()

    def test_get_api_key_error_mentions_env_var(self, no_api_key_env: None) -> None:
        with pytest.raises(LLMAuthError, match="ANTHROPIC_API_KEY"):
            _get_api_key()

    def test_get_api_key_error_has_suggestion(self, no_api_key_env: None) -> None:
        with pytest.raises(LLMAuthError) as exc_info:
            _get_api_key()
        assert exc_info.value.suggestion  # suggestion text is non-empty

    def test_provider_raises_at_construction_when_key_absent(
        self, no_api_key_env: None
    ) -> None:
        with pytest.raises(LLMAuthError):
            AnthropicProvider()

    def test_get_api_key_raises_for_empty_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
        with pytest.raises(LLMAuthError):
            _get_api_key()


# ---------------------------------------------------------------------------
# call(): parameter construction
# ---------------------------------------------------------------------------


class TestCallParameterConstruction:
    """call() builds the correct parameters for the Anthropic SDK."""

    def test_call_uses_correct_model(
        self,
        provider: AnthropicProvider,
        single_user_message: list[LLMMessage],
        base_config: LLMConfig,
    ) -> None:
        sdk_response = _make_sdk_message([{"type": "text", "text": "ok"}])
        provider._client.messages.create.return_value = sdk_response

        provider.call("system", single_user_message, [], base_config)

        _, kwargs = provider._client.messages.create.call_args
        assert kwargs["model"] == "claude-sonnet-4-5"

    def test_call_uses_correct_temperature(
        self,
        provider: AnthropicProvider,
        single_user_message: list[LLMMessage],
        base_config: LLMConfig,
    ) -> None:
        sdk_response = _make_sdk_message([{"type": "text", "text": "ok"}])
        provider._client.messages.create.return_value = sdk_response

        provider.call("system", single_user_message, [], base_config)

        _, kwargs = provider._client.messages.create.call_args
        assert kwargs["temperature"] == pytest.approx(0.2)

    def test_call_uses_correct_max_tokens(
        self,
        provider: AnthropicProvider,
        single_user_message: list[LLMMessage],
        base_config: LLMConfig,
    ) -> None:
        sdk_response = _make_sdk_message([{"type": "text", "text": "ok"}])
        provider._client.messages.create.return_value = sdk_response

        provider.call("system", single_user_message, [], base_config)

        _, kwargs = provider._client.messages.create.call_args
        assert kwargs["max_tokens"] == 1024

    def test_call_passes_system_prompt(
        self,
        provider: AnthropicProvider,
        single_user_message: list[LLMMessage],
        base_config: LLMConfig,
    ) -> None:
        sdk_response = _make_sdk_message([{"type": "text", "text": "ok"}])
        provider._client.messages.create.return_value = sdk_response

        provider.call("You are a code reviewer.", single_user_message, [], base_config)

        _, kwargs = provider._client.messages.create.call_args
        assert kwargs["system"] == "You are a code reviewer."

    def test_call_passes_messages(
        self,
        provider: AnthropicProvider,
        base_config: LLMConfig,
    ) -> None:
        sdk_response = _make_sdk_message([{"type": "text", "text": "ok"}])
        provider._client.messages.create.return_value = sdk_response

        messages = [
            LLMMessage(role="user", content="Hello"),
            LLMMessage(role="assistant", content="World"),
        ]
        provider.call("system", messages, [], base_config)

        _, kwargs = provider._client.messages.create.call_args
        assert kwargs["messages"] == [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "World"},
        ]

    def test_call_passes_tools(
        self,
        provider: AnthropicProvider,
        single_user_message: list[LLMMessage],
        base_config: LLMConfig,
    ) -> None:
        sdk_response = _make_sdk_message([{"type": "text", "text": "ok"}])
        provider._client.messages.create.return_value = sdk_response

        tools = [
            LLMToolDefinition(
                name="repository:read",
                description="Read a file",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            )
        ]
        provider.call("system", single_user_message, tools, base_config)

        _, kwargs = provider._client.messages.create.call_args
        assert "tools" in kwargs
        assert kwargs["tools"][0]["name"] == "repository:read"

    def test_call_omits_tools_key_when_no_tools(
        self,
        provider: AnthropicProvider,
        single_user_message: list[LLMMessage],
        base_config: LLMConfig,
    ) -> None:
        sdk_response = _make_sdk_message([{"type": "text", "text": "ok"}])
        provider._client.messages.create.return_value = sdk_response

        provider.call("system", single_user_message, [], base_config)

        _, kwargs = provider._client.messages.create.call_args
        assert "tools" not in kwargs

    def test_call_passes_timeout_when_set(
        self,
        provider: AnthropicProvider,
        single_user_message: list[LLMMessage],
    ) -> None:
        sdk_response = _make_sdk_message([{"type": "text", "text": "ok"}])
        provider._client.messages.create.return_value = sdk_response

        config = LLMConfig(model="claude-sonnet-4-5", max_tokens=1024, timeout=30.0)
        provider.call("system", single_user_message, [], config)

        _, kwargs = provider._client.messages.create.call_args
        assert kwargs.get("timeout") == pytest.approx(30.0)

    def test_call_omits_timeout_when_not_set(
        self,
        provider: AnthropicProvider,
        single_user_message: list[LLMMessage],
        base_config: LLMConfig,
    ) -> None:
        sdk_response = _make_sdk_message([{"type": "text", "text": "ok"}])
        provider._client.messages.create.return_value = sdk_response

        provider.call("system", single_user_message, [], base_config)

        _, kwargs = provider._client.messages.create.call_args
        assert "timeout" not in kwargs


# ---------------------------------------------------------------------------
# call(): response extraction
# ---------------------------------------------------------------------------


class TestCallResponseExtraction:
    """call() correctly extracts content, tool calls, and usage from the response."""

    def test_call_returns_text_content(
        self,
        provider: AnthropicProvider,
        single_user_message: list[LLMMessage],
        base_config: LLMConfig,
    ) -> None:
        sdk_response = _make_sdk_message(
            [{"type": "text", "text": "Here is my review."}]
        )
        provider._client.messages.create.return_value = sdk_response

        result = provider.call("system", single_user_message, [], base_config)

        assert isinstance(result, LLMResponse)
        assert result.content == "Here is my review."

    def test_call_returns_empty_content_on_tool_use_only(
        self,
        provider: AnthropicProvider,
        single_user_message: list[LLMMessage],
        base_config: LLMConfig,
    ) -> None:
        sdk_response = _make_sdk_message(
            [
                {
                    "type": "tool_use",
                    "id": "tu_01",
                    "name": "repository:read",
                    "input": {"path": "src/main.py"},
                }
            ]
        )
        provider._client.messages.create.return_value = sdk_response

        result = provider.call("system", single_user_message, [], base_config)

        assert result.content == ""

    def test_call_returns_tool_calls(
        self,
        provider: AnthropicProvider,
        single_user_message: list[LLMMessage],
        base_config: LLMConfig,
    ) -> None:
        sdk_response = _make_sdk_message(
            [
                {
                    "type": "tool_use",
                    "id": "tu_01",
                    "name": "repository:read",
                    "input": {"path": "src/main.py"},
                }
            ]
        )
        provider._client.messages.create.return_value = sdk_response

        result = provider.call("system", single_user_message, [], base_config)

        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["name"] == "repository:read"
        assert result.tool_calls[0]["input"] == {"path": "src/main.py"}

    def test_call_returns_token_usage(
        self,
        provider: AnthropicProvider,
        single_user_message: list[LLMMessage],
        base_config: LLMConfig,
    ) -> None:
        sdk_response = _make_sdk_message(
            [{"type": "text", "text": "ok"}],
            input_tokens=42,
            output_tokens=17,
        )
        provider._client.messages.create.return_value = sdk_response

        result = provider.call("system", single_user_message, [], base_config)

        assert result.usage.input_tokens == 42
        assert result.usage.output_tokens == 17

    def test_call_returns_model_name(
        self,
        provider: AnthropicProvider,
        single_user_message: list[LLMMessage],
        base_config: LLMConfig,
    ) -> None:
        sdk_response = _make_sdk_message(
            [{"type": "text", "text": "ok"}],
            model="claude-sonnet-4-5",
        )
        provider._client.messages.create.return_value = sdk_response

        result = provider.call("system", single_user_message, [], base_config)

        assert result.model == "claude-sonnet-4-5"

    def test_call_returns_stop_reason(
        self,
        provider: AnthropicProvider,
        single_user_message: list[LLMMessage],
        base_config: LLMConfig,
    ) -> None:
        sdk_response = _make_sdk_message(
            [{"type": "text", "text": "ok"}],
            stop_reason="tool_use",
        )
        provider._client.messages.create.return_value = sdk_response

        result = provider.call("system", single_user_message, [], base_config)

        assert result.stop_reason == "tool_use"

    def test_call_concatenates_multiple_text_blocks(
        self,
        provider: AnthropicProvider,
        single_user_message: list[LLMMessage],
        base_config: LLMConfig,
    ) -> None:
        sdk_response = _make_sdk_message(
            [
                {"type": "text", "text": "Hello "},
                {"type": "text", "text": "world"},
            ]
        )
        provider._client.messages.create.return_value = sdk_response

        result = provider.call("system", single_user_message, [], base_config)

        assert result.content == "Hello world"


# ---------------------------------------------------------------------------
# call(): error handling
# ---------------------------------------------------------------------------


class TestCallErrorHandling:
    """call() converts SDK exceptions to agentry LLM exceptions."""

    def test_call_raises_llm_auth_error_on_authentication_error(
        self,
        provider: AnthropicProvider,
        single_user_message: list[LLMMessage],
        base_config: LLMConfig,
    ) -> None:
        import anthropic as sdk

        provider._client.messages.create.side_effect = sdk.AuthenticationError(
            message="Invalid API key",
            response=MagicMock(status_code=401),
            body={},
        )

        with pytest.raises(LLMAuthError):
            provider.call("system", single_user_message, [], base_config)

    def test_call_raises_llm_timeout_error_on_api_timeout(
        self,
        provider: AnthropicProvider,
        single_user_message: list[LLMMessage],
    ) -> None:
        import anthropic as sdk

        provider._client.messages.create.side_effect = sdk.APITimeoutError(
            request=MagicMock()
        )
        config = LLMConfig(model="claude-sonnet-4-5", max_tokens=1024, timeout=5.0)

        with pytest.raises(LLMTimeoutError):
            provider.call("system", single_user_message, [], config)

    def test_call_raises_llm_provider_error_on_generic_api_error(
        self,
        provider: AnthropicProvider,
        single_user_message: list[LLMMessage],
        base_config: LLMConfig,
    ) -> None:
        import anthropic as sdk

        provider._client.messages.create.side_effect = sdk.InternalServerError(
            message="Server error",
            response=MagicMock(status_code=500),
            body={},
        )

        with pytest.raises(LLMProviderError):
            provider.call("system", single_user_message, [], base_config)


# ---------------------------------------------------------------------------
# call_async(): basic behavior
# ---------------------------------------------------------------------------


class TestCallAsync:
    """call_async() performs an equivalent async call."""

    def test_call_async_returns_llm_response(
        self,
        provider: AnthropicProvider,
        single_user_message: list[LLMMessage],
        base_config: LLMConfig,
    ) -> None:
        sdk_response = _make_sdk_message(
            [{"type": "text", "text": "async review"}]
        )
        provider._async_client.messages.create = AsyncMock(return_value=sdk_response)

        result = asyncio.run(
            provider.call_async("system", single_user_message, [], base_config)
        )

        assert isinstance(result, LLMResponse)
        assert result.content == "async review"

    def test_call_async_passes_system_prompt(
        self,
        provider: AnthropicProvider,
        single_user_message: list[LLMMessage],
        base_config: LLMConfig,
    ) -> None:
        sdk_response = _make_sdk_message([{"type": "text", "text": "ok"}])
        provider._async_client.messages.create = AsyncMock(return_value=sdk_response)

        asyncio.run(
            provider.call_async("My system prompt", single_user_message, [], base_config)
        )

        _, kwargs = provider._async_client.messages.create.call_args
        assert kwargs["system"] == "My system prompt"

    def test_call_async_raises_llm_timeout_on_asyncio_timeout(
        self,
        provider: AnthropicProvider,
        single_user_message: list[LLMMessage],
    ) -> None:
        provider._async_client.messages.create = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )
        config = LLMConfig(model="claude-sonnet-4-5", max_tokens=1024, timeout=1.0)

        with pytest.raises(LLMTimeoutError):
            asyncio.run(
                provider.call_async("system", single_user_message, [], config)
            )


# ---------------------------------------------------------------------------
# build_tool_definitions
# ---------------------------------------------------------------------------


class TestBuildToolDefinitions:
    """build_tool_definitions() produces correct Anthropic tool dicts."""

    def test_repository_read_tool_definition(self) -> None:
        defs = build_tool_definitions(["repository:read"])
        assert len(defs) == 1
        td = defs[0]
        assert td["name"] == "repository__read"
        assert "description" in td
        assert td["input_schema"]["type"] == "object"
        assert "path" in td["input_schema"]["properties"]

    def test_shell_execute_tool_definition(self) -> None:
        defs = build_tool_definitions(["shell:execute"])
        assert len(defs) == 1
        td = defs[0]
        assert td["name"] == "shell__execute"
        assert "command" in td["input_schema"]["properties"]

    def test_both_tools_returned(self) -> None:
        defs = build_tool_definitions(["repository:read", "shell:execute"])
        names = [d["name"] for d in defs]
        assert "repository__read" in names
        assert "shell__execute" in names

    def test_unknown_tool_produces_passthrough_definition(self) -> None:
        defs = build_tool_definitions(["custom:tool"])
        assert len(defs) == 1
        assert defs[0]["name"] == "custom__tool"

    def test_empty_tool_list_returns_empty(self) -> None:
        defs = build_tool_definitions([])
        assert defs == []


# ---------------------------------------------------------------------------
# LLMResponse defaults
# ---------------------------------------------------------------------------


class TestLLMResponseDefaults:
    """LLMResponse has sensible defaults for optional fields."""

    def test_default_tool_calls_is_empty_list(self) -> None:
        response = LLMResponse(content="hello")
        assert response.tool_calls == []

    def test_default_model_is_empty_string(self) -> None:
        response = LLMResponse(content="hello")
        assert response.model == ""

    def test_default_stop_reason_is_empty_string(self) -> None:
        response = LLMResponse(content="hello")
        assert response.stop_reason == ""


# ---------------------------------------------------------------------------
# TokenUsage
# ---------------------------------------------------------------------------


class TestTokenUsage:
    """TokenUsage totals and attributes."""

    def test_total_tokens_sums_correctly(self) -> None:
        usage = TokenUsage(input_tokens=100, output_tokens=50)
        assert usage.total_tokens == 150

    def test_zero_usage(self) -> None:
        usage = TokenUsage(input_tokens=0, output_tokens=0)
        assert usage.total_tokens == 0
