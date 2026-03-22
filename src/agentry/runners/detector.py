"""RunnerDetector for selecting appropriate runner based on trust level.

Determines which runner backend to use based on the workflow's trust level and
environment availability. For trust: elevated, always returns InProcessRunner.
For trust: sandboxed, checks Docker availability and returns DockerRunner if
available, or raises an error if Docker is unavailable.

The agent runtime is resolved by name from an ``AgentRegistry`` and injected
into ``InProcessRunner`` at selection time.

Usage::

    from agentry.runners.detector import RunnerDetector
    from agentry.agents.registry import AgentRegistry
    from agentry.models.safety import SafetyBlock

    registry = AgentRegistry.default()
    detector = RunnerDetector(agent_registry=registry, agent_name="claude-code")
    safety = SafetyBlock()
    runner = detector.get_runner(safety)
"""

from __future__ import annotations

from typing import Any

from agentry.agents.registry import AgentRegistry
from agentry.models.safety import SafetyBlock, TrustLevel
from agentry.runners.docker_runner import DockerRunner
from agentry.runners.in_process import InProcessRunner
from agentry.runners.protocol import RunnerProtocol


class RunnerDetector:
    """Detects and selects the appropriate runner based on trust level.

    Implements the decision logic for choosing between InProcessRunner
    (trust: elevated) and DockerRunner (trust: sandboxed). For sandboxed
    mode, verifies Docker availability before selection.

    The agent runtime is resolved from ``agent_registry`` using ``agent_name``
    and injected into ``InProcessRunner`` so runners no longer depend on a
    raw ``llm_client``.

    Attributes:
        agent_registry: Registry used to resolve agent runtimes by name.
        agent_name: The identifier of the agent runtime to use (e.g.
            ``"claude-code"``).  Must be registered in ``agent_registry``.
        agent_kwargs: Keyword arguments forwarded to the agent factory when
            constructing the agent instance.
        docker_client: Optional pre-configured Docker client (for testing).
            If None, DockerRunner creates its own.
    """

    def __init__(
        self,
        agent_registry: AgentRegistry,
        agent_name: str = "claude-code",
        agent_kwargs: dict[str, Any] | None = None,
        docker_client: Any = None,
    ) -> None:
        """Initialize the RunnerDetector.

        Args:
            agent_registry: Registry mapping runtime names to agent factories.
            agent_name: The agent runtime identifier to resolve (e.g.
                ``"claude-code"``).
            agent_kwargs: Optional keyword arguments forwarded to the agent
                factory (e.g. ``{"model": "claude-opus-4-5"}``).
            docker_client: Optional Docker client instance (for testing).
                If None, DockerRunner will use the system Docker daemon.
        """
        self.agent_registry = agent_registry
        self.agent_name = agent_name
        self.agent_kwargs: dict[str, Any] = agent_kwargs or {}
        self.docker_client = docker_client

    def get_runner(self, safety_block: SafetyBlock) -> RunnerProtocol:
        """Select and return the appropriate runner for the safety block.

        Selection logic:
        - If ``trust: elevated``, resolves the agent from the registry and
          returns ``InProcessRunner`` (no isolation).
        - If ``trust: sandboxed``, checks Docker availability and returns
          ``DockerRunner`` if available. Raises ``RuntimeError`` if Docker is
          unavailable with a diagnostic message.

        Args:
            safety_block: Workflow safety configuration specifying trust
                level and other constraints.

        Returns:
            A RunnerProtocol implementation (InProcessRunner or DockerRunner).

        Raises:
            RuntimeError: If ``trust: sandboxed`` but Docker is unavailable.
                The error message provides guidance on how to resolve this
                (install Docker or set ``trust: elevated``).
            KeyError: If ``agent_name`` is not registered in
                ``agent_registry``.
        """
        if safety_block.trust == TrustLevel.elevated:
            # Elevated trust mode: resolve the agent and run in-process.
            agent = self.agent_registry.get(self.agent_name, **self.agent_kwargs)
            return InProcessRunner(agent=agent)

        # Sandboxed mode: check Docker availability.
        docker_runner = DockerRunner(docker_client=self.docker_client)
        status = docker_runner.check_available()

        if not status.available:
            raise RuntimeError(
                "Docker is required for sandboxed execution. "
                "Install Docker or set trust: elevated."
            )

        return docker_runner
