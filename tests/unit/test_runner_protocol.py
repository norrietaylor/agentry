"""Unit tests for RunnerProtocol data models, InProcessRunner, and RunnerDetector.

Tests cover:
- RunnerContext dataclass defaults and field assignment.
- RunnerStatus dataclass defaults and field assignment.
- AgentConfig dataclass field assignment (agent_name/agent_config, no llm_config).
- ExecutionResult dataclass defaults and field assignment.
- RunnerProtocol isinstance checks via @runtime_checkable.
- Concrete implementation satisfies protocol.
- Protocol rejects objects missing required methods.
- T02: InProcessRunner delegates to AgentProtocol, no AgentExecutor dependency.
- T02: RunnerDetector resolves agent by name and injects into runner.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from agentry.executor import ExecutionRecord
from agentry.models.safety import SafetyBlock
from agentry.runners.protocol import (
    AgentConfig,
    ExecutionResult,
    RunnerContext,
    RunnerProtocol,
    RunnerStatus,
)

# ---------------------------------------------------------------------------
# Helpers: minimal concrete runner
# ---------------------------------------------------------------------------


class _MinimalRunner:
    """Concrete runner satisfying RunnerProtocol for testing."""

    def provision(
        self,
        safety_block: SafetyBlock,
        resolved_inputs: dict[str, str],
    ) -> RunnerContext:
        return RunnerContext(
            container_id="ctr-001",
            network_id="net-001",
            mount_mappings={"/host/repo": "/workspace"},
            metadata={"image": safety_block.sandbox.base},
        )

    def execute(
        self,
        runner_context: RunnerContext,
        agent_config: AgentConfig,
    ) -> ExecutionResult:
        record = ExecutionRecord(
            final_content="done",
            model_used="claude-sonnet-4-5",
            total_llm_calls=1,
            stop_reason="end_turn",
        )
        return ExecutionResult(
            execution_record=record,
            exit_code=0,
            stdout="output text",
            stderr="",
        )

    def teardown(self, runner_context: RunnerContext) -> None:
        pass  # No-op for testing.

    def check_available(self) -> RunnerStatus:
        return RunnerStatus(available=True, message="Runner is ready.")


class _IncompleteRunner:
    """Runner that is missing the execute() method."""

    def provision(
        self, safety_block: SafetyBlock, resolved_inputs: dict[str, str]
    ) -> RunnerContext:
        return RunnerContext()

    def teardown(self, runner_context: RunnerContext) -> None:
        pass

    def check_available(self) -> RunnerStatus:
        return RunnerStatus(available=False)


# ---------------------------------------------------------------------------
# RunnerContext tests
# ---------------------------------------------------------------------------


class TestRunnerContext:
    """Tests for the RunnerContext dataclass."""

    def test_defaults(self) -> None:
        """RunnerContext has sensible default values."""
        ctx = RunnerContext()
        assert ctx.container_id == ""
        assert ctx.network_id == ""
        assert ctx.mount_mappings == {}
        assert ctx.metadata == {}

    def test_field_assignment(self) -> None:
        """RunnerContext fields are assignable at construction."""
        ctx = RunnerContext(
            container_id="ctr-abc",
            network_id="net-def",
            mount_mappings={"/src": "/workspace", "/out": "/output"},
            metadata={"image": "agentry/sandbox:1.0", "cpu": 1.0},
        )
        assert ctx.container_id == "ctr-abc"
        assert ctx.network_id == "net-def"
        assert ctx.mount_mappings == {"/src": "/workspace", "/out": "/output"}
        assert ctx.metadata["image"] == "agentry/sandbox:1.0"

    def test_independent_default_dicts(self) -> None:
        """Each RunnerContext instance gets its own mount_mappings and metadata."""
        ctx1 = RunnerContext()
        ctx2 = RunnerContext()
        ctx1.mount_mappings["/src"] = "/workspace"
        ctx1.metadata["key"] = "value"
        assert ctx2.mount_mappings == {}
        assert ctx2.metadata == {}


# ---------------------------------------------------------------------------
# RunnerStatus tests
# ---------------------------------------------------------------------------


class TestRunnerStatus:
    """Tests for the RunnerStatus dataclass."""

    def test_available_true(self) -> None:
        """RunnerStatus with available=True."""
        status = RunnerStatus(available=True)
        assert status.available is True
        assert status.message == ""

    def test_available_false_with_message(self) -> None:
        """RunnerStatus with available=False carries a diagnostic message."""
        status = RunnerStatus(
            available=False,
            message="Docker daemon not responding.",
        )
        assert status.available is False
        assert status.message == "Docker daemon not responding."

    def test_message_default_empty(self) -> None:
        """Default message is empty string."""
        status = RunnerStatus(available=True)
        assert status.message == ""


# ---------------------------------------------------------------------------
# AgentConfig tests
# ---------------------------------------------------------------------------


class TestAgentConfig:
    """Tests for the AgentConfig dataclass."""

    def test_required_fields(self) -> None:
        """AgentConfig accepts the minimum required fields."""
        config = AgentConfig(
            system_prompt="You are a code reviewer.",
            resolved_inputs={"diff": "--- a/file.py\n+++ b/file.py"},
            tool_names=["repository:read"],
        )
        assert config.system_prompt == "You are a code reviewer."
        assert config.resolved_inputs == {"diff": "--- a/file.py\n+++ b/file.py"}
        assert config.tool_names == ["repository:read"]
        assert config.agent_name == "claude-code"
        assert config.agent_config == {}
        assert config.timeout is None

    def test_optional_fields(self) -> None:
        """AgentConfig accepts optional agent_name, agent_config, and timeout."""
        config = AgentConfig(
            system_prompt="test",
            resolved_inputs={},
            tool_names=[],
            agent_name="custom-agent",
            agent_config={"model": "claude-opus-4-5"},
            timeout=120.0,
        )
        assert config.agent_name == "custom-agent"
        assert config.agent_config == {"model": "claude-opus-4-5"}
        assert config.timeout == 120.0

    def test_no_llm_config_field(self) -> None:
        """AgentConfig does not have an llm_config field."""
        config = AgentConfig(
            system_prompt="test",
            resolved_inputs={},
            tool_names=[],
        )
        assert not hasattr(config, "llm_config"), "llm_config should be removed"


# ---------------------------------------------------------------------------
# ExecutionResult tests
# ---------------------------------------------------------------------------


class TestExecutionResult:
    """Tests for the ExecutionResult dataclass."""

    def test_defaults(self) -> None:
        """ExecutionResult has sensible default values."""
        result = ExecutionResult()
        assert result.execution_record is None
        assert result.exit_code == 0
        assert result.stdout == ""
        assert result.stderr == ""
        assert result.runner_metadata == {}
        assert result.timed_out is False
        assert result.error == ""

    def test_success_with_record(self) -> None:
        """ExecutionResult holds a full ExecutionRecord on success."""
        record = ExecutionRecord(
            final_content="review complete",
            model_used="claude-sonnet-4-5",
            total_llm_calls=2,
            stop_reason="end_turn",
            input_tokens=100,
            output_tokens=50,
        )
        result = ExecutionResult(
            execution_record=record,
            exit_code=0,
            stdout="review complete",
        )
        assert result.execution_record is record
        assert result.exit_code == 0
        assert result.execution_record.total_llm_calls == 2

    def test_timeout_result(self) -> None:
        """ExecutionResult correctly models a timeout scenario."""
        result = ExecutionResult(
            exit_code=137,  # SIGKILL
            timed_out=True,
            error="Execution killed after 300 seconds.",
        )
        assert result.timed_out is True
        assert result.exit_code == 137
        assert "300 seconds" in result.error

    def test_independent_default_dicts(self) -> None:
        """Each ExecutionResult instance gets its own runner_metadata."""
        r1 = ExecutionResult()
        r2 = ExecutionResult()
        r1.runner_metadata["container_id"] = "abc"
        assert r2.runner_metadata == {}


# ---------------------------------------------------------------------------
# RunnerProtocol isinstance tests
# ---------------------------------------------------------------------------


class TestRunnerProtocolCompliance:
    """Tests for RunnerProtocol runtime isinstance checks."""

    def test_complete_runner_satisfies_protocol(self) -> None:
        """A class with all required methods satisfies RunnerProtocol."""
        runner = _MinimalRunner()
        assert isinstance(runner, RunnerProtocol)

    def test_incomplete_runner_does_not_satisfy_protocol(self) -> None:
        """A class missing execute() does not satisfy RunnerProtocol."""
        runner = _IncompleteRunner()
        assert not isinstance(runner, RunnerProtocol)

    def test_plain_object_does_not_satisfy_protocol(self) -> None:
        """A plain object without any runner methods does not satisfy protocol."""
        assert not isinstance(object(), RunnerProtocol)

    def test_protocol_is_runtime_checkable(self) -> None:
        """RunnerProtocol supports isinstance() without raising TypeError."""
        # If @runtime_checkable is missing, isinstance raises TypeError.
        try:
            result = isinstance(_MinimalRunner(), RunnerProtocol)
            assert isinstance(result, bool)
        except TypeError:
            pytest.fail("RunnerProtocol is not @runtime_checkable")


# ---------------------------------------------------------------------------
# RunnerProtocol method signature integration
# ---------------------------------------------------------------------------


class TestRunnerProtocolUsage:
    """Tests that verify the protocol's methods work correctly end-to-end."""

    def test_provision_returns_runner_context(self) -> None:
        """provision() returns a RunnerContext with populated fields."""
        runner = _MinimalRunner()
        safety = SafetyBlock()
        ctx = runner.provision(safety_block=safety, resolved_inputs={"diff": "..."})

        assert isinstance(ctx, RunnerContext)
        assert ctx.container_id == "ctr-001"
        assert ctx.network_id == "net-001"
        assert ctx.mount_mappings == {"/host/repo": "/workspace"}
        assert ctx.metadata["image"] == "agentry/sandbox:1.0"

    def test_execute_returns_execution_result(self) -> None:
        """execute() returns an ExecutionResult with a populated record."""
        runner = _MinimalRunner()
        ctx = RunnerContext(container_id="ctr-001")
        config = AgentConfig(
            system_prompt="test",
            resolved_inputs={},
            tool_names=[],
        )
        result = runner.execute(runner_context=ctx, agent_config=config)

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == 0
        assert result.execution_record is not None
        assert result.execution_record.final_content == "done"

    def test_teardown_is_idempotent(self) -> None:
        """teardown() can be called multiple times without raising."""
        runner = _MinimalRunner()
        ctx = RunnerContext()
        # Should not raise on first or second call.
        runner.teardown(ctx)
        runner.teardown(ctx)

    def test_check_available_returns_runner_status(self) -> None:
        """check_available() returns a RunnerStatus."""
        runner = _MinimalRunner()
        status = runner.check_available()

        assert isinstance(status, RunnerStatus)
        assert status.available is True

    def test_full_lifecycle(self) -> None:
        """Full provision -> execute -> teardown lifecycle succeeds."""
        runner = _MinimalRunner()
        safety = SafetyBlock()

        # 1. Check availability.
        status = runner.check_available()
        assert status.available

        # 2. Provision.
        ctx = runner.provision(safety_block=safety, resolved_inputs={})
        assert ctx.container_id == "ctr-001"

        # 3. Execute.
        agent_config = AgentConfig(
            system_prompt="You are a reviewer.",
            resolved_inputs={},
            tool_names=["repository:read"],
            timeout=300.0,
        )
        result = runner.execute(runner_context=ctx, agent_config=agent_config)
        assert result.exit_code == 0
        assert result.execution_record is not None

        # 4. Teardown.
        runner.teardown(ctx)  # Should not raise.


# ---------------------------------------------------------------------------
# Public re-export via __init__.py
# ---------------------------------------------------------------------------


class TestPublicAPI:
    """Tests that runners package exports the expected symbols."""

    def test_imports_from_package(self) -> None:
        """All public symbols are importable from agentry.runners."""
        from agentry.runners import (
            AgentConfig,
            ExecutionResult,
            RunnerContext,
            RunnerProtocol,
            RunnerStatus,
        )

        # Verify they are the same objects as from the protocol module.
        from agentry.runners.protocol import (
            AgentConfig as _AgentConfig,
        )
        from agentry.runners.protocol import (
            ExecutionResult as _ExecutionResult,
        )
        from agentry.runners.protocol import (
            RunnerContext as _RunnerContext,
        )
        from agentry.runners.protocol import (
            RunnerProtocol as _RunnerProtocol,
        )
        from agentry.runners.protocol import (
            RunnerStatus as _RunnerStatus,
        )

        assert AgentConfig is _AgentConfig
        assert ExecutionResult is _ExecutionResult
        assert RunnerContext is _RunnerContext
        assert RunnerProtocol is _RunnerProtocol
        assert RunnerStatus is _RunnerStatus


# ---------------------------------------------------------------------------
# InProcessRunner tests
# ---------------------------------------------------------------------------


class TestInProcessRunner:
    """Tests for T02: InProcessRunner delegates to AgentProtocol."""

    def _make_mock_agent(
        self,
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

    def test_in_process_runner_satisfies_protocol(self) -> None:
        """InProcessRunner satisfies the RunnerProtocol."""
        from agentry.runners.in_process import InProcessRunner

        mock_agent = self._make_mock_agent()
        runner = InProcessRunner(agent=mock_agent)
        assert isinstance(runner, RunnerProtocol)

    def test_init_accepts_agent_protocol(self) -> None:
        """InProcessRunner.__init__ accepts an agent parameter, not llm_client."""
        from agentry.runners.in_process import InProcessRunner

        mock_agent = self._make_mock_agent()
        runner = InProcessRunner(agent=mock_agent)
        assert runner.agent is mock_agent

    def test_init_does_not_require_llm_client(self) -> None:
        """InProcessRunner can be constructed without llm_client."""
        from agentry.runners.in_process import InProcessRunner

        mock_agent = self._make_mock_agent()
        # This should not raise a TypeError about llm_client.
        runner = InProcessRunner(agent=mock_agent)
        assert runner is not None

    def test_no_agent_executor_import(self) -> None:
        """InProcessRunner module does not import AgentExecutor."""
        import inspect

        from agentry.runners import in_process

        source = inspect.getsource(in_process)
        assert "AgentExecutor" not in source, "AgentExecutor should not be used"

    def test_no_llm_client_reference(self) -> None:
        """InProcessRunner module does not reference LLMClient."""
        import inspect

        from agentry.runners import in_process

        source = inspect.getsource(in_process)
        assert "llm_client" not in source, "llm_client should be removed"

    def test_provision_returns_context_with_no_isolation(self) -> None:
        """provision() returns RunnerContext with no container/network IDs."""
        from agentry.runners.in_process import InProcessRunner

        runner = InProcessRunner(agent=self._make_mock_agent())
        safety = SafetyBlock()
        ctx = runner.provision(safety_block=safety, resolved_inputs={})

        assert isinstance(ctx, RunnerContext)
        assert ctx.container_id == ""
        assert ctx.network_id == ""
        assert ctx.mount_mappings == {}
        assert ctx.metadata["runner_type"] == "in_process"

    def test_provision_logs_elevated_trust_warning(self, caplog) -> None:
        """provision() logs a warning about elevated trust mode."""
        from agentry.runners.in_process import InProcessRunner

        runner = InProcessRunner(agent=self._make_mock_agent())
        safety = SafetyBlock()

        with caplog.at_level(logging.WARNING):
            runner.provision(safety_block=safety, resolved_inputs={})

        assert any(
            "elevated trust mode" in record.message.lower()
            and "no runner isolation" in record.message.lower()
            for record in caplog.records
        )

    def test_execute_delegates_to_agent_protocol(self) -> None:
        """execute() delegates to agent.execute() with an AgentTask."""
        from agentry.agents.models import AgentTask
        from agentry.runners.in_process import InProcessRunner

        mock_agent = self._make_mock_agent(raw_output="execution result")
        runner = InProcessRunner(agent=mock_agent)

        ctx = RunnerContext()
        config = AgentConfig(
            system_prompt="test",
            resolved_inputs={"input1": "value1"},
            tool_names=["read"],
        )

        result = runner.execute(runner_context=ctx, agent_config=config)

        # Verify agent.execute was called with an AgentTask.
        mock_agent.execute.assert_called_once()
        call_args = mock_agent.execute.call_args
        task_arg = call_args[0][0] if call_args[0] else call_args[1].get("agent_task")
        assert isinstance(task_arg, AgentTask)
        assert task_arg.system_prompt == "test"
        assert task_arg.tool_names == ["read"]

    def test_execute_populates_result_from_agent_result(self) -> None:
        """execute() maps AgentResult fields onto ExecutionResult."""
        from agentry.runners.in_process import InProcessRunner

        mock_agent = self._make_mock_agent(
            raw_output="execution result",
            exit_code=0,
            timed_out=False,
            error="",
            output={"key": "value"},
            input_tokens=100,
            output_tokens=50,
            tool_invocations=[{"tool": "read", "result": "ok"}],
        )
        runner = InProcessRunner(agent=mock_agent)

        ctx = RunnerContext()
        config = AgentConfig(
            system_prompt="test",
            resolved_inputs={},
            tool_names=[],
        )

        result = runner.execute(runner_context=ctx, agent_config=config)

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == 0
        assert result.stdout == "execution result"
        assert result.stderr == ""
        assert result.timed_out is False
        assert result.runner_metadata["runner_type"] == "in_process"
        assert result.output == {"key": "value"}
        assert result.token_usage["input_tokens"] == 100
        assert result.token_usage["output_tokens"] == 50
        assert result.tool_invocations == [{"tool": "read", "result": "ok"}]

    def test_execute_sets_error_exit_code_on_failure(self) -> None:
        """execute() sets exit_code=1 when execution has an error."""
        from agentry.runners.in_process import InProcessRunner

        mock_agent = self._make_mock_agent(
            raw_output="",
            exit_code=0,
            error="Agent failed",
        )
        runner = InProcessRunner(agent=mock_agent)
        ctx = RunnerContext()
        config = AgentConfig(
            system_prompt="test",
            resolved_inputs={},
            tool_names=[],
        )

        result = runner.execute(runner_context=ctx, agent_config=config)

        assert result.exit_code == 1
        assert result.stderr == "Agent failed"
        assert result.error == "Agent failed"

    def test_execute_handles_timeout(self) -> None:
        """execute() correctly reflects timeout from AgentResult."""
        from agentry.runners.in_process import InProcessRunner

        mock_agent = self._make_mock_agent(
            raw_output="",
            exit_code=1,
            timed_out=True,
            error="Timeout after 300 seconds",
        )
        runner = InProcessRunner(agent=mock_agent)
        ctx = RunnerContext()
        config = AgentConfig(
            system_prompt="test",
            resolved_inputs={},
            tool_names=[],
        )

        result = runner.execute(runner_context=ctx, agent_config=config)

        assert result.timed_out is True
        assert result.exit_code == 1

    def test_teardown_is_no_op(self) -> None:
        """teardown() is a no-op and never raises."""
        from agentry.runners.in_process import InProcessRunner

        runner = InProcessRunner(agent=self._make_mock_agent())
        ctx = RunnerContext()
        # Should not raise
        runner.teardown(ctx)
        # Should be idempotent
        runner.teardown(ctx)

    def test_check_available_always_returns_true(self) -> None:
        """check_available() always returns available=True."""
        from agentry.runners.in_process import InProcessRunner

        runner = InProcessRunner(agent=self._make_mock_agent())
        status = runner.check_available()

        assert isinstance(status, RunnerStatus)
        assert status.available is True
        assert "available" in status.message.lower()

    def test_full_in_process_lifecycle(self) -> None:
        """Full lifecycle: check_available -> provision -> execute -> teardown."""
        from agentry.runners.in_process import InProcessRunner

        mock_agent = self._make_mock_agent(raw_output="done")
        runner = InProcessRunner(agent=mock_agent)
        safety = SafetyBlock()

        # 1. Check availability
        status = runner.check_available()
        assert status.available

        # 2. Provision
        ctx = runner.provision(safety_block=safety, resolved_inputs={})
        assert ctx.container_id == ""
        assert ctx.metadata["runner_type"] == "in_process"

        # 3. Execute
        config = AgentConfig(
            system_prompt="You are a reviewer.",
            resolved_inputs={},
            tool_names=["repository:read"],
            timeout=300.0,
        )

        result = runner.execute(runner_context=ctx, agent_config=config)
        assert result.exit_code == 0
        assert result.stdout == "done"

        # 4. Teardown (should not raise)
        runner.teardown(ctx)


# ---------------------------------------------------------------------------
# RunnerDetector tests
# ---------------------------------------------------------------------------


class TestRunnerDetector:
    """Tests for T02: RunnerDetector resolves agent by name and injects into runner."""

    def _make_registry(self) -> "AgentRegistry":
        """Return a default AgentRegistry for testing."""
        from agentry.agents.registry import AgentRegistry

        return AgentRegistry.default()

    def test_get_runner_elevated_trust_returns_in_process_runner(self) -> None:
        """get_runner() returns InProcessRunner when trust: elevated."""
        from agentry.runners.detector import RunnerDetector
        from agentry.runners.in_process import InProcessRunner

        detector = RunnerDetector(agent_registry=self._make_registry())
        safety = SafetyBlock(trust="elevated")

        runner = detector.get_runner(safety)

        assert isinstance(runner, InProcessRunner)

    def test_get_runner_elevated_injects_agent(self) -> None:
        """get_runner() resolves agent from registry and injects into InProcessRunner."""
        from unittest.mock import MagicMock

        from agentry.agents.registry import AgentRegistry
        from agentry.runners.detector import RunnerDetector
        from agentry.runners.in_process import InProcessRunner

        mock_agent = MagicMock()
        mock_registry = MagicMock(spec=AgentRegistry)
        mock_registry.get.return_value = mock_agent

        detector = RunnerDetector(
            agent_registry=mock_registry,
            agent_name="claude-code",
        )
        safety = SafetyBlock(trust="elevated")

        runner = detector.get_runner(safety)

        assert isinstance(runner, InProcessRunner)
        assert runner.agent is mock_agent
        mock_registry.get.assert_called_once_with("claude-code")

    def test_get_runner_elevated_forwards_agent_kwargs(self) -> None:
        """get_runner() forwards agent_kwargs to the registry factory."""
        from unittest.mock import MagicMock

        from agentry.agents.registry import AgentRegistry
        from agentry.runners.detector import RunnerDetector

        mock_agent = MagicMock()
        mock_registry = MagicMock(spec=AgentRegistry)
        mock_registry.get.return_value = mock_agent

        detector = RunnerDetector(
            agent_registry=mock_registry,
            agent_name="claude-code",
            agent_kwargs={"model": "claude-opus-4-5"},
        )
        safety = SafetyBlock(trust="elevated")

        detector.get_runner(safety)

        mock_registry.get.assert_called_once_with(
            "claude-code", model="claude-opus-4-5"
        )

    def test_get_runner_sandboxed_docker_available_returns_docker_runner(
        self,
    ) -> None:
        """get_runner() returns DockerRunner when trust: sandboxed and Docker available."""
        from agentry.runners.detector import RunnerDetector
        from agentry.runners.docker_runner import DockerRunner

        # Mock Docker client
        mock_docker_client = MagicMock()
        mock_docker_client.ping.return_value = True

        detector = RunnerDetector(
            agent_registry=self._make_registry(),
            docker_client=mock_docker_client,
        )
        safety = SafetyBlock(trust="sandboxed")

        runner = detector.get_runner(safety)

        assert isinstance(runner, DockerRunner)

    def test_get_runner_sandboxed_docker_unavailable_raises_error(
        self,
    ) -> None:
        """get_runner() raises error when trust: sandboxed but Docker unavailable."""
        from agentry.runners.detector import RunnerDetector

        # Mock Docker client that is unavailable
        mock_docker_client = MagicMock()
        mock_docker_client.ping.side_effect = Exception("Docker daemon not available")

        detector = RunnerDetector(
            agent_registry=self._make_registry(),
            docker_client=mock_docker_client,
        )
        safety = SafetyBlock(trust="sandboxed")

        with pytest.raises(RuntimeError) as exc_info:
            detector.get_runner(safety)

        error_msg = str(exc_info.value)
        assert "Docker is required for sandboxed execution" in error_msg
        assert "trust: elevated" in error_msg

    def test_get_runner_error_message_helpful(self) -> None:
        """Error message from get_runner() provides helpful guidance."""
        from agentry.runners.detector import RunnerDetector

        mock_docker_client = MagicMock()
        mock_docker_client.ping.side_effect = Exception("Cannot connect to Docker")

        detector = RunnerDetector(
            agent_registry=self._make_registry(),
            docker_client=mock_docker_client,
        )
        safety = SafetyBlock(trust="sandboxed")

        with pytest.raises(RuntimeError) as exc_info:
            detector.get_runner(safety)

        error_msg = str(exc_info.value)
        # Check that the error message suggests remediation
        assert "Install Docker" in error_msg or "set trust: elevated" in error_msg

    def test_get_runner_default_trust_is_sandboxed(self) -> None:
        """Default trust level is sandboxed, requiring Docker."""
        from agentry.runners.detector import RunnerDetector

        # Create SafetyBlock with no explicit trust (defaults to sandboxed)
        safety = SafetyBlock()
        assert safety.trust.value == "sandboxed"

        # With unavailable Docker, should raise
        mock_docker_client = MagicMock()
        mock_docker_client.ping.side_effect = Exception("Docker unavailable")

        detector = RunnerDetector(
            agent_registry=self._make_registry(),
            docker_client=mock_docker_client,
        )

        with pytest.raises(RuntimeError):
            detector.get_runner(safety)

    def test_get_runner_with_docker_available_succeeds(self) -> None:
        """get_runner() succeeds with default sandboxed trust when Docker available."""
        from agentry.runners.detector import RunnerDetector
        from agentry.runners.docker_runner import DockerRunner

        # Mock Docker client that responds successfully
        mock_docker_client = MagicMock()
        mock_docker_client.ping.return_value = True

        detector = RunnerDetector(
            agent_registry=self._make_registry(),
            docker_client=mock_docker_client,
        )
        safety = SafetyBlock()  # Default: trust: sandboxed

        runner = detector.get_runner(safety)

        assert isinstance(runner, DockerRunner)

    def test_elevated_trust_does_not_check_docker(self) -> None:
        """get_runner() with elevated trust never calls docker.ping()."""
        from agentry.runners.detector import RunnerDetector
        from agentry.runners.in_process import InProcessRunner

        # Mock Docker client with side effect to detect calls
        mock_docker_client = MagicMock()
        mock_docker_client.ping.side_effect = Exception(
            "Should not be called for elevated trust"
        )

        detector = RunnerDetector(
            agent_registry=self._make_registry(),
            docker_client=mock_docker_client,
        )
        safety = SafetyBlock(trust="elevated")

        # Should not raise, and should not call ping()
        runner = detector.get_runner(safety)

        assert isinstance(runner, InProcessRunner)
        mock_docker_client.ping.assert_not_called()

    def test_get_runner_returned_runner_satisfies_protocol(self) -> None:
        """Runners returned by get_runner() satisfy RunnerProtocol."""
        from agentry.runners.detector import RunnerDetector

        mock_docker_client = MagicMock()
        mock_docker_client.ping.return_value = True

        detector = RunnerDetector(
            agent_registry=self._make_registry(),
            docker_client=mock_docker_client,
        )

        # Test with elevated trust
        safety_elevated = SafetyBlock(trust="elevated")
        runner_elevated = detector.get_runner(safety_elevated)
        assert isinstance(runner_elevated, RunnerProtocol)

        # Test with sandboxed trust
        safety_sandboxed = SafetyBlock(trust="sandboxed")
        runner_sandboxed = detector.get_runner(safety_sandboxed)
        assert isinstance(runner_sandboxed, RunnerProtocol)

    def test_get_runner_resolves_claude_code_agent(self) -> None:
        """get_runner() with elevated trust resolves ClaudeCodeAgent from default registry."""
        from agentry.agents.claude_code import ClaudeCodeAgent
        from agentry.runners.detector import RunnerDetector
        from agentry.runners.in_process import InProcessRunner

        registry = self._make_registry()
        detector = RunnerDetector(
            agent_registry=registry, agent_name="claude-code"
        )
        safety = SafetyBlock(trust="elevated")

        runner = detector.get_runner(safety)

        assert isinstance(runner, InProcessRunner)
        assert isinstance(runner.agent, ClaudeCodeAgent)
