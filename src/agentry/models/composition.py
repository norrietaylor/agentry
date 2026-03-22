"""Composition block model.

Multi-agent composition steps.  Parsed but not executed in Phase 1.
"""

from __future__ import annotations

import graphlib
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


class FailurePolicy(BaseModel):
    """Failure handling policy for a composition step."""

    model_config = ConfigDict(strict=True, extra="forbid")

    mode: Literal["abort", "skip", "retry"] = "abort"
    max_retries: int = 1
    fallback: Literal["abort", "skip"] = "abort"


class CompositionStep(BaseModel):
    """A single step in a composition DAG."""

    model_config = ConfigDict(strict=True, extra="forbid")

    name: str
    workflow: str
    depends_on: list[str] = []
    id: str | None = None
    failure: FailurePolicy = FailurePolicy()
    inputs: dict[str, str] = {}

    @property
    def node_id(self) -> str:
        """Return the resolved node ID (id if set, else name)."""
        return self.id if self.id is not None else self.name


# Pattern matching <node_id>.output or <node_id>.output.<field>
_SOURCE_EXPR_RE = re.compile(r"^([^.]+)\.output(?:\.[^.]+)?$")


class CompositionBlock(BaseModel):
    """Composition block: defines a DAG of workflow steps.

    Parsed and validated in Phase 1 but execution is deferred to Phase 2.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    steps: list[CompositionStep] = []

    @property
    def node_ids(self) -> list[str]:
        """Return resolved IDs for all steps (id if set, else name)."""
        return [step.node_id for step in self.steps]

    @model_validator(mode="after")
    def validate_dag(self) -> CompositionBlock:
        """Validate the composition DAG structure.

        Checks performed:
        1. All depends_on references resolve to existing node IDs.
        2. All input source expressions reference valid node IDs.
        3. No cycles exist in the dependency graph.
        4. Input source node IDs are listed in the step's depends_on.
        """
        if not self.steps:
            return self

        known_ids: set[str] = {step.node_id for step in self.steps}

        # 1. Validate depends_on references
        for step in self.steps:
            for dep in step.depends_on:
                if dep not in known_ids:
                    raise ValueError(
                        f"Step '{step.node_id}' has unknown depends_on reference: '{dep}'"
                    )

        # 2. Validate input source expressions
        for step in self.steps:
            for _key, source in step.inputs.items():
                match = _SOURCE_EXPR_RE.match(source)
                if not match:
                    raise ValueError(
                        f"Step '{step.node_id}' has invalid input source expression: '{source}'. "
                        f"Expected format '<node_id>.output' or '<node_id>.output.<field>'."
                    )
                ref_node = match.group(1)
                if ref_node not in known_ids:
                    raise ValueError(
                        f"Step '{step.node_id}' input references unknown node '{ref_node}' "
                        f"in source expression '{source}'"
                    )
                # Auto-add implicit dependency if not already listed
                if ref_node not in step.depends_on:
                    raise ValueError(
                        f"Step '{step.node_id}' input references node '{ref_node}' "
                        f"but '{ref_node}' is not listed in depends_on. "
                        f"Add '{ref_node}' to the depends_on list."
                    )

        # 3. Check for cycles using graphlib.TopologicalSorter
        graph: dict[str, set[str]] = {step.node_id: set(step.depends_on) for step in self.steps}
        sorter = graphlib.TopologicalSorter(graph)
        try:
            # prepare() raises CycleError if a cycle is detected
            sorter.prepare()
        except graphlib.CycleError as exc:
            # exc.args[1] is the list of nodes involved in the cycle
            cycle_nodes: list[str] = list(exc.args[1]) if len(exc.args) > 1 else []
            if cycle_nodes:
                # graphlib already includes the closing node in cycle_nodes (e.g. [A, B, A])
                cycle_path = " -> ".join(cycle_nodes)
                raise ValueError(f"Cycle detected: {cycle_path}") from exc
            raise ValueError(f"Cycle detected in composition graph: {exc}") from exc

        return self
