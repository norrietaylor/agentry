"""EnvironmentBinder protocol definition.

Defines the interface that all environment binders must implement. An environment
binder translates abstract workflow inputs into concrete values for a specific
execution environment (local, CI, Docker, etc.).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EnvironmentBinder(Protocol):
    """Protocol for environment binders.

    An EnvironmentBinder translates abstract workflow inputs into concrete values,
    binds tool capabilities to concrete implementations, and maps outputs to their
    destination. Each execution environment (local, CI, Docker) provides its own
    binder implementation.
    """

    def resolve_inputs(
        self,
        input_declarations: dict[str, Any],
        provided_values: dict[str, str],
    ) -> dict[str, Any]:
        """Resolve abstract input declarations to concrete values.

        Args:
            input_declarations: The workflow's input block (name -> input spec).
            provided_values: The values provided by the user (name -> raw string).

        Returns:
            A mapping of input name to resolved concrete value.

        Raises:
            ValueError: If a required input is missing or cannot be resolved.
            NotAGitRepositoryError: If a git-dependent input targets a non-git dir.
        """
        ...

    def bind_tools(
        self,
        tool_declarations: list[str],
    ) -> dict[str, Any]:
        """Bind declared tool names to concrete callable implementations.

        Args:
            tool_declarations: List of tool identifiers from the workflow definition
                (e.g., ["repository:read", "shell:execute"]).

        Returns:
            A mapping of tool name to its bound implementation callable.

        Raises:
            ValueError: If a declared tool is not supported by this binder.
        """
        ...

    def map_outputs(
        self,
        output_declarations: dict[str, Any],
        target_dir: str,
        run_id: str,
    ) -> dict[str, str]:
        """Map output declarations to concrete filesystem paths.

        Args:
            output_declarations: The workflow's output block.
            target_dir: The resolved target directory for this execution.
            run_id: A timestamp-based run identifier (e.g. "20260101T120000").

        Returns:
            A mapping of output name to absolute path where the output should be written.
        """
        ...

    def generate_pipeline_config(self) -> dict[str, Any]:
        """Generate CI pipeline configuration for this environment.

        Not implemented in Phase 1 (local binders only). CI generation is Phase 3.

        Raises:
            NotImplementedError: Always. CI generation is a Phase 3 feature.
        """
        ...
