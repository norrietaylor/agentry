"""LocalBinder: environment binder for local filesystem execution.

Resolves workflow inputs from the local filesystem, binds tool capabilities
to local implementations, and maps outputs to the .agentry/runs/ directory.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agentry.binders.exceptions import NotAGitRepositoryError, UnsupportedToolError

# Tools supported by the local binder.
# Tool bindings (implementations) are provided in T03.3.
SUPPORTED_TOOLS = frozenset({"repository:read", "shell:execute"})


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

        Concrete callable implementations are wired up in T03.3. This skeleton
        validates tool names and returns placeholder stubs so that the protocol
        contract can be verified independently.

        Args:
            tool_declarations: Tool identifiers declared in the workflow
                (e.g. ``["repository:read", "shell:execute"]``).

        Returns:
            Mapping of tool name to implementation (stubs in T03.1, real
            implementations in T03.3).

        Raises:
            UnsupportedToolError: If any declared tool is not in
                :data:`SUPPORTED_TOOLS`.
        """
        bindings: dict[str, Any] = {}
        for tool_name in tool_declarations:
            if tool_name not in SUPPORTED_TOOLS:
                raise UnsupportedToolError(tool_name, self.name)
            # Real implementations are provided in T03.3.
            # For now, store a sentinel so callers know the tool is bound.
            bindings[tool_name] = _unimplemented_tool_stub(tool_name)
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

        Implementation is provided in T03.2. This skeleton validates that the
        target directory is a git repository and raises a clear error if not.

        Args:
            ref: The git ref from ``--input diff=<ref>``.
            spec: The input declaration spec (may contain ``target`` key).

        Returns:
            The raw output of ``git diff <ref>``.

        Raises:
            NotAGitRepositoryError: If the target is not a git repository.
        """
        target = spec.get("target", os.getcwd())
        _assert_git_repo(target)
        raise NotImplementedError(
            "_resolve_git_diff() will be implemented in T03.2"
        )

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
# Internal helpers
# ---------------------------------------------------------------------------


def _unimplemented_tool_stub(tool_name: str) -> Any:
    """Return a callable that raises NotImplementedError with a helpful message."""

    def _stub(*args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(
            f"Tool {tool_name!r} binding is not yet implemented. "
            "Concrete tool implementations are provided in T03.3."
        )

    _stub.__name__ = tool_name.replace(":", "_")
    return _stub
