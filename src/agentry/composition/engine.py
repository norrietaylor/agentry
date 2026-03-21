"""Composition engine for async DAG-based multi-node workflow execution.

Schedules composition nodes using ``graphlib.TopologicalSorter`` for incremental
dispatch, running independent nodes concurrently via ``asyncio.gather()``.  Each
node loads its workflow definition, provisions a runner, executes the agent, and
records the result.

Usage::

    from agentry.composition.engine import CompositionEngine

    engine = CompositionEngine(
        composition=workflow.composition,
        runner_detector=detector,
        binder=binder,
        run_dir=Path("/tmp/run-001"),
        workflow_base_dir=Path("/workflows"),
    )
    record = await engine.execute()
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from graphlib import TopologicalSorter
from pathlib import Path
from typing import Any

from agentry.binders.local import LocalBinder
from agentry.composition.record import (
    CompositionRecord,
    CompositionStatus,
    NodeStatus,
)
from agentry.executor import ExecutionRecord
from agentry.models.composition import CompositionBlock, CompositionStep
from agentry.models.workflow import WorkflowDefinition
from agentry.parser import load_workflow_file
from agentry.runners.detector import RunnerDetector
from agentry.runners.protocol import AgentConfig, RunnerContext

logger = logging.getLogger(__name__)


class CompositionEngine:
    """Async DAG scheduler for composed workflow execution.

    Uses ``graphlib.TopologicalSorter`` incremental API (``prepare()``,
    ``get_ready()``, ``done()``) to dispatch nodes whose dependencies have
    been satisfied.  Independent nodes run concurrently via
    ``asyncio.gather()``.

    Args:
        composition: The composition block defining the DAG steps.
        runner_detector: Detector used to provision a runner per node.
        binder: Local environment binder for input resolution and tool binding.
        run_dir: Root directory for this execution run.  Per-node outputs are
            written to ``<run_dir>/<node_id>/result.json``.
        workflow_base_dir: Base directory from which node workflow YAML files
            are resolved (``workflow_base_dir / step.workflow``).
    """

    def __init__(
        self,
        composition: CompositionBlock,
        runner_detector: RunnerDetector,
        binder: LocalBinder,
        run_dir: Path,
        workflow_base_dir: Path,
    ) -> None:
        self._composition = composition
        self._runner_detector = runner_detector
        self._binder = binder
        self._run_dir = run_dir
        self._workflow_base_dir = workflow_base_dir

        # Per-node execution records, populated during execute().
        self._node_records: dict[str, ExecutionRecord | None] = {}
        self._node_statuses: dict[str, NodeStatus] = {}

    async def execute(self) -> CompositionRecord:
        """Execute all composition nodes in topological order.

        Nodes whose dependencies are satisfied are dispatched concurrently.
        On the happy path (all nodes succeed), returns a
        :class:`~agentry.composition.record.CompositionRecord` with
        ``overall_status=COMPLETED``.

        Returns:
            A :class:`CompositionRecord` summarising per-node results,
            overall status, and wall-clock timing.
        """
        wall_clock_start = time.time()

        # Build the dependency graph for TopologicalSorter.
        graph: dict[str, set[str]] = {}
        step_map: dict[str, CompositionStep] = {}
        for step in self._composition.steps:
            node_id = step.node_id
            graph[node_id] = set(step.depends_on)
            step_map[node_id] = step
            # Initialise statuses to NOT_REACHED; they will be updated as
            # nodes are executed.
            self._node_statuses[node_id] = NodeStatus.NOT_REACHED
            self._node_records[node_id] = None

        sorter: TopologicalSorter[str] = TopologicalSorter(graph)
        sorter.prepare()

        while sorter.is_active():
            ready_nodes = sorter.get_ready()
            if not ready_nodes:
                # All remaining nodes are blocked; should not happen with a
                # valid DAG but guard defensively.
                break  # pragma: no cover

            # Dispatch all ready nodes concurrently.
            tasks = [
                self._execute_node(step_map[node_id])
                for node_id in ready_nodes
            ]
            await asyncio.gather(*tasks)

            # Mark nodes as done so the sorter can release dependents.
            for node_id in ready_nodes:
                sorter.done(node_id)

        wall_clock_end = time.time()

        # Determine overall status from per-node statuses.
        overall_status = self._compute_overall_status()

        record = CompositionRecord(
            node_statuses=dict(self._node_statuses),
            node_records=dict(self._node_records),
            overall_status=overall_status,
            wall_clock_start=wall_clock_start,
            wall_clock_end=wall_clock_end,
        )

        # Persist the composition record.
        record.save(self._run_dir)

        return record

    async def _execute_node(self, step: CompositionStep) -> None:
        """Execute a single composition node.

        Loads the workflow, provisions a runner, executes the agent, writes
        the node output, and records the status.  Runner teardown is always
        performed in a ``finally`` block.

        Args:
            step: The composition step to execute.
        """
        node_id = step.node_id
        logger.info("Executing composition node: %s", node_id)

        # Resolve node inputs (placeholder for T04 data passing).
        _resolved_inputs = self._resolve_node_inputs(step)

        # Load the workflow definition for this node.
        workflow_path = self._workflow_base_dir / step.workflow
        workflow = load_workflow_file(str(workflow_path))

        # Provision a runner based on the workflow's safety configuration.
        runner = self._runner_detector.get_runner(workflow.safety)
        runner_context: RunnerContext | None = None

        try:
            # Provision the execution environment.
            runner_context = runner.provision(
                safety_block=workflow.safety,
                resolved_inputs=_resolved_inputs,
            )

            # Build agent configuration from the loaded workflow.
            agent_config = AgentConfig(
                system_prompt=self._build_system_prompt(workflow),
                resolved_inputs=_resolved_inputs,
                tool_names=list(workflow.tools.capabilities),
                llm_config=self._build_llm_config(workflow),
            )

            # Execute the agent.
            result = runner.execute(runner_context, agent_config)

            # Extract the execution record.
            exec_record = result.execution_record

            # Write node output to disk.
            self._write_node_output(node_id, exec_record)

            # Record success.
            self._node_records[node_id] = exec_record
            if exec_record is not None and exec_record.error:
                self._node_statuses[node_id] = NodeStatus.FAILED
            else:
                self._node_statuses[node_id] = NodeStatus.COMPLETED

        except Exception as exc:
            logger.error(
                "Node %s failed: %s", node_id, exc, exc_info=True
            )
            # Apply failure policy (placeholder for T03 -- re-raises).
            try:
                self._apply_failure_policy(step, exc)
            except Exception:
                self._node_statuses[node_id] = NodeStatus.FAILED
                # Create a minimal error record.
                error_record = ExecutionRecord(error=str(exc))
                self._node_records[node_id] = error_record
        finally:
            if runner_context is not None:
                try:
                    runner.teardown(runner_context)
                except Exception:
                    logger.warning(
                        "Teardown failed for node %s", node_id, exc_info=True
                    )

    def _resolve_node_inputs(self, step: CompositionStep) -> dict[str, str]:
        """Resolve inputs for a composition node.

        Placeholder hook for T04 data-passing integration.  Currently
        returns an empty dict.

        Args:
            step: The composition step whose inputs should be resolved.

        Returns:
            An empty dict (to be replaced by T04 sub-tasks).
        """
        return {}

    def _apply_failure_policy(
        self, step: CompositionStep, error: Exception
    ) -> None:
        """Apply the failure policy for a failed node.

        Placeholder hook for T03 failure-policy integration.  Currently
        re-raises the original exception (abort behaviour).

        Args:
            step: The composition step that failed.
            error: The exception raised during execution.

        Raises:
            Exception: Always re-raises the original error.
        """
        raise error

    def _build_system_prompt(self, workflow: WorkflowDefinition) -> str:
        """Build a system prompt string from the workflow definition.

        Reads the system prompt file if specified in the workflow's identity
        block, otherwise returns a default prompt derived from the workflow
        identity.

        Args:
            workflow: The parsed ``WorkflowDefinition``.

        Returns:
            The system prompt text.
        """
        # If a system prompt file is declared in the model block, load it.
        if workflow.model.system_prompt:
            prompt_path = self._workflow_base_dir / workflow.model.system_prompt
            if prompt_path.exists():
                return prompt_path.read_text(encoding="utf-8")

        # Fallback: construct a basic prompt from the identity block.
        identity = workflow.identity
        description: str = identity.description
        return f"You are {identity.name}. {description}"

    def _build_llm_config(self, workflow: WorkflowDefinition) -> Any:
        """Build an LLM config from the workflow's model block.

        Args:
            workflow: The parsed ``WorkflowDefinition``.

        Returns:
            An ``LLMConfig`` instance derived from the workflow's model block.
        """
        from agentry.llm.models import LLMConfig

        model_block = workflow.model
        return LLMConfig(
            model=model_block.model_id,
            max_tokens=model_block.max_tokens,
            temperature=model_block.temperature,
        )

    def _write_node_output(
        self, node_id: str, exec_record: ExecutionRecord | None
    ) -> None:
        """Write the execution result for a node to disk.

        Creates ``<run_dir>/<node_id>/result.json`` containing the
        serialised execution record.

        Args:
            node_id: The node identifier.
            exec_record: The execution record to serialise, or ``None``.
        """
        node_dir = self._run_dir / node_id
        node_dir.mkdir(parents=True, exist_ok=True)
        output_path = node_dir / "result.json"

        if exec_record is not None:
            data = exec_record.to_dict()
        else:
            data = {"error": "No execution record available."}

        output_path.write_text(json.dumps(data, indent=2))

    def _compute_overall_status(self) -> CompositionStatus:
        """Derive the overall composition status from per-node statuses.

        Returns:
            ``COMPLETED`` if all nodes completed, ``FAILED`` if any node
            failed, or ``PARTIAL`` if some succeeded and some did not.
        """
        statuses = set(self._node_statuses.values())

        if not statuses:
            return CompositionStatus.COMPLETED

        if statuses == {NodeStatus.COMPLETED}:
            return CompositionStatus.COMPLETED

        if NodeStatus.FAILED in statuses:
            # If some completed and some failed, it could be partial,
            # but on the happy-path-only implementation we treat any
            # failure as overall FAILED.
            if NodeStatus.COMPLETED in statuses:
                return CompositionStatus.PARTIAL
            return CompositionStatus.FAILED

        # Mix of COMPLETED / SKIPPED / NOT_REACHED.
        if NodeStatus.COMPLETED in statuses:
            return CompositionStatus.PARTIAL

        return CompositionStatus.FAILED
