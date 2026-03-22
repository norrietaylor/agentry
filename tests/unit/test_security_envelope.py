"""Unit tests for T04: SecurityEnvelope delegates to runner, no executor.

Tests cover:
- strip_tools() filters available tools to manifest-only set.
- SecurityEnvelope strips undeclared tools before execution.
- SecurityEnvelope aborts when abort_on_strip=True and tools exceed manifest.
- Preflight check failures abort execution before agent runs.
- Runner teardown always runs, even on failure.
- Runner provisioning is called before execution.
- Output validation pipeline runs on successful execution with structured output.
- Execution without structured output skips validation.
- Multiple preflight checks run in order; first failure aborts.
- EnvelopeResult captures stripped and allowed tool lists.
- ToolManifestViolationError contains excess and manifest tool sets.
- PreflightError contains check name, message, and remediation.
- SecurityEnvelope delegates agent execution to runner.execute(), not an executor.
- RunnerProtocol imported from runners.protocol (no duplicate definition).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from agentry.models.identity import IdentityBlock
from agentry.models.output import OutputBlock, SideEffect
from agentry.models.safety import SafetyBlock
from agentry.models.tools import ToolsBlock
from agentry.models.workflow import WorkflowDefinition
from agentry.runners.protocol import (
    AgentConfig,
    ExecutionResult,
    RunnerContext,
    RunnerProtocol,
    RunnerStatus,
)
from agentry.security.envelope import (
    EnvelopeResult,
    PreflightCheckResult,
    PreflightError,
    SecurityEnvelope,
    SecurityEnvelopeError,
    ToolManifestViolationError,
    strip_tools,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_workflow(
    tool_capabilities: list[str] | None = None,
    schema_def: dict[str, Any] | None = None,
    side_effects: list[SideEffect] | None = None,
    output_paths: list[str] | None = None,
) -> WorkflowDefinition:
    """Build a minimal WorkflowDefinition for testing."""
    tools = ToolsBlock(capabilities=tool_capabilities or [])
    output = OutputBlock(
        schema=schema_def or {},
        side_effects=side_effects or [],
        output_paths=output_paths or [],
    )
    return WorkflowDefinition(
        identity=IdentityBlock(
            name="test-workflow", version="1.0.0", description="Test workflow"
        ),
        tools=tools,
        output=output,
    )


class MockRunner:
    """Mock runner satisfying the canonical RunnerProtocol."""

    def __init__(
        self,
        provision_result: dict[str, Any] | None = None,
        provision_error: Exception | None = None,
        teardown_error: Exception | None = None,
        execution_output: dict[str, Any] | None = None,
        execution_error: str = "",
        execution_side_effect: Exception | None = None,
    ) -> None:
        self.provision_result = provision_result or {"container_id": "abc123"}
        self.provision_error = provision_error
        self.teardown_error = teardown_error
        self.execution_output = execution_output
        self.execution_error = execution_error
        self.execution_side_effect = execution_side_effect
        self.provisioned = False
        self.torn_down = False
        self.last_runner_context: RunnerContext | None = None
        self.last_agent_config: AgentConfig | None = None

    def provision(
        self,
        safety_block: Any,
        resolved_inputs: dict[str, str],
    ) -> RunnerContext:
        if self.provision_error:
            raise self.provision_error
        self.provisioned = True
        return RunnerContext(metadata=self.provision_result)

    def execute(
        self,
        runner_context: RunnerContext,
        agent_config: AgentConfig,
    ) -> ExecutionResult:
        self.last_runner_context = runner_context
        self.last_agent_config = agent_config
        if self.execution_side_effect:
            raise self.execution_side_effect
        return ExecutionResult(
            output=self.execution_output,
            error=self.execution_error,
            tool_invocations=[],
        )

    def teardown(self, runner_context: RunnerContext) -> None:
        self.torn_down = True
        if self.teardown_error:
            raise self.teardown_error

    def check_available(self) -> RunnerStatus:
        return RunnerStatus(available=True)


@dataclass
class MockPreflightCheck:
    """Mock preflight check."""

    _name: str
    _passed: bool = True
    _message: str = ""
    _remediation: str = ""

    @property
    def name(self) -> str:
        return self._name

    def run(self) -> PreflightCheckResult:
        return PreflightCheckResult(
            passed=self._passed,
            name=self._name,
            message=self._message,
            remediation=self._remediation,
        )


# ---------------------------------------------------------------------------
# strip_tools tests
# ---------------------------------------------------------------------------


class TestStripTools:
    """Tests for the strip_tools() function."""

    def test_all_tools_in_manifest(self) -> None:
        """When all available tools are in the manifest, none are stripped."""
        allowed, stripped = strip_tools(
            available_tools=["read_file", "write_file"],
            manifest_tools=["read_file", "write_file"],
        )
        assert allowed == ["read_file", "write_file"]
        assert stripped == []

    def test_some_tools_stripped(self) -> None:
        """Tools not in the manifest are stripped."""
        allowed, stripped = strip_tools(
            available_tools=["read_file", "write_file", "shell_exec", "http_request"],
            manifest_tools=["read_file", "write_file"],
        )
        assert allowed == ["read_file", "write_file"]
        assert stripped == ["shell_exec", "http_request"]

    def test_no_tools_available(self) -> None:
        """Empty available tools produces empty results."""
        allowed, stripped = strip_tools(
            available_tools=[],
            manifest_tools=["read_file"],
        )
        assert allowed == []
        assert stripped == []

    def test_no_tools_in_manifest(self) -> None:
        """Empty manifest strips all available tools."""
        allowed, stripped = strip_tools(
            available_tools=["read_file", "write_file"],
            manifest_tools=[],
        )
        assert allowed == []
        assert stripped == ["read_file", "write_file"]

    def test_preserves_order(self) -> None:
        """Allowed tools maintain the order from available_tools."""
        allowed, stripped = strip_tools(
            available_tools=["c", "a", "b", "d"],
            manifest_tools=["b", "a"],
        )
        assert allowed == ["a", "b"]
        assert stripped == ["c", "d"]


# ---------------------------------------------------------------------------
# SecurityEnvelope tests
# ---------------------------------------------------------------------------


class TestSecurityEnvelopeToolStripping:
    """Tests for tool manifest enforcement in SecurityEnvelope."""

    def test_strips_undeclared_tools(self) -> None:
        """Undeclared tools are stripped and not passed to runner."""
        workflow = _make_workflow(tool_capabilities=["read_file", "write_file"])
        runner = MockRunner()

        envelope = SecurityEnvelope(workflow=workflow, runner=runner)
        result = envelope.execute(
            system_prompt="test",
            resolved_inputs={},
            available_tools=["read_file", "write_file", "shell_exec"],
        )

        assert result.tools_allowed == ["read_file", "write_file"]
        assert result.tools_stripped == ["shell_exec"]
        # Verify runner was called with only allowed tools.
        assert runner.last_agent_config is not None
        assert runner.last_agent_config.tool_names == ["read_file", "write_file"]

    def test_abort_on_strip_raises(self) -> None:
        """When abort_on_strip=True, exceeding the manifest raises."""
        workflow = _make_workflow(tool_capabilities=["read_file"])
        runner = MockRunner()

        envelope = SecurityEnvelope(
            workflow=workflow,
            runner=runner,
            abort_on_strip=True,
        )

        with pytest.raises(ToolManifestViolationError) as exc_info:
            envelope.execute(
                system_prompt="test",
                resolved_inputs={},
                available_tools=["read_file", "shell_exec"],
            )

        assert exc_info.value.excess_tools == {"shell_exec"}
        assert exc_info.value.manifest_tools == {"read_file"}

    def test_abort_on_strip_sets_aborted(self) -> None:
        """When abort_on_strip aborts, runner.execute is never called."""
        workflow = _make_workflow(tool_capabilities=["read_file"])
        runner = MockRunner()

        envelope = SecurityEnvelope(
            workflow=workflow,
            runner=runner,
            abort_on_strip=True,
        )

        with pytest.raises(ToolManifestViolationError):
            envelope.execute(
                system_prompt="test",
                resolved_inputs={},
                available_tools=["read_file", "dangerous_tool"],
            )

        # Runner execute should NOT have been called.
        assert runner.last_agent_config is None

    def test_no_strip_when_tools_match(self) -> None:
        """No stripping or error when tools exactly match manifest."""
        workflow = _make_workflow(tool_capabilities=["read_file", "write_file"])
        runner = MockRunner()

        envelope = SecurityEnvelope(
            workflow=workflow,
            runner=runner,
            abort_on_strip=True,
        )
        result = envelope.execute(
            system_prompt="test",
            resolved_inputs={},
            available_tools=["read_file", "write_file"],
        )

        assert result.tools_stripped == []
        assert not result.aborted


class TestSecurityEnvelopePreflightChecks:
    """Tests for preflight check enforcement."""

    def test_preflight_failure_aborts_execution(self) -> None:
        """A failing preflight check prevents agent execution."""
        workflow = _make_workflow(tool_capabilities=["read_file"])
        runner = MockRunner()

        failing_check = MockPreflightCheck(
            _name="docker_check",
            _passed=False,
            _message="Docker is not available.",
            _remediation="Install Docker.",
        )

        envelope = SecurityEnvelope(
            workflow=workflow,
            runner=runner,
            preflight_checks=[failing_check],
        )

        with pytest.raises(PreflightError) as exc_info:
            envelope.execute(
                system_prompt="test",
                resolved_inputs={},
                available_tools=["read_file"],
            )

        assert exc_info.value.check_name == "docker_check"
        assert "Docker is not available" in exc_info.value.message
        assert exc_info.value.remediation == "Install Docker."
        # Runner execute should NOT have been called.
        assert runner.last_agent_config is None

    def test_passing_preflights_allow_execution(self) -> None:
        """All passing preflight checks allow execution to proceed."""
        workflow = _make_workflow(tool_capabilities=["read_file"])
        runner = MockRunner()

        checks = [
            MockPreflightCheck(_name="api_key_check", _passed=True),
            MockPreflightCheck(_name="docker_check", _passed=True),
        ]

        envelope = SecurityEnvelope(
            workflow=workflow,
            runner=runner,
            preflight_checks=checks,
        )
        result = envelope.execute(
            system_prompt="test",
            resolved_inputs={},
            available_tools=["read_file"],
        )

        assert len(result.preflight_results) == 2
        assert all(r.passed for r in result.preflight_results)
        assert not result.aborted
        assert runner.last_agent_config is not None

    def test_first_failing_preflight_stops_remaining(self) -> None:
        """Once a preflight fails, subsequent checks are not run."""
        workflow = _make_workflow(tool_capabilities=["read_file"])
        runner = MockRunner()

        check1 = MockPreflightCheck(_name="check1", _passed=True)
        check2 = MockPreflightCheck(
            _name="check2", _passed=False, _message="Failed."
        )
        check3 = MockPreflightCheck(_name="check3", _passed=True)

        envelope = SecurityEnvelope(
            workflow=workflow,
            runner=runner,
            preflight_checks=[check1, check2, check3],
        )

        with pytest.raises(PreflightError):
            envelope.execute(
                system_prompt="test",
                resolved_inputs={},
                available_tools=["read_file"],
            )

        # Runner execute should NOT have been called.
        assert runner.last_agent_config is None


class TestSecurityEnvelopeRunnerLifecycle:
    """Tests for runner provisioning and teardown."""

    def test_runner_provisioned_before_execution(self) -> None:
        """Runner.provision() is called before agent execution."""
        workflow = _make_workflow(tool_capabilities=["read_file"])
        runner = MockRunner(provision_result={"container_id": "xyz"})

        envelope = SecurityEnvelope(workflow=workflow, runner=runner)
        result = envelope.execute(
            system_prompt="test",
            resolved_inputs={},
            available_tools=["read_file"],
        )

        assert runner.provisioned
        assert result.runner_metadata == {"container_id": "xyz"}

    def test_runner_teardown_on_success(self) -> None:
        """Runner.teardown() is called after successful execution."""
        workflow = _make_workflow(tool_capabilities=["read_file"])
        runner = MockRunner()

        envelope = SecurityEnvelope(workflow=workflow, runner=runner)
        envelope.execute(
            system_prompt="test",
            resolved_inputs={},
            available_tools=["read_file"],
        )

        assert runner.torn_down

    def test_runner_teardown_on_preflight_failure(self) -> None:
        """Runner.teardown() is called even when preflight fails."""
        workflow = _make_workflow(tool_capabilities=["read_file"])
        runner = MockRunner()

        failing_check = MockPreflightCheck(
            _name="fail", _passed=False, _message="Bad."
        )

        envelope = SecurityEnvelope(
            workflow=workflow,
            runner=runner,
            preflight_checks=[failing_check],
        )

        with pytest.raises(PreflightError):
            envelope.execute(
                system_prompt="test",
                resolved_inputs={},
                available_tools=["read_file"],
            )

        assert runner.torn_down

    def test_runner_teardown_on_execution_error(self) -> None:
        """Runner.teardown() is called even when runner.execute raises."""
        workflow = _make_workflow(tool_capabilities=["read_file"])
        runner = MockRunner(
            execution_side_effect=RuntimeError("Runner exploded")
        )

        envelope = SecurityEnvelope(workflow=workflow, runner=runner)
        result = envelope.execute(
            system_prompt="test",
            resolved_inputs={},
            available_tools=["read_file"],
        )

        assert runner.torn_down
        assert "Runner exploded" in result.envelope_error

    def test_teardown_error_logged_not_raised(self) -> None:
        """Teardown errors are captured but do not override other results."""
        workflow = _make_workflow(tool_capabilities=["read_file"])
        runner = MockRunner(teardown_error=RuntimeError("Teardown boom"))

        envelope = SecurityEnvelope(workflow=workflow, runner=runner)
        result = envelope.execute(
            system_prompt="test",
            resolved_inputs={},
            available_tools=["read_file"],
        )

        assert runner.torn_down
        # Teardown error captured.
        assert "Teardown boom" in result.envelope_error

    def test_runner_execute_called_with_agent_config(self) -> None:
        """SecurityEnvelope delegates execution to runner.execute with correct args."""
        workflow = _make_workflow(tool_capabilities=["read_file"])
        runner = MockRunner()

        envelope = SecurityEnvelope(workflow=workflow, runner=runner)
        envelope.execute(
            system_prompt="You are helpful.",
            resolved_inputs={"key": "value"},
            available_tools=["read_file"],
            agent_name="claude-code",
            agent_config={"model": "claude-sonnet-4-5"},
        )

        assert runner.last_agent_config is not None
        assert runner.last_agent_config.system_prompt == "You are helpful."
        assert runner.last_agent_config.resolved_inputs == {"key": "value"}
        assert runner.last_agent_config.tool_names == ["read_file"]
        assert runner.last_agent_config.agent_name == "claude-code"
        assert runner.last_agent_config.agent_config == {"model": "claude-sonnet-4-5"}


class TestSecurityEnvelopeValidation:
    """Tests for output validation pipeline integration."""

    def test_validation_runs_on_structured_output(self) -> None:
        """When runner returns structured JSON output, validation pipeline runs."""
        workflow = _make_workflow(
            tool_capabilities=["read_file"],
            schema_def={
                "type": "object",
                "properties": {"result": {"type": "string"}},
                "required": ["result"],
            },
        )
        runner = MockRunner(execution_output={"result": "All good"})

        envelope = SecurityEnvelope(workflow=workflow, runner=runner)
        result = envelope.execute(
            system_prompt="test",
            resolved_inputs={},
            available_tools=["read_file"],
        )

        assert result.validation_result is not None
        assert result.validation_result.validation_status == "passed"

    def test_no_validation_without_structured_output(self) -> None:
        """When runner returns no output, validation is skipped."""
        workflow = _make_workflow(tool_capabilities=["read_file"])
        runner = MockRunner(execution_output=None)

        envelope = SecurityEnvelope(workflow=workflow, runner=runner)
        result = envelope.execute(
            system_prompt="test",
            resolved_inputs={},
            available_tools=["read_file"],
        )

        assert result.validation_result is None

    def test_no_validation_when_execution_errors(self) -> None:
        """When runner reports an error, validation is skipped."""
        workflow = _make_workflow(tool_capabilities=["read_file"])
        runner = MockRunner(
            execution_output={"result": "data"},
            execution_error="Timeout exceeded",
        )

        envelope = SecurityEnvelope(workflow=workflow, runner=runner)
        result = envelope.execute(
            system_prompt="test",
            resolved_inputs={},
            available_tools=["read_file"],
        )

        assert result.validation_result is None

    def test_validation_failure_captured(self) -> None:
        """Schema validation failure is captured in the result."""
        workflow = _make_workflow(
            tool_capabilities=["read_file"],
            schema_def={
                "type": "object",
                "properties": {"count": {"type": "integer"}},
                "required": ["count"],
            },
        )
        runner = MockRunner(execution_output={"count": "not-an-integer"})

        envelope = SecurityEnvelope(workflow=workflow, runner=runner)
        result = envelope.execute(
            system_prompt="test",
            resolved_inputs={},
            available_tools=["read_file"],
        )

        assert result.validation_result is not None
        assert result.validation_result.validation_status == "failed"


class TestExceptionModels:
    """Tests for exception data models."""

    def test_tool_manifest_violation_error_fields(self) -> None:
        """ToolManifestViolationError carries excess and manifest sets."""
        exc = ToolManifestViolationError(
            excess_tools={"shell_exec", "http_request"},
            manifest_tools={"read_file"},
        )
        assert exc.excess_tools == {"shell_exec", "http_request"}
        assert exc.manifest_tools == {"read_file"}
        assert "shell_exec" in str(exc) or "http_request" in str(exc)

    def test_preflight_error_fields(self) -> None:
        """PreflightError carries check name, message, and remediation."""
        exc = PreflightError(
            check_name="docker",
            message="Docker not found.",
            remediation="Install Docker Desktop.",
        )
        assert exc.check_name == "docker"
        assert exc.message == "Docker not found."
        assert exc.remediation == "Install Docker Desktop."
        assert "docker" in str(exc)
        assert "Docker not found" in str(exc)
        assert "Install Docker Desktop" in str(exc)

    def test_preflight_error_without_remediation(self) -> None:
        """PreflightError works without remediation."""
        exc = PreflightError(
            check_name="api_key",
            message="Key missing.",
        )
        assert exc.remediation == ""
        assert "Key missing" in str(exc)

    def test_security_envelope_error_is_base(self) -> None:
        """Both specific errors inherit from SecurityEnvelopeError."""
        assert issubclass(ToolManifestViolationError, SecurityEnvelopeError)
        assert issubclass(PreflightError, SecurityEnvelopeError)


class TestEnvelopeResult:
    """Tests for the EnvelopeResult data class."""

    def test_default_values(self) -> None:
        """Default EnvelopeResult has sensible defaults."""
        result = EnvelopeResult()
        assert result.execution_result is None
        assert result.validation_result is None
        assert result.tools_stripped == []
        assert result.tools_allowed == []
        assert result.preflight_results == []
        assert result.runner_metadata == {}
        assert result.envelope_error == ""
        assert result.aborted is False


class TestRunnerProtocolImport:
    """Tests that RunnerProtocol is imported from runners.protocol (not duplicated)."""

    def test_runner_protocol_from_runners_module(self) -> None:
        """RunnerProtocol should be importable from runners.protocol."""
        from agentry.runners.protocol import RunnerProtocol as CanonicalProtocol

        runner = MockRunner()
        assert isinstance(runner, CanonicalProtocol)

    def test_mock_runner_satisfies_protocol(self) -> None:
        """MockRunner satisfies the canonical RunnerProtocol."""
        runner = MockRunner()
        assert isinstance(runner, RunnerProtocol)

    def test_no_executor_parameter_in_envelope(self) -> None:
        """SecurityEnvelope.__init__ no longer accepts executor parameter."""
        import inspect

        sig = inspect.signature(SecurityEnvelope.__init__)
        assert "executor" not in sig.parameters
