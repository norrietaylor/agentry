"""Exceptions for the environment binder subsystem."""

from __future__ import annotations


class BinderError(Exception):
    """Base class for environment binder errors."""


class NotAGitRepositoryError(BinderError):
    """Raised when an operation requires a git repository but the target is not one.

    Example::

        raise NotAGitRepositoryError(
            "/tmp/not-a-repo",
            "git-diff inputs require a git repository",
        )
    """

    def __init__(self, path: str, reason: str = "") -> None:
        self.path = path
        self.reason = reason
        message = f"Target directory is not a git repository: {path!r}"
        if reason:
            message = f"{message}. {reason}"
        super().__init__(message)


class UnsupportedToolError(BinderError):
    """Raised when a workflow declares a tool not supported by the active binder."""

    def __init__(self, tool_name: str, binder_name: str) -> None:
        self.tool_name = tool_name
        self.binder_name = binder_name
        super().__init__(
            f"Tool {tool_name!r} is not supported by binder {binder_name!r}"
        )


class PathTraversalError(BinderError):
    """Raised when a repository:read request tries to escape the repository root.

    Example::

        raise PathTraversalError(
            "/tmp/repo",
            "../outside/secret.txt",
        )
    """

    def __init__(self, repo_root: str, requested_path: str) -> None:
        self.repo_root = repo_root
        self.requested_path = requested_path
        super().__init__(
            f"Path traversal detected: {requested_path!r} resolves outside "
            f"repository root {repo_root!r}"
        )


class CommandNotAllowedError(BinderError):
    """Raised when shell:execute receives a command not in the read-only allowlist.

    Example::

        raise CommandNotAllowedError("rm -rf /tmp/foo")
    """

    def __init__(self, command: str) -> None:
        self.command = command
        super().__init__(
            f"Command {command!r} is not in the shell:execute allowlist. "
            "Only read-only commands are permitted: "
            "git log, git diff, git show, git blame, ls, find, grep, cat, head, tail, wc."
        )
