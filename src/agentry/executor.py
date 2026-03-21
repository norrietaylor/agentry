"""Agent execution engine.

Orchestrates the full agent execution flow: formats resolved inputs as user
messages, binds declared tools as Claude tool-use definitions, sends to the
LLM, handles tool invocation callbacks, collects structured output. Implements
retry logic with exponential backoff and per-execution timeout enforcement.
Records token usage, wall-clock timing, and tool invocations in an execution
record.

Usage::

    from agentry.executor import AgentExecutor, ExecutionRecord

    executor = AgentExecutor(llm_client=provider)
    record = executor.run(
        system_prompt="You are a code reviewer.",
        resolved_inputs={"diff": "...", "repo": "/path"},
        tool_names=["repository:read", "shell:execute"],
        config=llm_config,
        retry_config=retry_config,
        timeout=300,
    )
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agentry.llm.exceptions import LLMProviderError, LLMTimeoutError
from agentry.llm.models import LLMConfig, LLMMessage, LLMResponse, TokenUsage
from agentry.llm.protocol import LLMClient, build_tool_definitions
from agentry.models.model import RetryConfig


@dataclass
class ToolInvocation:
    """Record of a single tool invocation during execution.

    Attributes:
        tool_name: The tool that was invoked (e.g. ``"repository:read"``).
        tool_input: The input parameters passed to the tool.
        tool_output: The output returned by the tool handler, or error text.
        timestamp: Wall-clock time of the invocation (seconds since epoch).
        duration_ms: How long the invocation took in milliseconds.
    """

    tool_name: str
    tool_input: dict[str, Any]
    tool_output: str
    timestamp: float
    duration_ms: float


@dataclass
class RetryAttempt:
    """Record of a single retry attempt.

    Attributes:
        attempt: The attempt number (1-based).
        error: Description of the error that triggered the retry.
        delay_seconds: The backoff delay before the next attempt.
        timestamp: Wall-clock time of this attempt.
    """

    attempt: int
    error: str
    delay_seconds: float
    timestamp: float


@dataclass
class ExecutionRecord:
    """Complete record of an agent execution.

    Attributes:
        input_tokens: Total input tokens consumed across all LLM calls.
        output_tokens: Total output tokens generated across all LLM calls.
        wall_clock_start: Start timestamp (seconds since epoch).
        wall_clock_end: End timestamp (seconds since epoch).
        tool_invocations: Ordered list of tool invocations during execution.
        retry_attempts: Ordered list of retry attempts (empty on first-try success).
        model_used: The model identifier actually used.
        final_content: The final text content from the LLM.
        final_output: Parsed structured output (dict), or None if not JSON.
        stop_reason: Why the LLM stopped on its final turn.
        total_llm_calls: Total number of LLM calls made (including retries).
        timed_out: Whether the execution was terminated due to timeout.
        error: Error message if execution failed, otherwise empty.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    wall_clock_start: float = 0.0
    wall_clock_end: float = 0.0
    tool_invocations: list[ToolInvocation] = field(default_factory=list)
    retry_attempts: list[RetryAttempt] = field(default_factory=list)
    model_used: str = ""
    final_content: str = ""
    final_output: dict[str, Any] | None = None
    stop_reason: str = ""
    total_llm_calls: int = 0
    timed_out: bool = False
    error: str = ""

    @property
    def wall_clock_seconds(self) -> float:
        """Total wall-clock duration in seconds."""
        return self.wall_clock_end - self.wall_clock_start

    @property
    def total_tokens(self) -> int:
        """Total tokens consumed (input + output)."""
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "wall_clock_timing": {
                "start": self.wall_clock_start,
                "end": self.wall_clock_end,
                "duration_seconds": self.wall_clock_seconds,
            },
            "model_used": self.model_used,
            "total_llm_calls": self.total_llm_calls,
            "stop_reason": self.stop_reason,
            "timed_out": self.timed_out,
            "error": self.error,
            "tool_invocations": [
                {
                    "tool_name": inv.tool_name,
                    "tool_input": inv.tool_input,
                    "tool_output": inv.tool_output,
                    "timestamp": inv.timestamp,
                    "duration_ms": inv.duration_ms,
                }
                for inv in self.tool_invocations
            ],
            "retry_attempts": [
                {
                    "attempt": att.attempt,
                    "error": att.error,
                    "delay_seconds": att.delay_seconds,
                    "timestamp": att.timestamp,
                }
                for att in self.retry_attempts
            ],
        }


# Default tool handler that returns an error for unhandled tools.
def _default_tool_handler(tool_name: str, tool_input: dict[str, Any]) -> str:
    """Default tool handler that returns an error indicating the tool is not bound."""
    return f"Error: Tool '{tool_name}' is not bound to a handler."


def _compute_backoff_delay(attempt: int, backoff: str) -> float:
    """Compute the backoff delay for a retry attempt.

    Args:
        attempt: The attempt number (1-based; delay is for the wait *after* this attempt).
        backoff: The backoff strategy name. Currently only ``"exponential"`` is
            supported; any other value falls back to a fixed 1-second delay.

    Returns:
        Delay in seconds before the next attempt.
    """
    if backoff == "exponential":
        return float(2 ** (attempt - 1))
    return 1.0


def _is_retryable(error: Exception) -> bool:
    """Determine whether an LLM error is retryable.

    Transient failures (timeouts, server errors with 5xx status codes) are
    retryable. Auth errors and client errors (4xx) are not.

    Args:
        error: The exception raised by the LLM client.

    Returns:
        True if the error is transient and the call should be retried.
    """
    if isinstance(error, LLMTimeoutError):
        return True
    if isinstance(error, LLMProviderError):
        if error.status_code is not None and error.status_code >= 500:
            return True
        # Rate limit (429) is retryable.
        if error.status_code == 429:
            return True
    return False


def format_inputs_as_messages(resolved_inputs: dict[str, str]) -> list[LLMMessage]:
    """Format resolved workflow inputs as user messages for the LLM.

    Each input is formatted as a labelled block so the model can distinguish
    between different input sources.

    Args:
        resolved_inputs: Mapping from input name to resolved content string.

    Returns:
        A list of :class:`~agentry.llm.models.LLMMessage` objects with
        ``role="user"``.
    """
    if not resolved_inputs:
        return []

    parts: list[str] = []
    for name, content in resolved_inputs.items():
        parts.append(f"[{name}]\n{content}")

    combined = "\n\n".join(parts)
    return [LLMMessage(role="user", content=combined)]


def _try_parse_json(content: str) -> dict[str, Any] | None:
    """Attempt to parse the LLM content as JSON.

    If the content contains a JSON code fence, extracts and parses the fenced
    content. Otherwise attempts direct parsing.

    Args:
        content: Raw text content from the LLM.

    Returns:
        Parsed dict if content is valid JSON, None otherwise.
    """
    text = content.strip()

    # Try extracting from markdown code fences.
    if "```json" in text:
        start = text.index("```json") + len("```json")
        end = text.index("```", start)
        text = text[start:end].strip()
    elif "```" in text:
        start = text.index("```") + 3
        # Skip any language tag on the same line.
        newline = text.find("\n", start)
        if newline != -1:
            start = newline + 1
        end = text.index("```", start)
        text = text[start:end].strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    return None


class AgentExecutor:
    """Agent execution engine with retry, timeout, and recording.

    Orchestrates the interaction between the CLI, the LLM client, and the tool
    system. Supports a multi-turn conversation loop where the LLM can invoke
    tools and receive their results before producing final output.

    Args:
        llm_client: An object satisfying the :class:`~agentry.llm.protocol.LLMClient`
            protocol.
        tool_handler: Optional callable ``(tool_name, tool_input) -> str`` that
            executes tool invocations. Defaults to a handler that returns an
            error for all tools.
        sleep_func: Optional callable for sleeping between retries. Defaults to
            ``time.sleep``. Override in tests to avoid actual delays.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tool_handler: Callable[[str, dict[str, Any]], str] | None = None,
        sleep_func: Callable[[float], None] | None = None,
    ) -> None:
        self._client = llm_client
        self._tool_handler = tool_handler or _default_tool_handler
        self._sleep = sleep_func or time.sleep

    def run(
        self,
        system_prompt: str,
        resolved_inputs: dict[str, str],
        tool_names: list[str],
        config: LLMConfig,
        retry_config: RetryConfig | None = None,
        timeout: float | None = None,
    ) -> ExecutionRecord:
        """Execute the agent and return the execution record.

        This is the synchronous entry point. Internally it uses asyncio to
        enforce the timeout if one is configured.

        Args:
            system_prompt: The system prompt text (already loaded from disk).
            resolved_inputs: Mapping from input name to resolved content.
            tool_names: List of tool identifiers to expose to the model.
            config: LLM call configuration (model, temperature, max_tokens).
            retry_config: Retry configuration. Defaults to a single attempt
                with no retries.
            timeout: Overall execution timeout in seconds. ``None`` means
                no timeout.

        Returns:
            An :class:`ExecutionRecord` with token usage, timing, tool
            invocations, and the final output.
        """
        if retry_config is None:
            retry_config = RetryConfig()

        record = ExecutionRecord()
        record.wall_clock_start = time.time()

        try:
            if timeout is not None:
                config = LLMConfig(
                    model=config.model,
                    max_tokens=config.max_tokens,
                    temperature=config.temperature,
                    timeout=timeout,
                )
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # Already in an async context; run synchronously.
                        self._execute_with_retry(
                            system_prompt=system_prompt,
                            resolved_inputs=resolved_inputs,
                            tool_names=tool_names,
                            config=config,
                            retry_config=retry_config,
                            record=record,
                        )
                    else:
                        asyncio.run(
                            asyncio.wait_for(
                                self._execute_with_retry_async(
                                    system_prompt=system_prompt,
                                    resolved_inputs=resolved_inputs,
                                    tool_names=tool_names,
                                    config=config,
                                    retry_config=retry_config,
                                    record=record,
                                ),
                                timeout=timeout,
                            )
                        )
                except RuntimeError:
                    # No event loop available; use sync path.
                    self._execute_with_retry(
                        system_prompt=system_prompt,
                        resolved_inputs=resolved_inputs,
                        tool_names=tool_names,
                        config=config,
                        retry_config=retry_config,
                        record=record,
                    )
                except asyncio.TimeoutError:
                    record.timed_out = True
                    record.error = f"Execution timed out after {timeout:.1f} seconds."
            else:
                self._execute_with_retry(
                    system_prompt=system_prompt,
                    resolved_inputs=resolved_inputs,
                    tool_names=tool_names,
                    config=config,
                    retry_config=retry_config,
                    record=record,
                )
        except LLMTimeoutError as exc:
            record.timed_out = True
            record.error = str(exc)
        except Exception as exc:
            record.error = str(exc)
        finally:
            record.wall_clock_end = time.time()

        return record

    def _execute_with_retry(
        self,
        system_prompt: str,
        resolved_inputs: dict[str, str],
        tool_names: list[str],
        config: LLMConfig,
        retry_config: RetryConfig,
        record: ExecutionRecord,
    ) -> None:
        """Execute the agent with retry logic (synchronous path).

        Retries on transient LLM errors up to ``retry_config.max_attempts``
        times with exponential backoff.
        """
        messages = format_inputs_as_messages(resolved_inputs)
        tool_defs = build_tool_definitions(tool_names)

        last_error: Exception | None = None

        for attempt in range(1, retry_config.max_attempts + 1):
            try:
                self._conversation_loop(
                    system_prompt=system_prompt,
                    messages=messages,
                    tool_defs=tool_defs,
                    tool_names=tool_names,
                    config=config,
                    record=record,
                )
                return  # Success.
            except Exception as exc:
                last_error = exc
                record.total_llm_calls += 1

                if not _is_retryable(exc) or attempt == retry_config.max_attempts:
                    raise

                delay = _compute_backoff_delay(attempt, retry_config.backoff)
                record.retry_attempts.append(
                    RetryAttempt(
                        attempt=attempt,
                        error=str(exc),
                        delay_seconds=delay,
                        timestamp=time.time(),
                    )
                )
                self._sleep(delay)
                # Reset messages for retry (fresh conversation).
                messages = format_inputs_as_messages(resolved_inputs)

        # Should not reach here, but raise last error just in case.
        if last_error is not None:
            raise last_error  # pragma: no cover

    async def _execute_with_retry_async(
        self,
        system_prompt: str,
        resolved_inputs: dict[str, str],
        tool_names: list[str],
        config: LLMConfig,
        retry_config: RetryConfig,
        record: ExecutionRecord,
    ) -> None:
        """Execute the agent with retry logic (async path for timeout enforcement)."""
        # Delegate to synchronous implementation in a thread to avoid blocking.
        # The asyncio.wait_for wrapper in run() handles the timeout.
        self._execute_with_retry(
            system_prompt=system_prompt,
            resolved_inputs=resolved_inputs,
            tool_names=tool_names,
            config=config,
            retry_config=retry_config,
            record=record,
        )

    def _conversation_loop(
        self,
        system_prompt: str,
        messages: list[LLMMessage],
        tool_defs: list[dict[str, Any]],
        tool_names: list[str],
        config: LLMConfig,
        record: ExecutionRecord,
    ) -> None:
        """Run the multi-turn conversation loop.

        The loop continues as long as the model returns tool_use stop reasons.
        Tool results are appended to the conversation and sent back. The loop
        terminates when the model produces an end_turn or max_tokens stop, or
        when no tool calls are present in the response.

        A maximum of 20 turns is enforced to prevent infinite loops.
        """
        max_turns = 20

        for _turn in range(max_turns):
            response = self._client.call(
                system_prompt=system_prompt,
                messages=messages,
                tools=tool_defs,
                config=config,
            )
            record.total_llm_calls += 1
            self._accumulate_usage(record, response.usage)
            record.model_used = response.model or config.model

            if response.tool_calls:
                # Process tool invocations.
                tool_results = self._handle_tool_calls(
                    response.tool_calls, tool_names, record
                )

                # Add assistant response (with tool use) and tool results.
                messages.append(
                    LLMMessage(role="assistant", content=response.content or "")
                )
                # Format tool results as a user message.
                result_text = "\n\n".join(
                    f"[Tool Result: {r['name']}]\n{r['output']}"
                    for r in tool_results
                )
                messages.append(LLMMessage(role="user", content=result_text))

                if response.stop_reason != "tool_use":
                    # Model stopped for a reason other than tool use; finalize.
                    self._finalize(record, response)
                    return
            else:
                # No tool calls; this is the final response.
                self._finalize(record, response)
                return

        # Max turns reached; finalize with whatever we have.
        self._finalize(record, response)

    def _handle_tool_calls(
        self,
        tool_calls: list[dict[str, Any]],
        tool_names: list[str],
        record: ExecutionRecord,
    ) -> list[dict[str, Any]]:
        """Execute tool invocations and record them.

        Args:
            tool_calls: Tool call dicts from the LLM response.
            tool_names: Allowed tool identifiers.
            record: Execution record to append invocations to.

        Returns:
            List of dicts with ``name`` and ``output`` keys.
        """
        results: list[dict[str, Any]] = []

        for call in tool_calls:
            call_name = call.get("name", "")
            call_input = call.get("input", {})

            # Map internal names back to original (e.g. repository__read -> repository:read).
            original_name = call_name.replace("__", ":")

            start_time = time.time()
            try:
                output = self._tool_handler(original_name, call_input)
            except Exception as exc:
                output = f"Error executing tool '{original_name}': {exc}"
            end_time = time.time()

            duration_ms = (end_time - start_time) * 1000

            record.tool_invocations.append(
                ToolInvocation(
                    tool_name=original_name,
                    tool_input=call_input,
                    tool_output=output,
                    timestamp=start_time,
                    duration_ms=duration_ms,
                )
            )

            results.append({"name": call_name, "output": output})

        return results

    def _accumulate_usage(self, record: ExecutionRecord, usage: TokenUsage) -> None:
        """Add token usage from a single call to the cumulative record."""
        record.input_tokens += usage.input_tokens
        record.output_tokens += usage.output_tokens

    def _finalize(self, record: ExecutionRecord, response: LLMResponse) -> None:
        """Finalize the execution record with the last response."""
        record.final_content = response.content
        record.stop_reason = response.stop_reason

        # Attempt to parse structured JSON output.
        if response.content:
            record.final_output = _try_parse_json(response.content)
