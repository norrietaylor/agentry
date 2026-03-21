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

from dataclasses import dataclass
from typing import Any

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
            ExecutionResult as _ExecutionResult,
            RunnerContext as _RunnerContext,
            RunnerProtocol as _RunnerProtocol,
            RunnerStatus as _RunnerStatus,
        )

        assert AgentConfig is _AgentConfig
        assert ExecutionResult is _ExecutionResult
        assert RunnerContext is _RunnerContext
        assert RunnerProtocol is _RunnerProtocol
        assert RunnerStatus is _RunnerStatus
