"""Security envelope delegating to runners for agent execution.

Provides the SecurityEnvelope class that wraps a RunnerProtocol to enforce
security controls during workflow execution. The envelope manages the full
lifecycle: tool manifest enforcement, runner provisioning, preflight checks,
agent execution (via runner), output validation, and runner teardown.

Usage::

    from agentry.security.envelope import SecurityEnvelope

    envelope = SecurityEnvelope(
        workflow=workflow_definition,
        runner=runner_instance,
    )
    result = envelope.execute(
        system_prompt="You are a code reviewer.",
        resolved_inputs={"diff": "..."},
        available_tools=["read_file", "write_file", "shell_exec"],
        agent_name="claude-code",
        agent_config={"model": "claude-sonnet-4-5"},
    )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agentry.models.workflow import WorkflowDefinition
from agentry.runners.protocol import (
    AgentConfig,
    ExecutionResult,
    RunnerContext,
    RunnerProtocol,
    RunnerStatus,
)
from agentry.validation.pipeline import run_pipeline
from agentry.validation.result import ValidationResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SecurityEnvelopeError(Exception):
    """Base exception for security envelope failures."""


class ToolManifestViolationError(SecurityEnvelopeError):
    """Raised when available tools exceed the workflow's declared tool manifest.

    Attributes:
        excess_tools: Set of tool names that are not in the manifest.
        manifest_tools: Set of tool names declared in the workflow manifest.
    """

    def __init__(
        self,
        excess_tools: set[str],
        manifest_tools: set[str],
    ) -> None:
        self.excess_tools = excess_tools
        self.manifest_tools = manifest_tools
        excess_sorted = sorted(excess_tools)
        super().__init__(
            f"Tools not in workflow manifest: {', '.join(excess_sorted)}. "
            f"Allowed tools: {', '.join(sorted(manifest_tools))}."
        )


class PreflightError(SecurityEnvelopeError):
    """Raised when one or more preflight checks fail.

    Attributes:
        check_name: Name of the failed check.
        message: Human-readable failure description.
        remediation: Suggested fix, if available.
    """

    def __init__(
        self,
        check_name: str,
        message: str,
        remediation: str = "",
    ) -> None:
        self.check_name = check_name
        self.message = message
        self.remediation = remediation
        detail = f"Preflight check failed: {check_name}: {message}"
        if remediation:
            detail += f" Remediation: {remediation}"
        super().__init__(detail)


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class PreflightCheck(Protocol):
    """Protocol for preflight checks.

    Each check validates a single aspect of the execution environment
    (e.g. API key validity, Docker availability, filesystem permissions).
    """

    @property
    def name(self) -> str:
        """Human-readable name of this check."""
        ...

    def run(self) -> PreflightCheckResult:
        """Execute the check.

        Returns:
            A :class:`PreflightCheckResult` indicating pass or failure.
        """
        ...


@dataclass
class PreflightCheckResult:
    """Result of a single preflight check.

    Attributes:
        passed: True when the check succeeded.
        name: Name of the check.
        message: Description of the result (especially on failure).
        remediation: Suggested fix when the check fails.
    """

    passed: bool
    name: str
    message: str = ""
    remediation: str = ""


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class EnvelopeResult:
    """Result of a secured agent execution through the SecurityEnvelope.

    Attributes:
        execution_result: The runner's ExecutionResult, or None if execution
            failed before agent run.
        validation_result: Output of the three-layer validation pipeline,
            or None if execution failed before validation.
        tools_stripped: Tools that were removed from the available set.
        tools_allowed: Tools that were passed through to the runner.
        preflight_results: Results of all preflight checks.
        runner_metadata: Metadata from runner provisioning.
        envelope_error: Error message if the envelope itself failed.
        aborted: True if execution was aborted due to preflight or other failure.
    """

    execution_result: ExecutionResult | None = None
    validation_result: ValidationResult | None = None
    tools_stripped: list[str] = field(default_factory=list)
    tools_allowed: list[str] = field(default_factory=list)
    preflight_results: list[PreflightCheckResult] = field(default_factory=list)
    runner_metadata: dict[str, Any] = field(default_factory=dict)
    envelope_error: str = ""
    aborted: bool = False


# ---------------------------------------------------------------------------
# SecurityEnvelope
# ---------------------------------------------------------------------------


def strip_tools(
    available_tools: list[str],
    manifest_tools: list[str],
) -> tuple[list[str], list[str]]:
    """Filter available tools to only those declared in the workflow manifest.

    Args:
        available_tools: All tools available in the runtime environment.
        manifest_tools: Tools declared in the workflow's ``tools.capabilities``.

    Returns:
        A tuple of ``(allowed, stripped)`` where *allowed* contains only
        tools present in the manifest and *stripped* contains tools that
        were removed.
    """
    manifest_set = set(manifest_tools)
    allowed: list[str] = []
    stripped: list[str] = []

    for tool in available_tools:
        if tool in manifest_set:
            allowed.append(tool)
        else:
            stripped.append(tool)

    return allowed, stripped


class SecurityEnvelope:
    """Wraps a RunnerProtocol with security controls.

    The envelope manages the full execution lifecycle:

    1. **Tool stripping** -- removes tools not in the workflow manifest.
    2. **Runner provisioning** -- sets up the execution environment.
    3. **Preflight checks** -- validates environment readiness.
    4. **Agent execution** -- delegates to runner.execute(runner_context, agent_config).
    5. **Output validation** -- passes output through the three-layer pipeline.
    6. **Runner teardown** -- cleans up the execution environment (always runs).

    Args:
        workflow: The parsed workflow definition.
        runner: An object satisfying :class:`~agentry.runners.protocol.RunnerProtocol`.
        preflight_checks: Optional list of preflight checks to run before
            execution. Each must satisfy :class:`PreflightCheck`.
        abort_on_strip: If True (default), abort execution when tools are
            stripped. If False, silently strip and continue.
    """

    def __init__(
        self,
        workflow: WorkflowDefinition,
        runner: RunnerProtocol,
        preflight_checks: list[PreflightCheck] | None = None,
        abort_on_strip: bool = False,
    ) -> None:
        self._workflow = workflow
        self._runner = runner
        self._preflight_checks = preflight_checks or []
        self._abort_on_strip = abort_on_strip

    @property
    def workflow(self) -> WorkflowDefinition:
        """The workflow definition being executed."""
        return self._workflow

    @property
    def runner(self) -> RunnerProtocol:
        """The execution environment runner."""
        return self._runner

    def execute(
        self,
        system_prompt: str,
        resolved_inputs: dict[str, str],
        available_tools: list[str],
        agent_name: str = "claude-code",
        agent_config: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> EnvelopeResult:
        """Execute the agent within the security envelope.

        This is the main entry point. It enforces the full lifecycle:
        tool stripping, provisioning, preflight, execution (via runner),
        validation, and teardown.

        Args:
            system_prompt: The system prompt text.
            resolved_inputs: Mapping from input name to resolved content.
            available_tools: All tools available in the runtime environment.
            agent_name: Identifier of the agent runtime to use.
            agent_config: Runtime-specific configuration forwarded to the agent.
            timeout: Overall execution timeout in seconds.

        Returns:
            An :class:`EnvelopeResult` with execution details.

        Raises:
            ToolManifestViolationError: When ``abort_on_strip`` is True and
                tools exceed the manifest.
            PreflightError: When a preflight check fails.
        """
        result = EnvelopeResult()
        runner_context: RunnerContext | None = None

        try:
            # Phase 1: Tool manifest enforcement.
            manifest_tools = self._workflow.tools.capabilities
            allowed, stripped = strip_tools(available_tools, manifest_tools)
            result.tools_allowed = allowed
            result.tools_stripped = stripped

            if stripped:
                logger.warning(
                    "Stripped tools not in manifest: %s",
                    ", ".join(sorted(stripped)),
                )
                if self._abort_on_strip:
                    raise ToolManifestViolationError(
                        excess_tools=set(stripped),
                        manifest_tools=set(manifest_tools),
                    )

            # Phase 2: Runner provisioning.
            runner_context = self._runner.provision(
                self._workflow.safety,
                resolved_inputs,
            )
            result.runner_metadata = runner_context.metadata

            # Phase 3: Preflight checks.
            for check in self._preflight_checks:
                check_result = check.run()
                result.preflight_results.append(check_result)

                if not check_result.passed:
                    raise PreflightError(
                        check_name=check_result.name,
                        message=check_result.message,
                        remediation=check_result.remediation,
                    )

            # Phase 4: Agent execution via runner (with only allowed tools).
            run_agent_config = AgentConfig(
                system_prompt=system_prompt,
                resolved_inputs=resolved_inputs,
                tool_names=allowed,
                agent_name=agent_name,
                agent_config=agent_config or {},
                timeout=timeout,
            )
            execution_result = self._runner.execute(runner_context, run_agent_config)
            result.execution_result = execution_result

            # Phase 5: Output validation pipeline.
            if execution_result.output is not None and not execution_result.error:
                validation = self._run_validation(execution_result)
                result.validation_result = validation

        except ToolManifestViolationError:
            result.aborted = True
            result.envelope_error = "Aborted: tools exceed workflow manifest."
            raise
        except PreflightError:
            result.aborted = True
            result.envelope_error = "Aborted: preflight check failed."
            raise
        except SecurityEnvelopeError as exc:
            result.aborted = True
            result.envelope_error = str(exc)
        except Exception as exc:
            result.envelope_error = f"Unexpected error: {exc}"
        finally:
            # Phase 6: Runner teardown (always runs if provisioned).
            if runner_context is not None:
                try:
                    self._runner.teardown(runner_context)
                except Exception as teardown_exc:
                    logger.error("Runner teardown failed: %s", teardown_exc)
                    if not result.envelope_error:
                        result.envelope_error = (
                            f"Runner teardown failed: {teardown_exc}"
                        )

        return result

    def _run_validation(self, execution_result: ExecutionResult) -> ValidationResult:
        """Run the three-layer validation pipeline on agent output.

        Args:
            execution_result: The completed runner execution result.

        Returns:
            A :class:`~agentry.validation.result.ValidationResult`.
        """
        # Build tool invocations in the format expected by the pipeline.
        tool_invocations = [
            {"tool": inv.get("tool", ""), "input": inv.get("input", {})}
            for inv in execution_result.tool_invocations
        ]

        # Build file writes from tool invocations that look like writes.
        file_writes: list[dict[str, Any]] = []
        for inv in execution_result.tool_invocations:
            tool_name = inv.get("tool", "")
            tool_input = inv.get("input", {})
            if "write" in tool_name.lower() and "path" in tool_input:
                file_writes.append({"path": tool_input["path"]})

        # Get schema, side-effect allowlist, and output paths from workflow.
        schema = self._workflow.output.schema_def
        side_effects_allowlist = [
            se.type for se in self._workflow.output.side_effects
        ]
        output_paths = self._workflow.output.output_paths

        return run_pipeline(
            output=execution_result.output,
            schema=schema,
            tool_invocations=tool_invocations,
            side_effects_allowlist=side_effects_allowlist,
            file_writes=file_writes,
            output_paths=output_paths,
        )
