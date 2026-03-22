"""Execution record data models.

Provides dataclasses for recording the outcome of agent execution,
including token usage, tool invocations, retry attempts, and timing.

These models were originally in ``agentry.executor`` and are retained
for backward compatibility with the composition engine's record-keeping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
