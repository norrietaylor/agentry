"""LocalBinder: environment binder for local filesystem execution.

Resolves workflow inputs from the local filesystem, binds tool capabilities
to local implementations, and maps outputs to the .agentry/runs/ directory.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

from agentry.binders.exceptions import (
    CommandNotAllowedError,
    NotAGitRepositoryError,
    PathTraversalError,
    UnsupportedToolError,
)

# Tools supported by the local binder.
SUPPORTED_TOOLS = frozenset({"repository:read", "shell:execute", "pr:create"})

# Allowlist of permitted executable names for shell:execute.
_SHELL_ALLOWLIST: frozenset[str] = frozenset(
    {"git", "ls", "find", "grep", "cat", "head", "tail", "wc"}
)

# For git, only these sub-commands are allowed (for the shell:execute tool).
_GIT_SUBCOMMAND_ALLOWLIST: frozenset[str] = frozenset(
    {"log", "diff", "show", "blame"}
)


def _assert_git_repo(path: str | Path) -> Path:
    """Verify that *path* is a git repository root or a subdirectory thereof.

    Checks for the presence of a ``.git`` entry (directory or file, to support
    git worktrees) at the given path.

    Args:
        path: Filesystem path to check.

    Returns:
        The resolved absolute ``Path`` object.

    Raises:
        NotAGitRepositoryError: If ``.git`` is not present at ``path``.
    """
    resolved = Path(path).resolve()
    if not (resolved / ".git").exists():
        raise NotAGitRepositoryError(
            str(resolved),
            "Ensure --target points to a directory that contains a .git entry.",
        )
    return resolved


class LocalBinder:
    """Environment binder for local filesystem execution.

    Implements the :class:`~agentry.binders.protocol.EnvironmentBinder` protocol
    for running agents against a local git repository.

    Attributes:
        name: Human-readable name used for logging and error messages.
    """

    name: str = "local"

    # ------------------------------------------------------------------
    # EnvironmentBinder protocol
    # ------------------------------------------------------------------

    def resolve_inputs(
        self,
        input_declarations: dict[str, Any],
        provided_values: dict[str, str],
    ) -> dict[str, Any]:
        """Resolve abstract input declarations to concrete values.

        Handles input types:

        - ``git-diff``: Deferred to
          :meth:`~agentry.binders.local.LocalBinder._resolve_git_diff` (T03.2).
        - ``repository-ref``: Deferred to
          :meth:`~agentry.binders.local.LocalBinder._resolve_repository_ref` (T03.2).
        - ``document-ref``: Raw string pass-through for Phase 1.
        - ``string``: Raw string pass-through.

        Args:
            input_declarations: The workflow's input block (name -> input spec dict).
            provided_values: User-supplied values from ``--input key=value`` CLI args.

        Returns:
            A mapping of input name to resolved concrete value.

        Raises:
            ValueError: If a required input is missing from *provided_values*.
            NotAGitRepositoryError: If a git-dependent input targets a non-git dir.
        """
        resolved: dict[str, Any] = {}

        for name, spec in input_declarations.items():
            required = spec.get("required", False)
            input_type = spec.get("type", "string")

            if name not in provided_values:
                if required:
                    raise ValueError(
                        f"Required input {name!r} was not provided. "
                        f"Pass it with --input {name}=<value>."
                    )
                # Optional inputs not provided are skipped (None).
                resolved[name] = None
                continue

            raw_value = provided_values[name]

            if input_type == "git-diff":
                resolved[name] = self._resolve_git_diff(raw_value, spec)
            elif input_type == "repository-ref":
                resolved[name] = self._resolve_repository_ref(raw_value, spec)
            else:
                # string, document-ref, etc. — pass through as-is.
                resolved[name] = raw_value

        return resolved

    def bind_tools(
        self,
        tool_declarations: list[str],
    ) -> dict[str, Any]:
        """Bind declared tool names to their local implementations.

        Wires ``repository:read`` to a concrete implementation that reads files
        from the resolved repository path with path traversal protection, and
        ``shell:execute`` to an implementation that enforces a hardcoded allowlist
        of read-only commands.

        Args:
            tool_declarations: Tool identifiers declared in the workflow
                (e.g. ``["repository:read", "shell:execute"]``).

        Returns:
            Mapping of tool name to a concrete callable implementation.

        Raises:
            UnsupportedToolError: If any declared tool is not in
                :data:`SUPPORTED_TOOLS`.
        """
        bindings: dict[str, Any] = {}
        for tool_name in tool_declarations:
            if tool_name not in SUPPORTED_TOOLS:
                raise UnsupportedToolError(tool_name, self.name)
            if tool_name == "repository:read":
                bindings[tool_name] = _make_repository_read()
            elif tool_name == "shell:execute":
                bindings[tool_name] = _make_shell_execute()
            elif tool_name == "pr:create":
                bindings[tool_name] = _make_pr_create()
        return bindings

    def map_outputs(
        self,
        output_declarations: dict[str, Any],
        target_dir: str,
        run_id: str,
    ) -> dict[str, str]:
        """Map output declarations to local filesystem paths.

        Outputs land in ``<target_dir>/.agentry/runs/<run_id>/``.

        Args:
            output_declarations: The workflow's output block.
            target_dir: Absolute path to the target directory.
            run_id: Timestamp-based identifier, e.g. ``"20260101T120000"``.

        Returns:
            Mapping of logical output name to absolute path string.
        """
        runs_dir = Path(target_dir) / ".agentry" / "runs" / run_id
        paths: dict[str, str] = {
            "output": str(runs_dir / "output.json"),
            "execution_record": str(runs_dir / "execution-record.json"),
        }
        # Preserve any extra declared output paths.
        for declared_path in output_declarations.get("output_paths", []):
            name = Path(declared_path).stem
            paths[name] = str(runs_dir / Path(declared_path).name)
        return paths

    def generate_pipeline_config(self) -> dict[str, Any]:
        """CI pipeline generation — not available in Phase 1.

        Raises:
            NotImplementedError: Always. CI generation is a Phase 3 feature.
        """
        raise NotImplementedError(
            "generate_pipeline_config() is not implemented for the local binder. "
            "CI pipeline generation is a Phase 3 feature (GitHub Actions binder)."
        )

    # ------------------------------------------------------------------
    # Private helpers — implementations filled in by T03.2
    # ------------------------------------------------------------------

    def _resolve_git_diff(self, ref: str, spec: dict[str, Any]) -> str:
        """Resolve a git-diff input by running ``git diff <ref>``.

        Runs ``git diff <ref>`` in the target directory via subprocess and
        returns the output as a string.

        Args:
            ref: The git ref from ``--input diff=<ref>`` (e.g. ``"HEAD~1"``).
            spec: The input declaration spec (may contain ``target`` key).

        Returns:
            The raw output of ``git diff <ref>``.

        Raises:
            NotAGitRepositoryError: If the target is not a git repository.
            subprocess.CalledProcessError: If git diff returns a non-zero exit code.
        """
        target = spec.get("target", os.getcwd())
        resolved_target = _assert_git_repo(target)
        result = subprocess.run(
            ["git", "diff", ref],
            cwd=str(resolved_target),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout

    def _resolve_repository_ref(self, ref: str, spec: dict[str, Any]) -> str:
        """Resolve a repository-ref input to the absolute target path.

        Implementation is provided in T03.2. This skeleton validates that the
        target directory is a git repository and raises a clear error if not.

        Args:
            ref: The raw value from ``--input <name>=<ref>`` (typically a path).
            spec: The input declaration spec (may contain ``target`` key).

        Returns:
            The absolute path of the verified git repository directory.

        Raises:
            NotAGitRepositoryError: If the target is not a git repository.
        """
        target = spec.get("target", ref or os.getcwd())
        resolved = _assert_git_repo(target)
        return str(resolved)


# ---------------------------------------------------------------------------
# Tool implementation factories (T03.3)
# ---------------------------------------------------------------------------


def _make_repository_read() -> Any:
    """Return a callable implementing the ``repository:read`` tool.

    The returned function reads a file path relative to a repository root,
    enforcing that the resolved path stays within the repository root (path
    traversal protection).

    The callable signature is::

        def repository_read(*, repo_root: str, path: str) -> str: ...

    Args:
        repo_root: Absolute path to the repository root.
        path: Relative path to the file within the repository.

    Returns:
        File contents as a string.

    Raises:
        PathTraversalError: If *path* resolves outside *repo_root* (including
            ``../`` traversal and symlink attacks).
        FileNotFoundError: If the resolved path does not exist.
        IsADirectoryError: If the resolved path is a directory, not a file.
    """

    def repository_read(*, repo_root: str, path: str) -> str:
        root = Path(repo_root).resolve()
        # Resolve the candidate path. We must handle symlinks properly:
        # Path.resolve() follows symlinks, so resolving the candidate gives us
        # the true on-disk location for symlink attack prevention.
        candidate = (root / path).resolve()

        # Reject traversal: the resolved path must start with the repo root.
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise PathTraversalError(str(root), path) from exc

        if not candidate.exists():
            raise FileNotFoundError(
                f"File not found: {path!r} (resolved to {candidate})"
            )
        if candidate.is_dir():
            raise IsADirectoryError(
                f"Path {path!r} is a directory; a file path is required."
            )

        return candidate.read_text()

    repository_read.__name__ = "repository_read"
    return repository_read


def _validate_shell_command(command: str) -> None:
    """Validate that *command* is in the read-only allowlist.

    Parses the command string using shell-like tokenisation and checks:
    1. The executable (first token) is in :data:`_SHELL_ALLOWLIST`.
    2. For ``git`` commands, the sub-command (second token) is in
       :data:`_GIT_SUBCOMMAND_ALLOWLIST`.

    Args:
        command: The raw shell command string to validate.

    Raises:
        CommandNotAllowedError: If the command or git sub-command is not in the
            allowlist, or if the command string is empty.
    """
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise CommandNotAllowedError(command) from exc

    if not tokens:
        raise CommandNotAllowedError(command)

    executable = tokens[0]

    # Strip any path prefix so "/usr/bin/git" becomes "git".
    executable_name = Path(executable).name

    if executable_name not in _SHELL_ALLOWLIST:
        raise CommandNotAllowedError(command)

    if executable_name == "git" and (
        len(tokens) < 2 or tokens[1] not in _GIT_SUBCOMMAND_ALLOWLIST
    ):
        raise CommandNotAllowedError(command)


def _make_shell_execute() -> Any:
    """Return a callable implementing the ``shell:execute`` tool.

    The returned function validates the command against a read-only allowlist
    before executing it. Permitted executables: ``git`` (sub-commands: ``log``,
    ``diff``, ``show``, ``blame``), ``ls``, ``find``, ``grep``, ``cat``,
    ``head``, ``tail``, ``wc``.

    The callable signature is::

        def shell_execute(*, command: str, cwd: str | None = None) -> str: ...

    Args:
        command: The shell command to execute.
        cwd: Optional working directory for the command.

    Returns:
        Combined stdout of the executed command as a string.

    Raises:
        CommandNotAllowedError: If the command is not in the allowlist.
        subprocess.CalledProcessError: If the command exits with non-zero status.
    """

    def shell_execute(*, command: str, cwd: str | None = None) -> str:
        _validate_shell_command(command)
        result = subprocess.run(
            command,
            shell=True,  # noqa: S602 — command is validated against allowlist
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout

    shell_execute.__name__ = "shell_execute"
    return shell_execute


# Branch names that must never be pushed to directly.
_PROTECTED_BRANCHES: frozenset[str] = frozenset({"main", "master"})


def _make_pr_create() -> Any:
    """Return a callable implementing the ``pr:create`` tool.

    The returned function creates a branch, commits staged changes, pushes
    to origin, and opens a pull request via the ``gh`` CLI.  It enforces
    safety guardrails: no force-push, no push to protected branches, and
    no auto-merge.

    The callable signature is::

        def pr_create(
            *,
            branch_name: str,
            commit_message: str,
            base_branch: str = "main",
            title: str,
            body: str,
            label: str = "agent-proposed",
            files: list[str] | None = None,
            cwd: str | None = None,
        ) -> dict[str, Any]: ...

    Returns:
        A dict with ``branch``, ``pr_url``, and ``status`` keys on success,
        or ``branch``, ``error``, and ``status`` keys on failure.

    Raises:
        ValueError: If *branch_name* matches a protected branch name.
    """

    def pr_create(
        *,
        branch_name: str,
        commit_message: str,
        base_branch: str = "main",
        title: str,
        body: str,
        label: str = "agent-proposed",
        files: list[str] | None = None,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        # Guard: never push to a protected branch.
        if branch_name in _PROTECTED_BRANCHES:
            raise ValueError(
                f"Cannot create a PR from protected branch {branch_name!r}. "
                "Use a feature branch name instead."
            )

        work_dir = cwd or os.getcwd()

        def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                cmd,
                cwd=work_dir,
                capture_output=True,
                text=True,
                check=True,
            )

        try:
            # 1. Create a new branch from base_branch.
            _run(["git", "checkout", "-b", branch_name, base_branch])

            # 2. Stage files.
            if files is not None:
                _run(["git", "add", *files])
            else:
                _run(["git", "add", "-A"])

            # 3. Commit.
            _run(["git", "commit", "-m", commit_message])

            # 4. Push (never force-push).
            _run(["git", "push", "-u", "origin", branch_name])

            # 5. Open PR via gh CLI (never auto-merge).
            gh_result = _run(
                [
                    "gh",
                    "pr",
                    "create",
                    "--base",
                    base_branch,
                    "--title",
                    title,
                    "--body",
                    body,
                    "--label",
                    label,
                ]
            )

            pr_url = gh_result.stdout.strip()
            return {
                "branch": branch_name,
                "pr_url": pr_url,
                "status": "created",
            }

        except subprocess.CalledProcessError as exc:
            return {
                "branch": branch_name,
                "error": (exc.stderr or exc.stdout or str(exc)).strip(),
                "status": "failed",
            }

    pr_create.__name__ = "pr_create"
    return pr_create
