"""InProcessRunner implementation for trust: elevated mode.

Provides a RunnerProtocol backend that executes agents in the current process
with no isolation. This is used when trust: elevated is specified in the workflow.

Delegates actual agent execution to an AgentProtocol instance, which handles
all communication with the underlying model (subprocess management, API calls,
etc.).

Usage::

    from agentry.runners.in_process import InProcessRunner
    from agentry.agents.claude_code import ClaudeCodeAgent
    from agentry.models.safety import SafetyBlock
    from agentry.runners import AgentConfig

    agent = ClaudeCodeAgent()
    runner = InProcessRunner(agent=agent)
    status = runner.check_available()
    assert status.available

    ctx = runner.provision(safety_block=SafetyBlock(), resolved_inputs={})
    result = runner.execute(runner_context=ctx, agent_config=config)
    runner.teardown(ctx)
"""

from __future__ import annotations

import logging

from agentry.agents.models import AgentTask
from agentry.agents.protocol import AgentProtocol
from agentry.models.safety import SafetyBlock
from agentry.runners.protocol import (
    AgentConfig,
    ExecutionResult,
    RunnerContext,
    RunnerStatus,
)

logger = logging.getLogger(__name__)


class InProcessRunner:
    """Runner backend for trust: elevated mode (no isolation).

    Executes agents in-process by delegating to an AgentProtocol instance.
    This runner satisfies RunnerProtocol and can be used as a drop-in
    replacement for DockerRunner when isolation is not required.

    Attributes:
        agent: The AgentProtocol implementation used for agent execution.
    """

    def __init__(self, agent: AgentProtocol) -> None:
        """Initialize the InProcessRunner.

        Args:
            agent: An AgentProtocol implementation (e.g. ClaudeCodeAgent)
                that will handle agent execution.
        """
        self.agent = agent

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

        Builds an AgentTask from the AgentConfig and delegates to the
        AgentProtocol instance. Maps the AgentResult fields onto an
        ExecutionResult.

        Args:
            runner_context: The provisioned environment context (unused for
                in-process execution).
            agent_config: Bundled agent execution parameters (system prompt,
                inputs, tool names, agent name/config, timeout).

        Returns:
            An ExecutionResult populated from the AgentResult returned by
            the agent runtime.
        """
        # Build the task description by joining resolved inputs.
        task_description = "\n\n".join(
            f"{k}:\n{v}" for k, v in agent_config.resolved_inputs.items()
        )

        agent_task = AgentTask(
            system_prompt=agent_config.system_prompt,
            task_description=task_description,
            tool_names=agent_config.tool_names,
            timeout=agent_config.timeout,
            output_schema=agent_config.output_schema,
        )

        agent_result = self.agent.execute(agent_task)

        exit_code = agent_result.exit_code
        # Normalise: treat non-zero exit or error as failure.
        if agent_result.error and exit_code == 0:
            exit_code = 1

        token_usage_dict: dict[str, int] = {
            "input_tokens": agent_result.token_usage.input_tokens,
            "output_tokens": agent_result.token_usage.output_tokens,
        }

        return ExecutionResult(
            exit_code=exit_code,
            stdout=agent_result.raw_output,
            stderr=agent_result.error,
            runner_metadata={"runner_type": "in_process"},
            timed_out=agent_result.timed_out,
            error=agent_result.error,
            output=agent_result.output,
            token_usage=token_usage_dict,
            tool_invocations=list(agent_result.tool_invocations),
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
