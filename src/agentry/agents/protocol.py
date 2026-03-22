"""AgentProtocol: PEP-544 runtime-checkable interface for agent runtimes.

Defines the structural interface that all agent runtime implementations must
satisfy. Using ``@runtime_checkable`` allows callers to use ``isinstance``
checks at runtime without requiring explicit base class inheritance.

Usage::

    from agentry.agents.protocol import AgentProtocol
    from agentry.agents.models import AgentTask, AgentResult

    class MyAgent:
        def execute(self, agent_task: AgentTask) -> AgentResult:
            ...

        @staticmethod
        def check_available() -> bool:
            ...

    assert isinstance(MyAgent(), AgentProtocol)
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentry.agents.models import AgentResult, AgentTask


@runtime_checkable
class AgentProtocol(Protocol):
    """Protocol for pluggable coding agent runtimes.

    An agent runtime accepts a fully-assembled :class:`~agentry.agents.models.AgentTask`
    and returns a :class:`~agentry.agents.models.AgentResult`.  The runtime is
    responsible for all interaction with the underlying model (including
    subprocess management, API calls, or any other mechanism).

    All conforming implementations must be usable as drop-in replacements.
    Implementations satisfy the runtime ``isinstance`` check enabled by
    ``@runtime_checkable``.

    Class method ``check_available`` should be callable on the class itself
    (not requiring an instance) so that the registry can verify prerequisites
    before constructing an instance.
    """

    def execute(self, agent_task: AgentTask) -> AgentResult:
        """Execute an agent task and return the result.

        Args:
            agent_task: The fully-assembled task bundle, including system
                prompt, task description, tool names, and optional schema.

        Returns:
            An :class:`~agentry.agents.models.AgentResult` with output,
            token usage, and execution metadata.

        Raises:
            RuntimeError: If the agent runtime encounters an unrecoverable
                error (distinct from the agent returning an error result).
        """
        ...

    @staticmethod
    def check_available() -> bool:
        """Check whether this agent runtime is available and operational.

        Called by the runner and registry before constructing an instance to
        verify prerequisites (e.g. the ``claude`` binary is on PATH).

        Returns:
            ``True`` when the runtime is ready to accept tasks.
        """
        ...
