"""Environment binder subsystem.

An ``EnvironmentBinder`` translates abstract workflow inputs (git-diff,
repository-ref) into concrete values for a specific execution environment.

Public API
----------
- :class:`~agentry.binders.protocol.EnvironmentBinder` — Protocol (interface).
- :class:`~agentry.binders.local.LocalBinder` — Built-in local filesystem binder.
- :class:`~agentry.binders.github_actions.GitHubActionsBinder` — GitHub Actions
  CI binder.
- :func:`~agentry.binders.registry.get_binder` — Factory: select and instantiate
  the correct binder (default: local).
- :func:`~agentry.binders.registry.discover_binders` — List all registered binders.
- :exc:`~agentry.binders.exceptions.NotAGitRepositoryError` — Raised when a
  git-dependent operation targets a non-git directory.
- :exc:`~agentry.binders.exceptions.UnsupportedToolError` — Raised when a workflow
  declares a tool not supported by the active binder.
"""

from agentry.binders.exceptions import (
    CommandNotAllowedError,
    NotAGitRepositoryError,
    PathTraversalError,
    UnsupportedToolError,
)
from agentry.binders.github_actions import GitHubActionsBinder
from agentry.binders.local import LocalBinder
from agentry.binders.protocol import EnvironmentBinder
from agentry.binders.registry import discover_binders, get_binder

__all__ = [
    "CommandNotAllowedError",
    "EnvironmentBinder",
    "GitHubActionsBinder",
    "LocalBinder",
    "NotAGitRepositoryError",
    "PathTraversalError",
    "UnsupportedToolError",
    "discover_binders",
    "get_binder",
]
