"""Unit tests for T02: InProcessRunner delegates to AgentProtocol.

Tests cover:
- InProcessRunner accepts AgentProtocol instead of llm_client.
- execute() delegates to agent.execute() with an AgentTask.
- ExecutionResult is populated from AgentResult fields.
- No AgentExecutor or llm_client references remain.
- Full lifecycle: check_available -> provision -> execute -> teardown.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from agentry.models.safety import SafetyBlock
from agentry.runners.protocol import (
    AgentConfig,
    ExecutionResult,
    RunnerContext,
    RunnerProtocol,
    RunnerStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_agent(
    raw_output: str = "execution result",
    exit_code: int = 0,
    timed_out: bool = False,
    error: str = "",
    output: dict | None = None,
    input_tokens: int = 10,
    output_tokens: int = 5,
    tool_invocations: list | None = None,
) -> MagicMock:
    """Build a mock AgentProtocol that returns a configured AgentResult."""
    from agentry.agents.models import AgentResult, TokenUsage

    mock_agent = MagicMock()
    mock_agent.execute.return_value = AgentResult(
        raw_output=raw_output,
        exit_code=exit_code,
        timed_out=timed_out,
        error=error,
        output=output,
        token_usage=TokenUsage(
            input_tokens=input_tokens, output_tokens=output_tokens
        ),
        tool_invocations=tool_invocations or [],
    )
    return mock_agent


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestInProcessRunnerAgentDelegation:
    """T02 proof: InProcessRunner delegates to a mock AgentProtocol, no LLMClient."""

    def test_satisfies_runner_protocol(self) -> None:
        """InProcessRunner satisfies the RunnerProtocol."""
        from agentry.runners.in_process import InProcessRunner

        runner = InProcessRunner(agent=_make_mock_agent())
        assert isinstance(runner, RunnerProtocol)

    def test_init_accepts_agent_not_llm_client(self) -> None:
        """InProcessRunner.__init__ takes agent, not llm_client."""
        from agentry.runners.in_process import InProcessRunner

        mock_agent = _make_mock_agent()
        runner = InProcessRunner(agent=mock_agent)
        assert runner.agent is mock_agent

    def test_no_agent_executor_in_source(self) -> None:
        """InProcessRunner module contains no AgentExecutor reference."""
        import inspect

        from agentry.runners import in_process

        source = inspect.getsource(in_process)
        assert "AgentExecutor" not in source

    def test_no_llm_client_in_source(self) -> None:
        """InProcessRunner module contains no llm_client reference."""
        import inspect

        from agentry.runners import in_process

        source = inspect.getsource(in_process)
        assert "llm_client" not in source

    def test_execute_calls_agent_execute_with_agent_task(self) -> None:
        """execute() passes an AgentTask to agent.execute()."""
        from agentry.agents.models import AgentTask
        from agentry.runners.in_process import InProcessRunner

        mock_agent = _make_mock_agent(raw_output="done")
        runner = InProcessRunner(agent=mock_agent)

        ctx = RunnerContext()
        config = AgentConfig(
            system_prompt="You are a code reviewer.",
            resolved_inputs={"diff": "--- a/f.py\n+++ b/f.py"},
            tool_names=["repo:read"],
            timeout=60.0,
        )

        runner.execute(runner_context=ctx, agent_config=config)

        mock_agent.execute.assert_called_once()
        task_arg = mock_agent.execute.call_args[0][0]
        assert isinstance(task_arg, AgentTask)
        assert task_arg.system_prompt == "You are a code reviewer."
        assert task_arg.tool_names == ["repo:read"]
        assert task_arg.timeout == 60.0

    def test_execute_result_maps_output(self) -> None:
        """ExecutionResult.output matches AgentResult.output."""
        from agentry.runners.in_process import InProcessRunner

        mock_agent = _make_mock_agent(
            raw_output="review done",
            output={"verdict": "approved"},
        )
        runner = InProcessRunner(agent=mock_agent)

        result = runner.execute(
            runner_context=RunnerContext(),
            agent_config=AgentConfig(
                system_prompt="test",
                resolved_inputs={},
                tool_names=[],
            ),
        )

        assert result.output == {"verdict": "approved"}

    def test_execute_result_maps_token_usage(self) -> None:
        """ExecutionResult.token_usage contains AgentResult token counts."""
        from agentry.runners.in_process import InProcessRunner

        mock_agent = _make_mock_agent(input_tokens=200, output_tokens=80)
        runner = InProcessRunner(agent=mock_agent)

        result = runner.execute(
            runner_context=RunnerContext(),
            agent_config=AgentConfig(
                system_prompt="test",
                resolved_inputs={},
                tool_names=[],
            ),
        )

        assert result.token_usage["input_tokens"] == 200
        assert result.token_usage["output_tokens"] == 80

    def test_execute_result_maps_tool_invocations(self) -> None:
        """ExecutionResult.tool_invocations mirrors AgentResult.tool_invocations."""
        from agentry.runners.in_process import InProcessRunner

        invocations = [{"tool": "read", "result": "file content"}]
        mock_agent = _make_mock_agent(tool_invocations=invocations)
        runner = InProcessRunner(agent=mock_agent)

        result = runner.execute(
            runner_context=RunnerContext(),
            agent_config=AgentConfig(
                system_prompt="test",
                resolved_inputs={},
                tool_names=[],
            ),
        )

        assert result.tool_invocations == invocations

    def test_execute_exit_code_zero_on_success(self) -> None:
        """execute() returns exit_code=0 when AgentResult has no error."""
        from agentry.runners.in_process import InProcessRunner

        runner = InProcessRunner(agent=_make_mock_agent(exit_code=0, error=""))
        result = runner.execute(
            runner_context=RunnerContext(),
            agent_config=AgentConfig(
                system_prompt="t",
                resolved_inputs={},
                tool_names=[],
            ),
        )
        assert result.exit_code == 0

    def test_execute_exit_code_one_on_error(self) -> None:
        """execute() returns exit_code=1 when AgentResult has an error."""
        from agentry.runners.in_process import InProcessRunner

        runner = InProcessRunner(
            agent=_make_mock_agent(exit_code=0, error="Agent failed badly")
        )
        result = runner.execute(
            runner_context=RunnerContext(),
            agent_config=AgentConfig(
                system_prompt="t",
                resolved_inputs={},
                tool_names=[],
            ),
        )
        assert result.exit_code == 1
        assert result.stderr == "Agent failed badly"

    def test_execute_timed_out_flag(self) -> None:
        """execute() reflects timed_out from AgentResult."""
        from agentry.runners.in_process import InProcessRunner

        runner = InProcessRunner(
            agent=_make_mock_agent(exit_code=1, timed_out=True, error="timeout")
        )
        result = runner.execute(
            runner_context=RunnerContext(),
            agent_config=AgentConfig(
                system_prompt="t",
                resolved_inputs={},
                tool_names=[],
            ),
        )
        assert result.timed_out is True

    def test_provision_no_isolation(self, caplog: pytest.LogCaptureFixture) -> None:
        """provision() is a no-op and logs elevated trust warning."""
        from agentry.runners.in_process import InProcessRunner

        runner = InProcessRunner(agent=_make_mock_agent())
        with caplog.at_level(logging.WARNING):
            ctx = runner.provision(SafetyBlock(), {})

        assert ctx.container_id == ""
        assert ctx.metadata["runner_type"] == "in_process"
        assert any("elevated trust mode" in r.message.lower() for r in caplog.records)

    def test_teardown_is_no_op(self) -> None:
        """teardown() never raises and is idempotent."""
        from agentry.runners.in_process import InProcessRunner

        runner = InProcessRunner(agent=_make_mock_agent())
        ctx = RunnerContext()
        runner.teardown(ctx)
        runner.teardown(ctx)

    def test_check_available_always_true(self) -> None:
        """check_available() always returns available=True."""
        from agentry.runners.in_process import InProcessRunner

        runner = InProcessRunner(agent=_make_mock_agent())
        status = runner.check_available()
        assert status.available is True

    def test_full_lifecycle(self) -> None:
        """Full check_available -> provision -> execute -> teardown succeeds."""
        from agentry.runners.in_process import InProcessRunner

        mock_agent = _make_mock_agent(
            raw_output="review complete",
            output={"approved": True},
            input_tokens=150,
            output_tokens=60,
            tool_invocations=[{"tool": "read_file", "result": "ok"}],
        )
        runner = InProcessRunner(agent=mock_agent)
        safety = SafetyBlock(trust="elevated")

        # 1. Check availability.
        status = runner.check_available()
        assert status.available

        # 2. Provision.
        ctx = runner.provision(safety_block=safety, resolved_inputs={})
        assert ctx.container_id == ""

        # 3. Execute.
        config = AgentConfig(
            system_prompt="You are a reviewer.",
            resolved_inputs={"diff": "--- a/f.py"},
            tool_names=["repo:read"],
            agent_name="claude-code",
            timeout=120.0,
        )
        result = runner.execute(runner_context=ctx, agent_config=config)

        assert result.exit_code == 0
        assert result.stdout == "review complete"
        assert result.output == {"approved": True}
        assert result.token_usage["input_tokens"] == 150
        assert result.tool_invocations == [{"tool": "read_file", "result": "ok"}]

        # 4. Teardown.
        runner.teardown(ctx)
