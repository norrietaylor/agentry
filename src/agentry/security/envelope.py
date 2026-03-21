"""Security envelope wrapping AgentExecutor.

Provides the SecurityEnvelope class that wraps an AgentExecutor to enforce
security controls during workflow execution. The envelope manages the full
lifecycle: tool manifest enforcement, runner provisioning, preflight checks,
agent execution, output validation, and runner teardown.

Usage::

    from agentry.security.envelope import SecurityEnvelope, RunnerProtocol

    envelope = SecurityEnvelope(
        workflow=workflow_definition,
        runner=runner_instance,
        executor=agent_executor,
    )
    result = envelope.execute(
        system_prompt="You are a code reviewer.",
        resolved_inputs={"diff": "..."},
        available_tools=["read_file", "write_file", "shell_exec"],
        config=llm_config,
    )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agentry.executor import AgentExecutor, ExecutionRecord
from agentry.llm.models import LLMConfig
from agentry.models.model import RetryConfig
from agentry.models.workflow import WorkflowDefinition
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
class RunnerProtocol(Protocol):
    """Protocol for execution environment runners.

    Runners manage the lifecycle of an execution environment (e.g. a Docker
    container or an in-process sandbox). The SecurityEnvelope calls these
    methods in order: ``provision()`` -> execute agent -> ``teardown()``.
    """

    def provision(self) -> dict[str, Any]:
        """Provision the execution environment.

        Returns:
            A dict of environment metadata (container ID, network config, etc.).
        """
        ...

    def teardown(self) -> None:
        """Tear down the execution environment and release resources."""
        ...

    def execute(
        self, command: str, timeout: float | None = None
    ) -> dict[str, Any]:
        """Execute a command in the runner environment.

        Args:
            command: The command to execute.
            timeout: Optional timeout in seconds.

        Returns:
            A dict with ``exit_code``, ``stdout``, and ``stderr`` keys.
        """
        ...

    def check_available(self) -> bool:
        """Check whether the runner backend is available.

        Returns:
            True if the runner can be used, False otherwise.
        """
        ...


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
        execution_record: The underlying AgentExecutor execution record.
        validation_result: Output of the three-layer validation pipeline,
            or None if execution failed before validation.
        tools_stripped: Tools that were removed from the available set.
        tools_allowed: Tools that were passed through to the executor.
        preflight_results: Results of all preflight checks.
        runner_metadata: Metadata from runner provisioning.
        envelope_error: Error message if the envelope itself failed.
        aborted: True if execution was aborted due to preflight or other failure.
    """

    execution_record: ExecutionRecord | None = None
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
    """Wraps AgentExecutor with security controls.

    The envelope manages the full execution lifecycle:

    1. **Tool stripping** -- removes tools not in the workflow manifest.
    2. **Runner provisioning** -- sets up the execution environment.
    3. **Preflight checks** -- validates environment readiness.
    4. **Agent execution** -- runs the agent with only allowed tools.
    5. **Output validation** -- passes output through the three-layer pipeline.
    6. **Runner teardown** -- cleans up the execution environment (always runs).

    Args:
        workflow: The parsed workflow definition.
        runner: An object satisfying :class:`RunnerProtocol`.
        executor: The :class:`~agentry.executor.AgentExecutor` to wrap.
        preflight_checks: Optional list of preflight checks to run before
            execution. Each must satisfy :class:`PreflightCheck`.
        abort_on_strip: If True (default), abort execution when tools are
            stripped. If False, silently strip and continue.
    """

    def __init__(
        self,
        workflow: WorkflowDefinition,
        runner: RunnerProtocol,
        executor: AgentExecutor,
        preflight_checks: list[PreflightCheck] | None = None,
        abort_on_strip: bool = False,
    ) -> None:
        self._workflow = workflow
        self._runner = runner
        self._executor = executor
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
        config: LLMConfig,
        retry_config: RetryConfig | None = None,
        timeout: float | None = None,
    ) -> EnvelopeResult:
        """Execute the agent within the security envelope.

        This is the main entry point. It enforces the full lifecycle:
        tool stripping, provisioning, preflight, execution, validation,
        and teardown.

        Args:
            system_prompt: The system prompt text.
            resolved_inputs: Mapping from input name to resolved content.
            available_tools: All tools available in the runtime environment.
            config: LLM call configuration.
            retry_config: Retry configuration for the executor.
            timeout: Overall execution timeout in seconds.

        Returns:
            An :class:`EnvelopeResult` with execution details.

        Raises:
            ToolManifestViolationError: When ``abort_on_strip`` is True and
                tools exceed the manifest.
            PreflightError: When a preflight check fails.
        """
        result = EnvelopeResult()
        provisioned = False

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
            runner_meta = self._runner.provision()
            provisioned = True
            result.runner_metadata = runner_meta

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

            # Phase 4: Agent execution (with only allowed tools).
            record = self._executor.run(
                system_prompt=system_prompt,
                resolved_inputs=resolved_inputs,
                tool_names=allowed,
                config=config,
                retry_config=retry_config,
                timeout=timeout,
            )
            result.execution_record = record

            # Phase 5: Output validation pipeline.
            if record.final_output is not None and not record.error:
                validation = self._run_validation(record)
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
            if provisioned:
                try:
                    self._runner.teardown()
                except Exception as teardown_exc:
                    logger.error("Runner teardown failed: %s", teardown_exc)
                    if not result.envelope_error:
                        result.envelope_error = (
                            f"Runner teardown failed: {teardown_exc}"
                        )

        return result

    def _run_validation(self, record: ExecutionRecord) -> ValidationResult:
        """Run the three-layer validation pipeline on agent output.

        Args:
            record: The completed execution record.

        Returns:
            A :class:`~agentry.validation.result.ValidationResult`.
        """
        # Build tool invocations in the format expected by the pipeline.
        tool_invocations = [
            {"tool": inv.tool_name, "input": inv.tool_input}
            for inv in record.tool_invocations
        ]

        # Build file writes from tool invocations that look like writes.
        file_writes: list[dict[str, Any]] = []
        for inv in record.tool_invocations:
            if "write" in inv.tool_name.lower() and "path" in inv.tool_input:
                file_writes.append({"path": inv.tool_input["path"]})

        # Get schema, side-effect allowlist, and output paths from workflow.
        schema = self._workflow.output.schema_def
        side_effects_allowlist = [
            se.type for se in self._workflow.output.side_effects
        ]
        output_paths = self._workflow.output.output_paths

        return run_pipeline(
            output=record.final_output,
            schema=schema,
            tool_invocations=tool_invocations,
            side_effects_allowlist=side_effects_allowlist,
            file_writes=file_writes,
            output_paths=output_paths,
        )
