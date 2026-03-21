"""InProcessRunner implementation for trust: elevated mode.

Provides a RunnerProtocol backend that executes agents in the current process
with no isolation. This is used when trust: elevated is specified in the workflow.

Delegates actual agent execution to the AgentExecutor, which handles LLM calls,
tool invocations, retry logic, and timeout enforcement.

Usage::

    from agentry.runners.in_process import InProcessRunner
    from agentry.models.safety import SafetyBlock
    from agentry.runners import AgentConfig

    runner = InProcessRunner(llm_client=client)
    status = runner.check_available()
    assert status.available

    ctx = runner.provision(safety_block=SafetyBlock(), resolved_inputs={})
    result = runner.execute(runner_context=ctx, agent_config=config)
    runner.teardown(ctx)
"""

from __future__ import annotations

import logging
from typing import Any

from agentry.executor import AgentExecutor, ExecutionRecord
from agentry.models.safety import SafetyBlock
from agentry.runners.protocol import (
    AgentConfig,
    ExecutionResult,
    RunnerContext,
    RunnerProtocol,
    RunnerStatus,
)

logger = logging.getLogger(__name__)


class InProcessRunner:
    """Runner backend for trust: elevated mode (no isolation).

    Executes agents in-process by delegating to AgentExecutor. This runner
    satisfies RunnerProtocol and can be used as a drop-in replacement for
    DockerRunner when isolation is not required.

    Attributes:
        llm_client: The LLM client (e.g. Claude API client) used for agent
            execution. Passed to AgentExecutor on each execute() call.
    """

    def __init__(self, llm_client: Any) -> None:
        """Initialize the InProcessRunner.

        Args:
            llm_client: The LLM client for agent execution.
        """
        self.llm_client = llm_client

    def provision(
        self,
        safety_block: SafetyBlock,
        resolved_inputs: dict[str, str],
    ) -> RunnerContext:
        """Provision the in-process execution environment.

        For in-process runners, provisioning is a no-op: no container is
        created, no network is isolated. The execution will happen in the
        current process. Logs a warning about elevated trust mode.

        Args:
            safety_block: Workflow safety configuration (unused, since no
                isolation is performed).
            resolved_inputs: Mapping from input name to resolved content
                (unused for provisioning).

        Returns:
            A RunnerContext with runner_type="in_process" and all other
            fields as empty defaults.
        """
        logger.warning(
            "Running in elevated trust mode -- no runner isolation."
        )
        return RunnerContext(
            container_id="",
            network_id="",
            mount_mappings={},
            metadata={"runner_type": "in_process"},
        )

    def execute(
        self,
        runner_context: RunnerContext,
        agent_config: AgentConfig,
    ) -> ExecutionResult:
        """Execute the agent in the current process.

        Delegates to AgentExecutor, which handles LLM communication, tool
        invocations, retry logic, and timeout enforcement.

        Args:
            runner_context: The provisioned environment context (unused for
                in-process execution).
            agent_config: Bundled agent execution parameters (system prompt,
                inputs, tool names, LLM config, retry config, timeout).

        Returns:
            An ExecutionResult wrapping the execution record from AgentExecutor.

        Raises:
            RuntimeError: If the LLM client is not available or if agent
                execution fails.
        """
        executor = AgentExecutor(llm_client=self.llm_client)
        record = executor.run(
            system_prompt=agent_config.system_prompt,
            resolved_inputs=agent_config.resolved_inputs,
            tool_names=agent_config.tool_names,
            config=agent_config.llm_config,
            retry_config=agent_config.retry_config,
            timeout=agent_config.timeout,
        )
        return ExecutionResult(
            execution_record=record,
            exit_code=0 if not record.error else 1,
            stdout=record.final_content,
            stderr=record.error,
            runner_metadata={"runner_type": "in_process"},
            timed_out=record.timed_out,
        )

    def teardown(self, runner_context: RunnerContext) -> None:
        """Tear down the in-process execution environment.

        For in-process runners, teardown is a no-op: no resources were
        allocated during provisioning, so none need to be released.

        Args:
            runner_context: The provisioned environment context (unused).
        """
        # No-op: no resources to clean up.
        pass

    def check_available(self) -> RunnerStatus:
        """Check whether the in-process runner is available.

        In-process runners are always available: they run in the current
        process and require no external dependencies.

        Returns:
            A RunnerStatus with available=True.
        """
        return RunnerStatus(
            available=True,
            message="In-process runner is always available.",
        )
