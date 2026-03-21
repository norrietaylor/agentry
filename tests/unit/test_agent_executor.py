"""Unit tests for T04.2: Agent executor with retry, timeout, and execution recording.

All LLM calls are mocked -- no network traffic is made.

Tests cover:
- format_inputs_as_messages() produces labelled user messages.
- AgentExecutor.run() sends correct parameters to LLM client.
- Multi-turn conversation loop with tool invocations.
- Retry logic with exponential backoff on transient failures.
- Execution timeout enforcement.
- Token usage accumulation across multiple LLM calls.
- Wall-clock timing recording.
- Tool invocation recording with timing.
- ExecutionRecord serialization via to_dict().
- Structured JSON output parsing from LLM content.
- JSON code fence extraction.
- Default tool handler returns error for unbound tools.
- _is_retryable() distinguishes transient from permanent errors.
- _compute_backoff_delay() exponential and fallback strategies.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from agentry.executor import (
    AgentExecutor,
    ExecutionRecord,
    RetryAttempt,
    ToolInvocation,
    _compute_backoff_delay,
    _default_tool_handler,
    _is_retryable,
    _try_parse_json,
    format_inputs_as_messages,
)
from agentry.llm.exceptions import LLMAuthError, LLMProviderError, LLMTimeoutError
from agentry.llm.models import LLMConfig, LLMResponse, TokenUsage
from agentry.models.model import RetryConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(
    content: str = "ok",
    tool_calls: list[dict[str, Any]] | None = None,
    input_tokens: int = 10,
    output_tokens: int = 20,
    model: str = "claude-sonnet-4-5",
    stop_reason: str = "end_turn",
) -> LLMResponse:
    """Build a mock LLMResponse."""
    return LLMResponse(
        content=content,
        tool_calls=tool_calls or [],
        usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        model=model,
        stop_reason=stop_reason,
    )


def _make_tool_response(
    tool_name: str = "repository__read",
    tool_input: dict[str, Any] | None = None,
    tool_id: str = "tu_01",
) -> LLMResponse:
    """Build an LLMResponse with a tool call."""
    return LLMResponse(
        content="",
        tool_calls=[
            {
                "id": tool_id,
                "name": tool_name,
                "input": tool_input or {"path": "src/main.py"},
            }
        ],
        usage=TokenUsage(input_tokens=15, output_tokens=5),
        model="claude-sonnet-4-5",
        stop_reason="tool_use",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_client() -> MagicMock:
    """Return a mock LLM client."""
    client = MagicMock()
    client.call.return_value = _make_response()
    return client


@pytest.fixture()
def base_config() -> LLMConfig:
    return LLMConfig(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        temperature=0.2,
    )


@pytest.fixture()
def no_sleep() -> MagicMock:
    """A mock sleep function that does nothing."""
    return MagicMock()


# ---------------------------------------------------------------------------
# format_inputs_as_messages
# ---------------------------------------------------------------------------


class TestFormatInputsAsMessages:
    """format_inputs_as_messages() formats resolved inputs as user messages."""

    def test_single_input(self) -> None:
        messages = format_inputs_as_messages({"diff": "--- a/file\n+++ b/file"})
        assert len(messages) == 1
        assert messages[0].role == "user"
        assert "[diff]" in messages[0].content
        assert "--- a/file" in messages[0].content

    def test_multiple_inputs(self) -> None:
        messages = format_inputs_as_messages(
            {"diff": "diff content", "repo": "/path/to/repo"}
        )
        assert len(messages) == 1
        assert "[diff]" in messages[0].content
        assert "[repo]" in messages[0].content

    def test_empty_inputs(self) -> None:
        messages = format_inputs_as_messages({})
        assert messages == []

    def test_inputs_combined_in_single_message(self) -> None:
        messages = format_inputs_as_messages({"a": "1", "b": "2"})
        assert len(messages) == 1


# ---------------------------------------------------------------------------
# AgentExecutor.run(): basic execution
# ---------------------------------------------------------------------------


class TestAgentExecutorBasicExecution:
    """AgentExecutor.run() sends correct parameters and records results."""

    def test_run_calls_llm_client(
        self, mock_client: MagicMock, base_config: LLMConfig
    ) -> None:
        executor = AgentExecutor(llm_client=mock_client)
        executor.run(
            system_prompt="You are a reviewer.",
            resolved_inputs={"diff": "some diff"},
            tool_names=[],
            config=base_config,
        )
        mock_client.call.assert_called_once()

    def test_run_passes_system_prompt(
        self, mock_client: MagicMock, base_config: LLMConfig
    ) -> None:
        executor = AgentExecutor(llm_client=mock_client)
        executor.run(
            system_prompt="You are a reviewer.",
            resolved_inputs={"diff": "some diff"},
            tool_names=[],
            config=base_config,
        )
        _, kwargs = mock_client.call.call_args
        assert kwargs["system_prompt"] == "You are a reviewer."

    def test_run_passes_formatted_messages(
        self, mock_client: MagicMock, base_config: LLMConfig
    ) -> None:
        executor = AgentExecutor(llm_client=mock_client)
        executor.run(
            system_prompt="sys",
            resolved_inputs={"diff": "some diff"},
            tool_names=[],
            config=base_config,
        )
        _, kwargs = mock_client.call.call_args
        messages = kwargs["messages"]
        assert len(messages) == 1
        assert messages[0].role == "user"
        assert "[diff]" in messages[0].content

    def test_run_passes_tool_definitions(
        self, mock_client: MagicMock, base_config: LLMConfig
    ) -> None:
        executor = AgentExecutor(llm_client=mock_client)
        executor.run(
            system_prompt="sys",
            resolved_inputs={"diff": "d"},
            tool_names=["repository:read"],
            config=base_config,
        )
        _, kwargs = mock_client.call.call_args
        tools = kwargs["tools"]
        assert len(tools) == 1
        assert tools[0]["name"] == "repository__read"

    def test_run_returns_execution_record(
        self, mock_client: MagicMock, base_config: LLMConfig
    ) -> None:
        executor = AgentExecutor(llm_client=mock_client)
        record = executor.run(
            system_prompt="sys",
            resolved_inputs={"diff": "d"},
            tool_names=[],
            config=base_config,
        )
        assert isinstance(record, ExecutionRecord)

    def test_run_records_final_content(
        self, mock_client: MagicMock, base_config: LLMConfig
    ) -> None:
        mock_client.call.return_value = _make_response(content="Review complete.")
        executor = AgentExecutor(llm_client=mock_client)
        record = executor.run(
            system_prompt="sys",
            resolved_inputs={"diff": "d"},
            tool_names=[],
            config=base_config,
        )
        assert record.final_content == "Review complete."

    def test_run_records_model_used(
        self, mock_client: MagicMock, base_config: LLMConfig
    ) -> None:
        mock_client.call.return_value = _make_response(model="claude-sonnet-4-5")
        executor = AgentExecutor(llm_client=mock_client)
        record = executor.run(
            system_prompt="sys",
            resolved_inputs={"diff": "d"},
            tool_names=[],
            config=base_config,
        )
        assert record.model_used == "claude-sonnet-4-5"

    def test_run_records_stop_reason(
        self, mock_client: MagicMock, base_config: LLMConfig
    ) -> None:
        mock_client.call.return_value = _make_response(stop_reason="end_turn")
        executor = AgentExecutor(llm_client=mock_client)
        record = executor.run(
            system_prompt="sys",
            resolved_inputs={"diff": "d"},
            tool_names=[],
            config=base_config,
        )
        assert record.stop_reason == "end_turn"


# ---------------------------------------------------------------------------
# Token usage recording
# ---------------------------------------------------------------------------


class TestTokenUsageRecording:
    """Token usage is accumulated across LLM calls."""

    def test_single_call_token_usage(
        self, mock_client: MagicMock, base_config: LLMConfig
    ) -> None:
        mock_client.call.return_value = _make_response(
            input_tokens=42, output_tokens=17
        )
        executor = AgentExecutor(llm_client=mock_client)
        record = executor.run(
            system_prompt="sys",
            resolved_inputs={"diff": "d"},
            tool_names=[],
            config=base_config,
        )
        assert record.input_tokens == 42
        assert record.output_tokens == 17
        assert record.total_tokens == 59

    def test_multi_turn_token_accumulation(
        self, mock_client: MagicMock, base_config: LLMConfig
    ) -> None:
        """Token usage is summed across tool-use turns."""
        tool_response = _make_tool_response()
        final_response = _make_response(
            content="Done.", input_tokens=20, output_tokens=30
        )
        mock_client.call.side_effect = [tool_response, final_response]

        executor = AgentExecutor(
            llm_client=mock_client,
            tool_handler=lambda name, inp: "file content",
        )
        record = executor.run(
            system_prompt="sys",
            resolved_inputs={"diff": "d"},
            tool_names=["repository:read"],
            config=base_config,
        )
        # tool_response: 15 + 5, final_response: 20 + 30
        assert record.input_tokens == 35
        assert record.output_tokens == 35
        assert record.total_llm_calls == 2


# ---------------------------------------------------------------------------
# Wall-clock timing
# ---------------------------------------------------------------------------


class TestWallClockTiming:
    """Wall-clock start/end timestamps are recorded."""

    def test_wall_clock_timestamps_are_set(
        self, mock_client: MagicMock, base_config: LLMConfig
    ) -> None:
        executor = AgentExecutor(llm_client=mock_client)
        before = time.time()
        record = executor.run(
            system_prompt="sys",
            resolved_inputs={"diff": "d"},
            tool_names=[],
            config=base_config,
        )
        after = time.time()

        assert record.wall_clock_start >= before
        assert record.wall_clock_end <= after
        assert record.wall_clock_end >= record.wall_clock_start

    def test_wall_clock_seconds_computed(
        self, mock_client: MagicMock, base_config: LLMConfig
    ) -> None:
        executor = AgentExecutor(llm_client=mock_client)
        record = executor.run(
            system_prompt="sys",
            resolved_inputs={"diff": "d"},
            tool_names=[],
            config=base_config,
        )
        assert record.wall_clock_seconds >= 0.0


# ---------------------------------------------------------------------------
# Tool invocation handling
# ---------------------------------------------------------------------------


class TestToolInvocationHandling:
    """Multi-turn tool invocation loop and recording."""

    def test_tool_invocation_recorded(
        self, mock_client: MagicMock, base_config: LLMConfig
    ) -> None:
        tool_response = _make_tool_response(
            tool_name="repository__read",
            tool_input={"path": "src/main.py"},
        )
        final_response = _make_response(content="Review done.")
        mock_client.call.side_effect = [tool_response, final_response]

        handler = MagicMock(return_value="file content here")
        executor = AgentExecutor(llm_client=mock_client, tool_handler=handler)
        record = executor.run(
            system_prompt="sys",
            resolved_inputs={"diff": "d"},
            tool_names=["repository:read"],
            config=base_config,
        )

        assert len(record.tool_invocations) == 1
        inv = record.tool_invocations[0]
        assert inv.tool_name == "repository:read"
        assert inv.tool_input == {"path": "src/main.py"}
        assert inv.tool_output == "file content here"
        assert inv.duration_ms >= 0

    def test_tool_handler_called_with_original_name(
        self, mock_client: MagicMock, base_config: LLMConfig
    ) -> None:
        """Tool name is mapped back from internal (__) to original (:) format."""
        tool_response = _make_tool_response(tool_name="shell__execute")
        final_response = _make_response()
        mock_client.call.side_effect = [tool_response, final_response]

        handler = MagicMock(return_value="output")
        executor = AgentExecutor(llm_client=mock_client, tool_handler=handler)
        executor.run(
            system_prompt="sys",
            resolved_inputs={"diff": "d"},
            tool_names=["shell:execute"],
            config=base_config,
        )
        handler.assert_called_once()
        assert handler.call_args[0][0] == "shell:execute"

    def test_tool_result_sent_back_to_llm(
        self, mock_client: MagicMock, base_config: LLMConfig
    ) -> None:
        tool_response = _make_tool_response()
        final_response = _make_response()
        mock_client.call.side_effect = [tool_response, final_response]

        executor = AgentExecutor(
            llm_client=mock_client,
            tool_handler=lambda name, inp: "tool output",
        )
        executor.run(
            system_prompt="sys",
            resolved_inputs={"diff": "d"},
            tool_names=["repository:read"],
            config=base_config,
        )

        # Second call should include tool result in messages.
        second_call = mock_client.call.call_args_list[1]
        messages = second_call[1]["messages"]
        # Last message should contain tool result.
        assert "tool output" in messages[-1].content

    def test_tool_handler_exception_captured(
        self, mock_client: MagicMock, base_config: LLMConfig
    ) -> None:
        tool_response = _make_tool_response()
        final_response = _make_response()
        mock_client.call.side_effect = [tool_response, final_response]

        def bad_handler(name: str, inp: dict[str, Any]) -> str:
            raise RuntimeError("tool broke")

        executor = AgentExecutor(llm_client=mock_client, tool_handler=bad_handler)
        record = executor.run(
            system_prompt="sys",
            resolved_inputs={"diff": "d"},
            tool_names=["repository:read"],
            config=base_config,
        )
        assert len(record.tool_invocations) == 1
        assert "Error" in record.tool_invocations[0].tool_output
        assert "tool broke" in record.tool_invocations[0].tool_output

    def test_multiple_tool_calls_in_single_turn(
        self, mock_client: MagicMock, base_config: LLMConfig
    ) -> None:
        """Multiple tool calls in a single response are all handled."""
        multi_tool_response = LLMResponse(
            content="",
            tool_calls=[
                {"id": "tu_01", "name": "repository__read", "input": {"path": "a.py"}},
                {"id": "tu_02", "name": "repository__read", "input": {"path": "b.py"}},
            ],
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            model="claude-sonnet-4-5",
            stop_reason="tool_use",
        )
        final_response = _make_response(content="Both files reviewed.")
        mock_client.call.side_effect = [multi_tool_response, final_response]

        handler = MagicMock(return_value="content")
        executor = AgentExecutor(llm_client=mock_client, tool_handler=handler)
        record = executor.run(
            system_prompt="sys",
            resolved_inputs={"diff": "d"},
            tool_names=["repository:read"],
            config=base_config,
        )
        assert len(record.tool_invocations) == 2
        assert handler.call_count == 2


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------


class TestRetryLogic:
    """Retry logic with exponential backoff on transient failures."""

    def test_retry_on_transient_error(
        self, mock_client: MagicMock, base_config: LLMConfig, no_sleep: MagicMock
    ) -> None:
        """Retries on server error and succeeds on second attempt."""
        mock_client.call.side_effect = [
            LLMProviderError("Server error", status_code=500),
            _make_response(content="Success"),
        ]
        retry = RetryConfig(max_attempts=3, backoff="exponential")

        executor = AgentExecutor(
            llm_client=mock_client, sleep_func=no_sleep
        )
        record = executor.run(
            system_prompt="sys",
            resolved_inputs={"diff": "d"},
            tool_names=[],
            config=base_config,
            retry_config=retry,
        )

        assert record.final_content == "Success"
        assert len(record.retry_attempts) == 1
        assert record.error == ""

    def test_retry_respects_max_attempts(
        self, mock_client: MagicMock, base_config: LLMConfig, no_sleep: MagicMock
    ) -> None:
        """Gives up after max_attempts."""
        mock_client.call.side_effect = LLMProviderError(
            "Server error", status_code=500
        )
        retry = RetryConfig(max_attempts=3, backoff="exponential")

        executor = AgentExecutor(
            llm_client=mock_client, sleep_func=no_sleep
        )
        record = executor.run(
            system_prompt="sys",
            resolved_inputs={"diff": "d"},
            tool_names=[],
            config=base_config,
            retry_config=retry,
        )

        assert record.error != ""
        assert "Server error" in record.error
        assert len(record.retry_attempts) == 2  # 2 retries before final failure

    def test_no_retry_on_auth_error(
        self, mock_client: MagicMock, base_config: LLMConfig, no_sleep: MagicMock
    ) -> None:
        """Auth errors are not retried."""
        mock_client.call.side_effect = LLMAuthError("Bad key")
        retry = RetryConfig(max_attempts=3, backoff="exponential")

        executor = AgentExecutor(
            llm_client=mock_client, sleep_func=no_sleep
        )
        record = executor.run(
            system_prompt="sys",
            resolved_inputs={"diff": "d"},
            tool_names=[],
            config=base_config,
            retry_config=retry,
        )

        assert record.error != ""
        assert mock_client.call.call_count == 1

    def test_exponential_backoff_delays(
        self, mock_client: MagicMock, base_config: LLMConfig, no_sleep: MagicMock
    ) -> None:
        """Retry delays follow exponential backoff pattern."""
        mock_client.call.side_effect = [
            LLMProviderError("Error", status_code=500),
            LLMProviderError("Error", status_code=500),
            _make_response(content="ok"),
        ]
        retry = RetryConfig(max_attempts=3, backoff="exponential")

        executor = AgentExecutor(
            llm_client=mock_client, sleep_func=no_sleep
        )
        record = executor.run(
            system_prompt="sys",
            resolved_inputs={"diff": "d"},
            tool_names=[],
            config=base_config,
            retry_config=retry,
        )

        assert len(record.retry_attempts) == 2
        # Exponential: 2^0=1, 2^1=2
        assert record.retry_attempts[0].delay_seconds == 1.0
        assert record.retry_attempts[1].delay_seconds == 2.0

    def test_retry_records_attempt_info(
        self, mock_client: MagicMock, base_config: LLMConfig, no_sleep: MagicMock
    ) -> None:
        mock_client.call.side_effect = [
            LLMProviderError("Err", status_code=500),
            _make_response(),
        ]
        retry = RetryConfig(max_attempts=2, backoff="exponential")

        executor = AgentExecutor(
            llm_client=mock_client, sleep_func=no_sleep
        )
        record = executor.run(
            system_prompt="sys",
            resolved_inputs={"diff": "d"},
            tool_names=[],
            config=base_config,
            retry_config=retry,
        )

        assert len(record.retry_attempts) == 1
        att = record.retry_attempts[0]
        assert att.attempt == 1
        assert "Err" in att.error
        assert att.timestamp > 0

    def test_retry_on_rate_limit(
        self, mock_client: MagicMock, base_config: LLMConfig, no_sleep: MagicMock
    ) -> None:
        """429 rate limit errors are retried."""
        mock_client.call.side_effect = [
            LLMProviderError("Rate limited", status_code=429),
            _make_response(content="ok"),
        ]
        retry = RetryConfig(max_attempts=3, backoff="exponential")

        executor = AgentExecutor(
            llm_client=mock_client, sleep_func=no_sleep
        )
        record = executor.run(
            system_prompt="sys",
            resolved_inputs={"diff": "d"},
            tool_names=[],
            config=base_config,
            retry_config=retry,
        )

        assert record.final_content == "ok"
        assert len(record.retry_attempts) == 1


# ---------------------------------------------------------------------------
# Timeout enforcement
# ---------------------------------------------------------------------------


class TestTimeoutEnforcement:
    """Execution timeout enforcement."""

    def test_timeout_error_from_llm_recorded(
        self, mock_client: MagicMock, base_config: LLMConfig
    ) -> None:
        """LLM timeout errors are captured in the record."""
        mock_client.call.side_effect = LLMTimeoutError(5.0)

        executor = AgentExecutor(llm_client=mock_client)
        record = executor.run(
            system_prompt="sys",
            resolved_inputs={"diff": "d"},
            tool_names=[],
            config=base_config,
            timeout=5.0,
        )

        assert record.timed_out is True
        assert "timed out" in record.error.lower()

    def test_timeout_sets_config_timeout(
        self, mock_client: MagicMock, base_config: LLMConfig
    ) -> None:
        """Timeout value is propagated to LLMConfig."""
        executor = AgentExecutor(llm_client=mock_client)
        executor.run(
            system_prompt="sys",
            resolved_inputs={"diff": "d"},
            tool_names=[],
            config=base_config,
            timeout=30.0,
        )

        _, kwargs = mock_client.call.call_args
        assert kwargs["config"].timeout == 30.0


# ---------------------------------------------------------------------------
# ExecutionRecord serialization
# ---------------------------------------------------------------------------


class TestExecutionRecordSerialization:
    """ExecutionRecord.to_dict() produces JSON-serializable output."""

    def test_to_dict_contains_token_fields(self) -> None:
        record = ExecutionRecord(input_tokens=100, output_tokens=50)
        d = record.to_dict()
        assert d["input_tokens"] == 100
        assert d["output_tokens"] == 50
        assert d["total_tokens"] == 150

    def test_to_dict_contains_wall_clock_timing(self) -> None:
        record = ExecutionRecord(
            wall_clock_start=1000.0,
            wall_clock_end=1005.0,
        )
        d = record.to_dict()
        assert d["wall_clock_timing"]["start"] == 1000.0
        assert d["wall_clock_timing"]["end"] == 1005.0
        assert d["wall_clock_timing"]["duration_seconds"] == pytest.approx(5.0)

    def test_to_dict_contains_tool_invocations(self) -> None:
        record = ExecutionRecord(
            tool_invocations=[
                ToolInvocation(
                    tool_name="repository:read",
                    tool_input={"path": "a.py"},
                    tool_output="content",
                    timestamp=1000.0,
                    duration_ms=5.0,
                )
            ]
        )
        d = record.to_dict()
        assert len(d["tool_invocations"]) == 1
        assert d["tool_invocations"][0]["tool_name"] == "repository:read"

    def test_to_dict_contains_retry_attempts(self) -> None:
        record = ExecutionRecord(
            retry_attempts=[
                RetryAttempt(
                    attempt=1, error="Server error", delay_seconds=1.0, timestamp=1000.0
                )
            ]
        )
        d = record.to_dict()
        assert len(d["retry_attempts"]) == 1
        assert d["retry_attempts"][0]["attempt"] == 1

    def test_to_dict_contains_metadata(self) -> None:
        record = ExecutionRecord(
            model_used="claude-sonnet-4-5",
            total_llm_calls=3,
            timed_out=False,
            error="",
        )
        d = record.to_dict()
        assert d["model_used"] == "claude-sonnet-4-5"
        assert d["total_llm_calls"] == 3
        assert d["timed_out"] is False
        assert d["error"] == ""


# ---------------------------------------------------------------------------
# JSON output parsing
# ---------------------------------------------------------------------------


class TestJsonOutputParsing:
    """Structured JSON output parsing from LLM content."""

    def test_plain_json_parsed(self) -> None:
        result = _try_parse_json('{"severity": "high", "findings": []}')
        assert result == {"severity": "high", "findings": []}

    def test_json_code_fence_parsed(self) -> None:
        content = '```json\n{"severity": "high"}\n```'
        result = _try_parse_json(content)
        assert result == {"severity": "high"}

    def test_generic_code_fence_parsed(self) -> None:
        content = '```\n{"severity": "high"}\n```'
        result = _try_parse_json(content)
        assert result == {"severity": "high"}

    def test_non_json_returns_none(self) -> None:
        result = _try_parse_json("This is just plain text.")
        assert result is None

    def test_non_dict_json_returns_none(self) -> None:
        result = _try_parse_json("[1, 2, 3]")
        assert result is None

    def test_run_stores_parsed_output(
        self, mock_client: MagicMock, base_config: LLMConfig
    ) -> None:
        mock_client.call.return_value = _make_response(
            content='{"summary": "All good", "findings": []}'
        )
        executor = AgentExecutor(llm_client=mock_client)
        record = executor.run(
            system_prompt="sys",
            resolved_inputs={"diff": "d"},
            tool_names=[],
            config=base_config,
        )
        assert record.final_output == {"summary": "All good", "findings": []}

    def test_run_non_json_output_is_none(
        self, mock_client: MagicMock, base_config: LLMConfig
    ) -> None:
        mock_client.call.return_value = _make_response(
            content="Just a text review."
        )
        executor = AgentExecutor(llm_client=mock_client)
        record = executor.run(
            system_prompt="sys",
            resolved_inputs={"diff": "d"},
            tool_names=[],
            config=base_config,
        )
        assert record.final_output is None


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestHelperFunctions:
    """Tests for module-level helper functions."""

    def test_default_tool_handler_returns_error(self) -> None:
        result = _default_tool_handler("unknown:tool", {})
        assert "not bound" in result.lower() or "Error" in result

    def test_is_retryable_timeout_error(self) -> None:
        assert _is_retryable(LLMTimeoutError(5.0)) is True

    def test_is_retryable_server_error(self) -> None:
        assert _is_retryable(LLMProviderError("err", status_code=500)) is True

    def test_is_retryable_rate_limit(self) -> None:
        assert _is_retryable(LLMProviderError("rate limit", status_code=429)) is True

    def test_is_not_retryable_auth_error(self) -> None:
        assert _is_retryable(LLMAuthError("bad key")) is False

    def test_is_not_retryable_client_error(self) -> None:
        assert _is_retryable(LLMProviderError("bad request", status_code=400)) is False

    def test_compute_backoff_exponential(self) -> None:
        assert _compute_backoff_delay(1, "exponential") == 1.0
        assert _compute_backoff_delay(2, "exponential") == 2.0
        assert _compute_backoff_delay(3, "exponential") == 4.0

    def test_compute_backoff_unknown_strategy(self) -> None:
        assert _compute_backoff_delay(1, "linear") == 1.0
        assert _compute_backoff_delay(5, "unknown") == 1.0


# ---------------------------------------------------------------------------
# ExecutionRecord defaults
# ---------------------------------------------------------------------------


class TestExecutionRecordDefaults:
    """ExecutionRecord has sensible defaults."""

    def test_default_tokens_are_zero(self) -> None:
        record = ExecutionRecord()
        assert record.input_tokens == 0
        assert record.output_tokens == 0
        assert record.total_tokens == 0

    def test_default_lists_are_empty(self) -> None:
        record = ExecutionRecord()
        assert record.tool_invocations == []
        assert record.retry_attempts == []

    def test_default_error_is_empty(self) -> None:
        record = ExecutionRecord()
        assert record.error == ""
        assert record.timed_out is False

    def test_default_final_output_is_none(self) -> None:
        record = ExecutionRecord()
        assert record.final_output is None


# ---------------------------------------------------------------------------
# Error handling in run()
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Non-retryable errors are captured in the record."""

    def test_auth_error_captured_in_record(
        self, mock_client: MagicMock, base_config: LLMConfig
    ) -> None:
        mock_client.call.side_effect = LLMAuthError("Invalid key")
        executor = AgentExecutor(llm_client=mock_client)
        record = executor.run(
            system_prompt="sys",
            resolved_inputs={"diff": "d"},
            tool_names=[],
            config=base_config,
        )
        assert "Invalid key" in record.error
        assert record.wall_clock_end > 0

    def test_unexpected_error_captured_in_record(
        self, mock_client: MagicMock, base_config: LLMConfig
    ) -> None:
        mock_client.call.side_effect = RuntimeError("Unexpected")
        executor = AgentExecutor(llm_client=mock_client)
        record = executor.run(
            system_prompt="sys",
            resolved_inputs={"diff": "d"},
            tool_names=[],
            config=base_config,
        )
        assert "Unexpected" in record.error


# ---------------------------------------------------------------------------
# No retry config defaults to single attempt
# ---------------------------------------------------------------------------


class TestDefaultRetryConfig:
    """When no retry config is provided, defaults to max_attempts=3."""

    def test_default_retry_allows_retries(
        self, mock_client: MagicMock, base_config: LLMConfig, no_sleep: MagicMock
    ) -> None:
        mock_client.call.side_effect = [
            LLMProviderError("Error", status_code=500),
            _make_response(content="ok"),
        ]
        executor = AgentExecutor(
            llm_client=mock_client, sleep_func=no_sleep
        )
        record = executor.run(
            system_prompt="sys",
            resolved_inputs={"diff": "d"},
            tool_names=[],
            config=base_config,
        )
        assert record.final_content == "ok"
