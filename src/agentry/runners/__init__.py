"""Runner backends for Agentry workflow execution.

This package provides pluggable execution environment abstractions and their
implementations. The RunnerProtocol defines the interface; DockerRunner and
InProcessRunner are the two built-in backends.

Public API::

    from agentry.runners import (
        RunnerProtocol,
        RunnerContext,
        RunnerStatus,
        AgentConfig,
        ExecutionResult,
    )
"""

from agentry.runners.protocol import (
    AgentConfig,
    ExecutionResult,
    RunnerContext,
    RunnerProtocol,
    RunnerStatus,
)

__all__ = [
    "AgentConfig",
    "ExecutionResult",
    "RunnerContext",
    "RunnerProtocol",
    "RunnerStatus",
]
