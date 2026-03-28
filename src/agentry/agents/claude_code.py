"""ClaudeCodeAgent: agent runtime backed by the Claude Code CLI.

Invokes ``claude -p`` (print mode) as a subprocess with the configured
model, system prompt, and task description.  When an output schema is
provided the ``--output-format json`` flag is added and the structured
JSON response is parsed into ``AgentResult.output``.

Timeout is enforced by killing the subprocess with SIGKILL if it exceeds
the configured limit.

Token usage is extracted from Claude Code's JSON metadata envelope when
``--output-format json`` is used.

Usage::

    from agentry.agents.claude_code import ClaudeCodeAgent
    from agentry.agents.models import AgentTask

    agent = ClaudeCodeAgent(model="claude-sonnet-4-20250514")
    if ClaudeCodeAgent.check_available():
        result = agent.execute(AgentTask(
            system_prompt="You are a code reviewer.",
            task_description="Check this diff for bugs.",
            working_directory="/workspace",
        ))
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any

from agentry.agents.models import AgentResult, AgentTask, TokenUsage


class ClaudeCodeAgent:
    """Agent runtime that delegates to the Claude Code CLI (``claude -p``).

    Args:
        model: The model identifier to pass via ``--model``.  Defaults to
            ``"claude-sonnet-4-20250514"``.
        env_overrides: Optional mapping of extra environment variables to
            inject into the subprocess.  Useful for testing.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        env_overrides: dict[str, str] | None = None,
        max_iterations: int | None = None,
        **kwargs: object,
    ) -> None:
        self._model = model
        self._env_overrides = env_overrides or {}
        self._max_turns = max_iterations

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(self, agent_task: AgentTask) -> AgentResult:
        """Execute the task by invoking ``claude -p``.

        Builds a subprocess command from the task, runs it with optional
        timeout enforcement, and parses the output into an
        :class:`~agentry.agents.models.AgentResult`.

        Args:
            agent_task: The assembled task bundle.

        Returns:
            :class:`~agentry.agents.models.AgentResult` populated from the
            subprocess output and exit code.
        """
        cmd = self._build_command(agent_task)
        stdin_text = self._build_stdin(agent_task)
        env = self._build_env()

        cwd: str | None = agent_task.working_directory or None

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=cwd,
                text=True,
            )

            timeout = agent_task.timeout
            try:
                stdout, stderr = proc.communicate(input=stdin_text, timeout=timeout)
                exit_code = proc.returncode
                timed_out = False
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
                exit_code = proc.returncode if proc.returncode is not None else -1
                timed_out = True

        except FileNotFoundError:
            return AgentResult(
                exit_code=127,
                error="claude binary not found on PATH",
            )
        except OSError as exc:
            return AgentResult(
                exit_code=1,
                error=f"Failed to launch claude subprocess: {exc}",
            )

        if timed_out:
            return AgentResult(
                raw_output=stdout,
                exit_code=exit_code,
                timed_out=True,
                error=f"Execution timed out after {agent_task.timeout} seconds.",
            )

        return self._parse_output(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            use_json=agent_task.output_schema is not None,
        )

    @staticmethod
    def check_available() -> bool:
        """Return ``True`` when the ``claude`` binary is on PATH and executable.

        Uses :func:`shutil.which` so the check honours the current ``PATH``
        without spawning a subprocess.
        """
        return shutil.which("claude") is not None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_command(self, agent_task: AgentTask) -> list[str]:
        """Assemble the ``claude`` CLI command."""
        cmd: list[str] = ["claude", "-p"]

        cmd.extend(["--model", self._model])

        # Task-level max_iterations takes precedence over instance-level.
        effective_max_turns = (
            agent_task.max_iterations
            if agent_task.max_iterations is not None
            else self._max_turns
        )
        if effective_max_turns is not None:
            if effective_max_turns < 1:
                raise ValueError(
                    f"max_iterations must be >= 1, got {effective_max_turns}"
                )
            cmd.extend(["--max-turns", str(effective_max_turns)])

        if agent_task.output_schema is not None:
            cmd.extend(["--output-format", "json"])

        if agent_task.system_prompt:
            cmd.extend(["--system-prompt", agent_task.system_prompt])

        return cmd

    def _build_stdin(self, agent_task: AgentTask) -> str:
        """Return text to pass to the subprocess via stdin."""
        return agent_task.task_description

    def _build_env(self) -> dict[str, str]:
        """Build the subprocess environment, forwarding ANTHROPIC_API_KEY."""
        env = os.environ.copy()
        env.update(self._env_overrides)
        return env

    def _parse_output(
        self,
        stdout: str,
        stderr: str,
        exit_code: int,
        use_json: bool,
    ) -> AgentResult:
        """Parse subprocess output into an :class:`~agentry.agents.models.AgentResult`.

        When *use_json* is ``True`` the stdout is expected to be a JSON object.
        Claude Code's ``--output-format json`` response has the structure::

            {
              "type": "result",
              "subtype": "success",
              "result": "<text or structured output>",
              "usage": {"input_tokens": N, "output_tokens": N},
              ...
            }

        On parse failure the raw_output is preserved and an error is recorded.
        """
        raw_output = stdout
        output: dict[str, Any] | None = None
        token_usage = TokenUsage()
        error = "" if exit_code == 0 else (stderr.strip() or f"Exit code {exit_code}")

        if use_json and stdout.strip():
            try:
                parsed = json.loads(stdout.strip())
                # Extract token usage from the metadata envelope.
                usage_data = parsed.get("usage", {})
                if usage_data:
                    # Include cache tokens in the total input count.
                    _input = int(usage_data.get("input_tokens", 0))
                    _cache_create = int(usage_data.get("cache_creation_input_tokens", 0))
                    _cache_read = int(usage_data.get("cache_read_input_tokens", 0))
                    token_usage = TokenUsage(
                        input_tokens=_input + _cache_create + _cache_read,
                        output_tokens=int(usage_data.get("output_tokens", 0)),
                    )

                # The result field contains the structured payload.
                result_field = parsed.get("result")
                if isinstance(result_field, dict):
                    output = result_field
                elif isinstance(result_field, str) and result_field.strip():
                    # Try to parse inner JSON string.
                    try:
                        inner = json.loads(result_field)
                        if isinstance(inner, dict):
                            output = inner
                        elif isinstance(inner, list):
                            output = {"result": inner}
                        else:
                            output = {"raw_response": result_field}
                    except (json.JSONDecodeError, ValueError):
                        # Preserve the text response so it's not silently lost.
                        output = {"raw_response": result_field}
                elif isinstance(result_field, list):
                    output = {"result": result_field}

                # Fallback: when result is None/empty, capture the full
                # envelope metadata so callers can inspect stop_reason etc.
                if output is None:
                    output = {
                        "raw_response": result_field or "",
                        "stop_reason": parsed.get("stop_reason", ""),
                        "subtype": parsed.get("subtype", ""),
                    }

            except (json.JSONDecodeError, ValueError) as exc:
                error = f"Failed to parse JSON output: {exc}; raw: {stdout[:200]}"

        return AgentResult(
            output=output,
            raw_output=raw_output,
            exit_code=exit_code,
            token_usage=token_usage,
            timed_out=False,
            error=error,
        )
