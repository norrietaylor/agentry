"""Runner backends for Agentry workflow execution.

This package provides pluggable execution environment abstractions and their
implementations. The RunnerProtocol defines the interface; DockerRunner and
InProcessRunner are the two built-in backends. RunnerDetector selects the
appropriate runner based on trust level and environment availability.

Public API::

    from agentry.runners import (
        RunnerProtocol,
        RunnerContext,
        RunnerStatus,
        AgentConfig,
        ExecutionResult,
        RunnerDetector,
    )
"""

from agentry.runners.detector import RunnerDetector
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
    "RunnerDetector",
]
