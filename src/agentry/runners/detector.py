"""RunnerDetector for selecting appropriate runner based on trust level.

Determines which runner backend to use based on the workflow's trust level and
environment availability. For trust: elevated, always returns InProcessRunner.
For trust: sandboxed, checks Docker availability and returns DockerRunner if
available, or raises an error if Docker is unavailable.

Usage::

    from agentry.runners.detector import RunnerDetector
    from agentry.models.safety import SafetyBlock

    detector = RunnerDetector(llm_client=client, docker_client=None)
    safety = SafetyBlock()
    runner = detector.get_runner(safety)
"""

from __future__ import annotations

from typing import Any

from agentry.models.safety import SafetyBlock, TrustLevel
from agentry.runners.docker_runner import DockerRunner
from agentry.runners.in_process import InProcessRunner
from agentry.runners.protocol import RunnerProtocol


class RunnerDetector:
    """Detects and selects the appropriate runner based on trust level.

    Implements the decision logic for choosing between InProcessRunner
    (trust: elevated) and DockerRunner (trust: sandboxed). For sandboxed
    mode, verifies Docker availability before selection.

    Attributes:
        llm_client: The LLM client for agent execution. Passed to
            InProcessRunner.
        docker_client: Optional pre-configured Docker client (for testing).
            If None, DockerRunner creates its own.
    """

    def __init__(
        self, llm_client: Any, docker_client: Any = None
    ) -> None:
        """Initialize the RunnerDetector.

        Args:
            llm_client: The LLM client for agent execution.
            docker_client: Optional Docker client instance (for testing).
                If None, DockerRunner will use the system Docker daemon.
        """
        self.llm_client = llm_client
        self.docker_client = docker_client

    def get_runner(self, safety_block: SafetyBlock) -> RunnerProtocol:
        """Select and return the appropriate runner for the safety block.

        Selection logic:
        - If ``trust: elevated``, returns InProcessRunner (no isolation).
        - If ``trust: sandboxed``, checks Docker availability and returns
          DockerRunner if available. Raises RuntimeError if Docker is
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
        """
        if safety_block.trust == TrustLevel.elevated:
            # Elevated trust mode: use in-process execution with no isolation.
            return InProcessRunner(llm_client=self.llm_client)

        # Sandboxed mode: check Docker availability.
        docker_runner = DockerRunner(docker_client=self.docker_client)
        status = docker_runner.check_available()

        if not status.available:
            raise RuntimeError(
                "Docker is required for sandboxed execution. "
                "Install Docker or set trust: elevated."
            )

        return docker_runner
