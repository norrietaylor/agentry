"""AgentRegistry: maps agent runtime names to factory functions.

Provides a central lookup so runners and the CLI can resolve an agent
runtime by its string identifier (e.g. ``"claude-code"``) without hard-coded
``if/elif`` chains.

Usage::

    from agentry.agents.registry import AgentRegistry

    registry = AgentRegistry.default()
    factory = registry.get("claude-code")
    agent = factory(model="claude-opus-4-5")
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agentry.agents.claude_code import ClaudeCodeAgent
from agentry.agents.protocol import AgentProtocol

# A factory is a callable that accepts keyword arguments and returns an agent.
AgentFactory = Callable[..., AgentProtocol]


class AgentRegistry:
    """Registry mapping string runtime names to agent factory callables.

    Factories are zero-argument callables by convention (``() -> AgentProtocol``)
    but may accept keyword arguments for runtime configuration (model name, etc.).

    Args:
        factories: Optional initial mapping of name to factory.  When omitted
            the registry starts empty; use :meth:`register` or
            :meth:`default` to populate it.
    """

    def __init__(
        self,
        factories: dict[str, AgentFactory] | None = None,
    ) -> None:
        self._factories: dict[str, AgentFactory] = dict(factories or {})

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def default(cls) -> AgentRegistry:
        """Return a registry pre-populated with the built-in runtimes.

        Currently registers ``"claude-code"`` -> :class:`~agentry.agents.claude_code.ClaudeCodeAgent`.
        """
        registry = cls()
        registry.register("claude-code", ClaudeCodeAgent)
        return registry

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, name: str, factory: AgentFactory) -> None:
        """Register a factory under *name*.

        Args:
            name: The runtime identifier (e.g. ``"claude-code"``).
            factory: A callable that produces an :class:`~agentry.agents.protocol.AgentProtocol`
                instance.  Typically the class itself.
        """
        self._factories[name] = factory

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, name: str, **kwargs: Any) -> AgentProtocol:
        """Resolve and instantiate an agent runtime by name.

        Args:
            name: The runtime identifier to look up.
            **kwargs: Keyword arguments forwarded to the factory function.

        Returns:
            A configured :class:`~agentry.agents.protocol.AgentProtocol` instance.

        Raises:
            KeyError: If *name* is not registered.
        """
        if name not in self._factories:
            available = ", ".join(sorted(self._factories))
            raise KeyError(
                f"Agent runtime '{name}' is not registered. "
                f"Available runtimes: [{available}]."
            )
        factory = self._factories[name]
        return factory(**kwargs)

    def get_factory(self, name: str) -> AgentFactory:
        """Return the raw factory callable for *name* without instantiating it.

        Args:
            name: The runtime identifier to look up.

        Returns:
            The factory callable.

        Raises:
            KeyError: If *name* is not registered.
        """
        if name not in self._factories:
            available = ", ".join(sorted(self._factories))
            raise KeyError(
                f"Agent runtime '{name}' is not registered. "
                f"Available runtimes: [{available}]."
            )
        return self._factories[name]

    def list_runtimes(self) -> list[str]:
        """Return a sorted list of registered runtime names."""
        return sorted(self._factories)
