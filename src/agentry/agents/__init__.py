"""Agent abstraction layer.

Exports the core types and the default registry so importers can access
everything from ``agentry.agents``.

Example::

    from agentry.agents import AgentProtocol, AgentTask, AgentResult
    from agentry.agents import ClaudeCodeAgent, AgentRegistry
"""

from agentry.agents.claude_code import ClaudeCodeAgent
from agentry.agents.models import AgentResult, AgentTask, TokenUsage
from agentry.agents.protocol import AgentProtocol
from agentry.agents.registry import AgentFactory, AgentRegistry

__all__ = [
    "AgentFactory",
    "AgentProtocol",
    "AgentRegistry",
    "AgentResult",
    "AgentTask",
    "ClaudeCodeAgent",
    "TokenUsage",
]
