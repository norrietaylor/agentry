"""RunnerProtocol and data models for pluggable execution environments.

Defines the interface that all runner backend implementations must satisfy.
Runners are interchangeable: swapping DockerRunner for InProcessRunner (or
a future GitHubActionsRunner) requires no changes to the SecurityEnvelope or
any other orchestration layer.

The protocol mirrors the relationship between local runners and CI runners
(e.g., GitHub Actions): DockerRunner is to local execution what
GitHubActionsRunner (Phase 3) will be to CI.

Usage::

    from agentry.runners.protocol import RunnerProtocol, RunnerContext, RunnerStatus

    class MyRunner:
        def provision(
            self, safety_block: SafetyBlock, resolved_inputs: dict[str, str]
        ) -> RunnerContext:
            ...

        def execute(
            self, runner_context: RunnerContext, agent_config: AgentConfig
        ) -> ExecutionResult:
            ...

        def teardown(self, runner_context: RunnerContext) -> None:
            ...

        def check_available(self) -> RunnerStatus:
            ...

    assert isinstance(MyRunner(), RunnerProtocol)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agentry.executor import ExecutionRecord
from agentry.models.safety import SafetyBlock

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class RunnerContext:
    """State produced by runner provisioning.

    Holds the provisioned execution environment's identifiers and configuration
    so that ``execute()`` and ``teardown()`` can reference the correct
    resources.

    Attributes:
        container_id: Docker container ID (or empty string for in-process
            runners that do not provision a container).
        network_id: Docker network ID attached to the container (or empty
            string when no isolated network is created).
        mount_mappings: Host-to-container path mapping for filesystem mounts.
            Keys are host paths, values are container paths (e.g.
            ``{"/home/user/repo": "/workspace"}``).
        metadata: Arbitrary runner-specific metadata (e.g. resource limits
            applied, image digest, execution ID). Stored for diagnostics and
            the setup manifest.
    """

    container_id: str = ""
    network_id: str = ""
    mount_mappings: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunnerStatus:
    """Result of a runner availability check.

    Attributes:
        available: True when the runner backend is operational and can accept
            provision requests.
        message: Human-readable description. On failure, this explains why the
            runner is unavailable and may suggest remediation.
    """

    available: bool
    message: str = ""


@dataclass
class AgentConfig:
    """Configuration passed to ``RunnerProtocol.execute()``.

    Bundles together everything the runner needs to execute the agent:
    the system prompt, resolved inputs, tool names, the agent runtime name,
    runtime-specific configuration, and timeout.

    ``llm_config`` has been replaced by ``agent_name`` and ``agent_config``
    so that the runner can delegate to any ``AgentProtocol`` implementation
    without knowing about LLM provider details.

    Attributes:
        system_prompt: The system prompt text (already loaded from disk).
        resolved_inputs: Mapping from input name to resolved content string.
        tool_names: Tool identifiers to expose to the agent.
        agent_name: Identifier of the agent runtime to use (e.g.
            ``"claude-code"``).  Resolved by ``AgentRegistry`` inside
            ``RunnerDetector``.
        agent_config: Runtime-specific configuration dict forwarded to the
            agent factory as keyword arguments (e.g. ``{"model": "claude-…"}``).
        timeout: Overall execution timeout in seconds. ``None`` means no limit.
    """

    system_prompt: str
    resolved_inputs: dict[str, str]
    tool_names: list[str]
    agent_name: str = "claude-code"
    agent_config: dict[str, Any] = field(default_factory=dict)
    timeout: float | None = None


@dataclass
class ExecutionResult:
    """Result of a runner execution.

    Populated from the ``AgentResult`` returned by the agent runtime, plus
    container-level metadata (exit code, captured stderr/stdout when available,
    and runner-specific context).

    When the runner delegates to an ``AgentProtocol`` instance the following
    fields are mapped directly from the corresponding ``AgentResult`` fields:
    ``output``, ``token_usage``, ``tool_invocations``, ``timed_out``, and
    ``error``.

    Attributes:
        execution_record: The full agent execution record (legacy; may be
            ``None`` when the runner delegates exclusively to an
            ``AgentProtocol``).
        exit_code: Container/process exit code. 0 indicates success.
        stdout: Raw standard output captured from the agent, if available.
        stderr: Raw standard error captured from the agent, if available.
        runner_metadata: Runner-specific metadata from the execution context
            (e.g. container resource usage, network stats).
        timed_out: True when the execution was terminated by the runner's
            hard timeout (SIGKILL for Docker, threading for in-process).
        error: Error message if execution failed.
        output: Structured output parsed from the agent response (mirrors
            ``AgentResult.output``).
        token_usage: Token counts from the agent run (mirrors
            ``AgentResult.token_usage``).
        tool_invocations: List of tool invocation records (mirrors
            ``AgentResult.tool_invocations``).
    """

    execution_record: ExecutionRecord | None = None
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    runner_metadata: dict[str, Any] = field(default_factory=dict)
    timed_out: bool = False
    error: str = ""
    output: dict[str, Any] | None = None
    token_usage: dict[str, int] = field(default_factory=dict)
    tool_invocations: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class RunnerProtocol(Protocol):
    """Protocol for pluggable execution environment runners.

    Runners manage the full lifecycle of an execution environment: provisioning
    (container creation, network setup, mount binding), agent execution, and
    teardown (container removal, network cleanup).

    The SecurityEnvelope calls these methods in order:
    1. ``check_available()`` -- verify the backend is operational.
    2. ``provision(safety_block, resolved_inputs)`` -- set up the environment.
    3. ``execute(runner_context, agent_config)`` -- run the agent.
    4. ``teardown(runner_context)`` -- release all resources (always called).

    All conforming implementations must be usable as drop-in replacements.
    Implementations should satisfy the runtime ``isinstance`` check that
    ``@runtime_checkable`` enables.
    """

    def provision(
        self,
        safety_block: SafetyBlock,
        resolved_inputs: dict[str, str],
    ) -> RunnerContext:
        """Provision the execution environment.

        Sets up all resources needed for agent execution: creates the container
        (or in-process context), attaches the network, applies filesystem
        mounts and resource limits.

        Args:
            safety_block: Workflow safety configuration specifying trust level,
                resource limits, filesystem patterns, network allowlist, and
                sandbox image.
            resolved_inputs: Mapping from input name to resolved content. Used
                to determine which filesystem paths need to be mounted.

        Returns:
            A :class:`RunnerContext` containing the environment's state
            (container ID, network ID, mount mappings, and metadata).

        Raises:
            RuntimeError: If provisioning fails (e.g. Docker daemon not
                reachable, image pull failure, insufficient resources).
        """
        ...

    def execute(
        self,
        runner_context: RunnerContext,
        agent_config: AgentConfig,
    ) -> ExecutionResult:
        """Execute the agent inside the provisioned environment.

        For ``DockerRunner``, this runs the Agentry runtime shim inside the
        container. For ``InProcessRunner``, this delegates to ``AgentExecutor``
        in the current process.

        Args:
            runner_context: The provisioned environment context returned by
                :meth:`provision`.
            agent_config: Bundled agent execution parameters (system prompt,
                inputs, tool names, LLM config, retry config, timeout).

        Returns:
            An :class:`ExecutionResult` wrapping the execution record and any
            container-level output.

        Raises:
            RuntimeError: If the runner cannot execute (e.g. container was
                removed before execute was called).
        """
        ...

    def teardown(self, runner_context: RunnerContext) -> None:
        """Tear down the execution environment and release all resources.

        Always called in a ``finally`` block by the SecurityEnvelope, even
        when execution fails. Teardown should be idempotent: calling it on an
        already-torn-down context should not raise.

        For ``DockerRunner``, removes the container and any associated volumes.
        For ``InProcessRunner``, this is a no-op.

        Args:
            runner_context: The provisioned environment context returned by
                :meth:`provision`.
        """
        ...

    def check_available(self) -> RunnerStatus:
        """Check whether this runner backend is available and operational.

        Called by ``RunnerDetector`` before provisioning to determine whether
        the selected backend can be used. For ``DockerRunner``, this probes
        the Docker daemon. For ``InProcessRunner``, this always returns
        available.

        Returns:
            A :class:`RunnerStatus` with ``available=True`` if the backend is
            ready, or ``available=False`` with a diagnostic message.
        """
        ...
