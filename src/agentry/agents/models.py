"""Pydantic v2 data models for agent tasks and results.

Defines the data contract between the orchestration layer and agent runtimes.
AgentTask carries everything the agent needs to execute; AgentResult carries
the complete execution record back to the caller.

Usage::

    from agentry.agents.models import AgentTask, AgentResult, TokenUsage

    task = AgentTask(
        system_prompt="You are a code reviewer.",
        task_description="Review the following diff for bugs.",
        tool_names=["repository:read"],
        working_directory="/workspace",
    )
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TokenUsage(BaseModel):
    """Token usage reported by an agent execution.

    Attributes:
        input_tokens: Number of input/prompt tokens consumed.
        output_tokens: Number of output/completion tokens generated.
    """

    input_tokens: int = 0
    output_tokens: int = 0


class AgentTask(BaseModel):
    """Input bundle for a single agent execution.

    Attributes:
        system_prompt: The system prompt text (already loaded from disk).
        task_description: The assembled task content, constructed from resolved
            workflow inputs.
        tool_names: Tool identifiers to expose to the agent runtime.
        output_schema: Optional JSON schema dict. When provided, the agent is
            asked to return structured JSON conforming to this schema.
        timeout: Execution timeout in seconds. ``None`` means no limit.
        max_iterations: Maximum number of agentic iterations. ``None`` defers
            to the agent runtime's default.
        working_directory: The directory the agent should treat as its working
            context (e.g. the mounted workspace path).
    """

    system_prompt: str
    task_description: str
    tool_names: list[str] = Field(default_factory=list)
    output_schema: dict[str, Any] | None = None
    timeout: float | None = None
    max_iterations: int | None = None
    working_directory: str = ""


class AgentResult(BaseModel):
    """Output bundle from a single agent execution.

    Attributes:
        output: Structured output parsed from the agent's response. ``None``
            when no structured output was requested or parsing failed.
        raw_output: The full raw text output from the agent subprocess.
        exit_code: The process exit code. 0 indicates success.
        token_usage: Input and output token counts from the agent run.
        tool_invocations: List of tool invocation records (name + result pairs).
            Claude Code's JSON output may not include granular tool history;
            the list may be empty even when tools were used.
        timed_out: ``True`` when the execution was terminated by the timeout
            enforcer.
        error: Error message if execution failed, or empty string on success.
    """

    output: dict[str, Any] | None = None
    raw_output: str = ""
    exit_code: int = 0
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    tool_invocations: list[dict[str, Any]] = Field(default_factory=list)
    timed_out: bool = False
    error: str = ""
