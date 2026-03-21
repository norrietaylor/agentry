"""Unit tests for T01.2: RunnerProtocol and data models.

Tests cover:
- RunnerContext dataclass defaults and field assignment.
- RunnerStatus dataclass defaults and field assignment.
- AgentConfig dataclass field assignment.
- ExecutionResult dataclass defaults and field assignment.
- RunnerProtocol isinstance checks via @runtime_checkable.
- Concrete implementation satisfies protocol.
- Protocol rejects objects missing required methods.
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
            llm_config=object(),
        )
        assert config.system_prompt == "You are a code reviewer."
        assert config.resolved_inputs == {"diff": "--- a/file.py\n+++ b/file.py"}
        assert config.tool_names == ["repository:read"]
        assert config.retry_config is None
        assert config.timeout is None

    def test_optional_fields(self) -> None:
        """AgentConfig accepts optional retry_config and timeout."""
        config = AgentConfig(
            system_prompt="test",
            resolved_inputs={},
            tool_names=[],
            llm_config=None,
            retry_config="mock-retry",
            timeout=120.0,
        )
        assert config.retry_config == "mock-retry"
        assert config.timeout == 120.0


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
        from agentry.llm.models import LLMConfig

        runner = _MinimalRunner()
        ctx = RunnerContext(container_id="ctr-001")
        config = AgentConfig(
            system_prompt="test",
            resolved_inputs={},
            tool_names=[],
            llm_config=LLMConfig(model="claude-sonnet-4-5", max_tokens=4096),
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
        from agentry.llm.models import LLMConfig

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
            llm_config=LLMConfig(model="claude-sonnet-4-5", max_tokens=4096),
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
    """Tests for T01.3: InProcessRunner implementation."""

    def test_in_process_runner_satisfies_protocol(self) -> None:
        """InProcessRunner satisfies the RunnerProtocol."""
        from agentry.runners.in_process import InProcessRunner

        runner = InProcessRunner(llm_client=object())
        assert isinstance(runner, RunnerProtocol)

    def test_provision_returns_context_with_no_isolation(self) -> None:
        """provision() returns RunnerContext with no container/network IDs."""
        from agentry.runners.in_process import InProcessRunner

        runner = InProcessRunner(llm_client=object())
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

        runner = InProcessRunner(llm_client=object())
        safety = SafetyBlock()

        with caplog.at_level(logging.WARNING):
            runner.provision(safety_block=safety, resolved_inputs={})

        assert any(
            "elevated trust mode" in record.message.lower()
            and "no runner isolation" in record.message.lower()
            for record in caplog.records
        )

    def test_execute_delegates_to_agent_executor(self) -> None:
        """execute() delegates to AgentExecutor and wraps result."""
        from unittest.mock import MagicMock

        from agentry.runners.in_process import InProcessRunner

        # Mock the AgentExecutor
        mock_executor = MagicMock()
        mock_record = ExecutionRecord(
            final_content="execution result",
            model_used="claude-test",
            total_llm_calls=1,
            stop_reason="end_turn",
            timed_out=False,
            error="",
        )
        mock_executor.run.return_value = mock_record

        # Create a runner with a mock that won't be used
        # (we'll patch AgentExecutor)
        runner = InProcessRunner(llm_client=MagicMock())

        ctx = RunnerContext()
        config = AgentConfig(
            system_prompt="test",
            resolved_inputs={},
            tool_names=[],
            llm_config=object(),
        )

        # Patch AgentExecutor inside the execute method
        from unittest.mock import patch

        with patch(
            "agentry.runners.in_process.AgentExecutor"
        ) as mock_executor_class:
            mock_executor_instance = MagicMock()
            mock_executor_class.return_value = mock_executor_instance
            mock_executor_instance.run.return_value = mock_record

            result = runner.execute(runner_context=ctx, agent_config=config)

            # Verify the result was wrapped correctly
            assert isinstance(result, ExecutionResult)
            assert result.execution_record is mock_record
            assert result.exit_code == 0
            assert result.stdout == "execution result"
            assert result.stderr == ""
            assert result.timed_out is False
            assert result.runner_metadata["runner_type"] == "in_process"

    def test_execute_sets_error_exit_code_on_failure(self) -> None:
        """execute() sets exit_code=1 when execution has an error."""
        from unittest.mock import patch

        from agentry.runners.in_process import InProcessRunner

        runner = InProcessRunner(llm_client=MagicMock())
        ctx = RunnerContext()
        config = AgentConfig(
            system_prompt="test",
            resolved_inputs={},
            tool_names=[],
            llm_config=object(),
        )

        # Create a record with an error
        mock_record = ExecutionRecord(
            final_content="",
            model_used="claude-test",
            total_llm_calls=1,
            stop_reason="end_turn",
            timed_out=False,
            error="LLM call failed",
        )

        with patch(
            "agentry.runners.in_process.AgentExecutor"
        ) as mock_executor_class:
            mock_executor_instance = MagicMock()
            mock_executor_class.return_value = mock_executor_instance
            mock_executor_instance.run.return_value = mock_record

            result = runner.execute(runner_context=ctx, agent_config=config)

            assert result.exit_code == 1
            assert result.stderr == "LLM call failed"

    def test_execute_handles_timeout(self) -> None:
        """execute() correctly reflects timeout from ExecutionRecord."""
        from unittest.mock import patch

        from agentry.runners.in_process import InProcessRunner

        runner = InProcessRunner(llm_client=MagicMock())
        ctx = RunnerContext()
        config = AgentConfig(
            system_prompt="test",
            resolved_inputs={},
            tool_names=[],
            llm_config=object(),
        )

        # Create a record with timeout
        mock_record = ExecutionRecord(
            final_content="",
            model_used="claude-test",
            total_llm_calls=0,
            stop_reason="",
            timed_out=True,
            error="Timeout after 300 seconds",
        )

        with patch(
            "agentry.runners.in_process.AgentExecutor"
        ) as mock_executor_class:
            mock_executor_instance = MagicMock()
            mock_executor_class.return_value = mock_executor_instance
            mock_executor_instance.run.return_value = mock_record

            result = runner.execute(runner_context=ctx, agent_config=config)

            assert result.timed_out is True
            assert result.exit_code == 1

    def test_teardown_is_no_op(self) -> None:
        """teardown() is a no-op and never raises."""
        from agentry.runners.in_process import InProcessRunner

        runner = InProcessRunner(llm_client=object())
        ctx = RunnerContext()
        # Should not raise
        runner.teardown(ctx)
        # Should be idempotent
        runner.teardown(ctx)

    def test_check_available_always_returns_true(self) -> None:
        """check_available() always returns available=True."""
        from agentry.runners.in_process import InProcessRunner

        runner = InProcessRunner(llm_client=object())
        status = runner.check_available()

        assert isinstance(status, RunnerStatus)
        assert status.available is True
        assert "available" in status.message.lower()

    def test_full_in_process_lifecycle(self) -> None:
        """Full lifecycle: check_available -> provision -> execute -> teardown."""
        from unittest.mock import MagicMock, patch

        from agentry.llm.models import LLMConfig
        from agentry.runners.in_process import InProcessRunner

        runner = InProcessRunner(llm_client=MagicMock())
        safety = SafetyBlock()

        # 1. Check availability
        status = runner.check_available()
        assert status.available

        # 2. Provision
        ctx = runner.provision(safety_block=safety, resolved_inputs={})
        assert ctx.container_id == ""
        assert ctx.metadata["runner_type"] == "in_process"

        # 3. Execute with mocked AgentExecutor
        mock_record = ExecutionRecord(
            final_content="done",
            model_used="claude-test",
            total_llm_calls=1,
            stop_reason="end_turn",
        )

        config = AgentConfig(
            system_prompt="You are a reviewer.",
            resolved_inputs={},
            tool_names=["repository:read"],
            llm_config=LLMConfig(
                model="claude-sonnet-4-5", max_tokens=4096
            ),
            timeout=300.0,
        )

        with patch(
            "agentry.runners.in_process.AgentExecutor"
        ) as mock_executor_class:
            mock_executor_instance = MagicMock()
            mock_executor_class.return_value = mock_executor_instance
            mock_executor_instance.run.return_value = mock_record

            result = runner.execute(runner_context=ctx, agent_config=config)
            assert result.exit_code == 0
            assert result.execution_record is mock_record

        # 4. Teardown (should not raise)
        runner.teardown(ctx)


# ---------------------------------------------------------------------------
# RunnerDetector tests
# ---------------------------------------------------------------------------


class TestRunnerDetector:
    """Tests for T01.6: RunnerDetector implementation."""

    def test_get_runner_elevated_trust_returns_in_process_runner(self) -> None:
        """get_runner() returns InProcessRunner when trust: elevated."""
        from agentry.runners.detector import RunnerDetector

        detector = RunnerDetector(llm_client=object())
        safety = SafetyBlock(trust="elevated")

        runner = detector.get_runner(safety)

        from agentry.runners.in_process import InProcessRunner

        assert isinstance(runner, InProcessRunner)

    def test_get_runner_sandboxed_docker_available_returns_docker_runner(
        self,
    ) -> None:
        """get_runner() returns DockerRunner when trust: sandboxed and Docker available."""
        from unittest.mock import MagicMock

        from agentry.runners.detector import RunnerDetector

        # Mock Docker client
        mock_docker_client = MagicMock()
        mock_docker_client.ping.return_value = True

        detector = RunnerDetector(
            llm_client=object(), docker_client=mock_docker_client
        )
        safety = SafetyBlock(trust="sandboxed")

        runner = detector.get_runner(safety)

        from agentry.runners.docker_runner import DockerRunner

        assert isinstance(runner, DockerRunner)

    def test_get_runner_sandboxed_docker_unavailable_raises_error(
        self,
    ) -> None:
        """get_runner() raises error when trust: sandboxed but Docker unavailable."""
        from unittest.mock import MagicMock

        from agentry.runners.detector import RunnerDetector

        # Mock Docker client that is unavailable
        mock_docker_client = MagicMock()
        mock_docker_client.ping.side_effect = Exception("Docker daemon not available")

        detector = RunnerDetector(
            llm_client=object(), docker_client=mock_docker_client
        )
        safety = SafetyBlock(trust="sandboxed")

        with pytest.raises(RuntimeError) as exc_info:
            detector.get_runner(safety)

        error_msg = str(exc_info.value)
        assert "Docker is required for sandboxed execution" in error_msg
        assert "trust: elevated" in error_msg

    def test_get_runner_error_message_helpful(self) -> None:
        """Error message from get_runner() provides helpful guidance."""
        from unittest.mock import MagicMock

        from agentry.runners.detector import RunnerDetector

        mock_docker_client = MagicMock()
        mock_docker_client.ping.side_effect = Exception("Cannot connect to Docker")

        detector = RunnerDetector(
            llm_client=object(), docker_client=mock_docker_client
        )
        safety = SafetyBlock(trust="sandboxed")

        with pytest.raises(RuntimeError) as exc_info:
            detector.get_runner(safety)

        error_msg = str(exc_info.value)
        # Check that the error message suggests remediation
        assert "Install Docker" in error_msg or "set trust: elevated" in error_msg

    def test_get_runner_default_trust_is_sandboxed(self) -> None:
        """Default trust level is sandboxed, requiring Docker."""
        from unittest.mock import MagicMock

        from agentry.runners.detector import RunnerDetector

        # Create SafetyBlock with no explicit trust (defaults to sandboxed)
        safety = SafetyBlock()
        assert safety.trust.value == "sandboxed"

        # With unavailable Docker, should raise
        mock_docker_client = MagicMock()
        mock_docker_client.ping.side_effect = Exception("Docker unavailable")

        detector = RunnerDetector(
            llm_client=object(), docker_client=mock_docker_client
        )

        with pytest.raises(RuntimeError):
            detector.get_runner(safety)

    def test_get_runner_with_docker_available_succeeds(self) -> None:
        """get_runner() succeeds with default sandboxed trust when Docker available."""
        from unittest.mock import MagicMock

        from agentry.runners.detector import RunnerDetector
        from agentry.runners.docker_runner import DockerRunner

        # Mock Docker client that responds successfully
        mock_docker_client = MagicMock()
        mock_docker_client.ping.return_value = True

        detector = RunnerDetector(
            llm_client=object(), docker_client=mock_docker_client
        )
        safety = SafetyBlock()  # Default: trust: sandboxed

        runner = detector.get_runner(safety)

        assert isinstance(runner, DockerRunner)

    def test_elevated_trust_does_not_check_docker(self) -> None:
        """get_runner() with elevated trust never calls docker.ping()."""
        from unittest.mock import MagicMock

        from agentry.runners.detector import RunnerDetector
        from agentry.runners.in_process import InProcessRunner

        # Mock Docker client with side effect to detect calls
        mock_docker_client = MagicMock()
        mock_docker_client.ping.side_effect = Exception(
            "Should not be called for elevated trust"
        )

        detector = RunnerDetector(
            llm_client=object(), docker_client=mock_docker_client
        )
        safety = SafetyBlock(trust="elevated")

        # Should not raise, and should not call ping()
        runner = detector.get_runner(safety)

        assert isinstance(runner, InProcessRunner)
        mock_docker_client.ping.assert_not_called()

    def test_get_runner_returned_runner_satisfies_protocol(self) -> None:
        """Runners returned by get_runner() satisfy RunnerProtocol."""
        from unittest.mock import MagicMock

        from agentry.runners.detector import RunnerDetector

        mock_docker_client = MagicMock()
        mock_docker_client.ping.return_value = True

        detector = RunnerDetector(
            llm_client=object(), docker_client=mock_docker_client
        )

        # Test with elevated trust
        safety_elevated = SafetyBlock(trust="elevated")
        runner_elevated = detector.get_runner(safety_elevated)
        assert isinstance(runner_elevated, RunnerProtocol)

        # Test with sandboxed trust
        safety_sandboxed = SafetyBlock(trust="sandboxed")
        runner_sandboxed = detector.get_runner(safety_sandboxed)
        assert isinstance(runner_sandboxed, RunnerProtocol)
