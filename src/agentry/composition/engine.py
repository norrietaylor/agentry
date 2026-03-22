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
from collections.abc import Callable
from graphlib import TopologicalSorter
from pathlib import Path
from typing import Any

from agentry.binders.local import LocalBinder
from agentry.composition import data_passing as _data_passing
from agentry.composition.failure import (
    CompositionAbortError,
    NodeFailure,
    handle_abort,
    handle_retry,
    handle_skip,
)
from agentry.composition.record import (
    CompositionRecord,
    CompositionStatus,
    NodeStatus,
)
from agentry.models.execution import ExecutionRecord
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
        on_node_start: Callable[[str], None] | None = None,
        on_node_complete: Callable[[str, float], None] | None = None,
        on_node_fail: Callable[[str, str], None] | None = None,
        on_node_skip: Callable[[str], None] | None = None,
    ) -> None:
        self._composition = composition
        self._runner_detector = runner_detector
        self._binder = binder
        self._run_dir = run_dir
        self._workflow_base_dir = workflow_base_dir

        # Optional event callbacks for progress display.
        self._on_node_start = on_node_start
        self._on_node_complete = on_node_complete
        self._on_node_fail = on_node_fail
        self._on_node_skip = on_node_skip

        # Per-node execution records, populated during execute().
        self._node_records: dict[str, ExecutionRecord | None] = {}
        self._node_statuses: dict[str, NodeStatus] = {}
        # Per-node wall-clock start times for duration reporting.
        self._node_start_times: dict[str, float] = {}
        # Per-node output paths for file-based data passing.
        # Populated with the Path to result.json for successfully completed
        # nodes so downstream nodes can read their outputs.
        self._node_outputs: dict[str, Path] = {}
        # Per-node failure paths for skip-policy failures.
        # Populated with the Path to result.json (which contains the
        # NodeFailure JSON) when a node fails under the skip policy.
        self._node_failures: dict[str, Path] = {}
        # Live composition record reference, set during execute().  Used by
        # the abort handler to update overall status.
        self._live_record: CompositionRecord | None = None
        # Flag set when abort policy triggers, to stop scheduling new nodes.
        self._aborted: bool = False

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

        # Create a live record that handlers (e.g. abort) can update
        # directly during execution.
        self._live_record = CompositionRecord(
            node_statuses=self._node_statuses,
            node_records=self._node_records,
            overall_status=CompositionStatus.COMPLETED,
            wall_clock_start=wall_clock_start,
            wall_clock_end=0.0,
        )

        sorter: TopologicalSorter[str] = TopologicalSorter(graph)
        sorter.prepare()

        while sorter.is_active():
            # If an abort was triggered, stop scheduling new nodes and
            # leave remaining nodes as NOT_REACHED.
            if self._aborted:
                break

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
        the node output, and records the status.  On failure, dispatches to
        the appropriate failure policy handler (abort, skip, or retry).

        Args:
            step: The composition step to execute.
        """
        node_id = step.node_id
        logger.info("Executing composition node: %s", node_id)

        # Fire the node-start callback.
        self._node_start_times[node_id] = time.time()
        if self._on_node_start is not None:
            try:
                self._on_node_start(node_id)
            except Exception:  # noqa: BLE001
                logger.debug("on_node_start callback raised", exc_info=True)

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
            agent_block = getattr(workflow, "agent", None)
            agent_name = agent_block.runtime if agent_block else "claude-code"
            agent_cfg = {}
            if agent_block:
                agent_cfg["model"] = agent_block.model
            agent_config = AgentConfig(
                system_prompt=self._build_system_prompt(workflow),
                resolved_inputs=_resolved_inputs,
                tool_names=list(workflow.tools.capabilities),
                agent_name=agent_name,
                agent_config=agent_cfg,
            )

            # Execute the agent.
            result = runner.execute(runner_context, agent_config)

            # Extract the execution record.
            exec_record = result.execution_record

            # Write node output to disk.
            self._write_node_output(node_id, exec_record)

            # Store the output path so downstream nodes can resolve inputs
            # via file-based data passing.
            self._node_outputs[node_id] = (
                self._run_dir / node_id / "result.json"
            )

            # Record success.
            self._node_records[node_id] = exec_record
            if exec_record is not None and exec_record.error:
                self._node_statuses[node_id] = NodeStatus.FAILED
                _duration = time.time() - self._node_start_times.get(
                    node_id, time.time()
                )
                if self._on_node_fail is not None:
                    try:
                        self._on_node_fail(node_id, exec_record.error)
                    except Exception:  # noqa: BLE001
                        logger.debug(
                            "on_node_fail callback raised", exc_info=True
                        )
            else:
                self._node_statuses[node_id] = NodeStatus.COMPLETED
                _duration = time.time() - self._node_start_times.get(
                    node_id, time.time()
                )
                if self._on_node_complete is not None:
                    try:
                        self._on_node_complete(node_id, _duration)
                    except Exception:  # noqa: BLE001
                        logger.debug(
                            "on_node_complete callback raised", exc_info=True
                        )

        except Exception as exc:
            logger.error(
                "Node %s failed: %s", node_id, exc, exc_info=True
            )
            self._apply_failure_policy(step, exc)
        finally:
            if runner_context is not None:
                try:
                    runner.teardown(runner_context)
                except Exception:
                    logger.warning(
                        "Teardown failed for node %s",
                        node_id,
                        exc_info=True,
                    )

    def _resolve_node_inputs(self, step: CompositionStep) -> dict[str, str]:
        """Resolve inputs for a composition node using file-based data passing.

        Delegates to :func:`agentry.composition.data_passing.resolve_node_inputs`
        to translate ``<node_id>.output`` and ``<node_id>.output.<field>``
        source expressions into absolute file-path strings.

        Args:
            step: The composition step whose inputs should be resolved.

        Returns:
            A mapping from input key to absolute file path string.  Returns
            an empty dict when the step declares no inputs.
        """
        if not step.inputs:
            return {}
        return _data_passing.resolve_node_inputs(
            step=step,
            node_outputs=self._node_outputs,
            node_failures=self._node_failures,
        )

    def _apply_failure_policy(
        self, step: CompositionStep, error: Exception
    ) -> None:
        """Apply the failure policy for a failed node.

        Dispatches to the appropriate handler based on the step's
        ``failure.mode``:

        - **abort**: Marks remaining nodes as ``NOT_REACHED``, sets the
          composition status to ``FAILED``, and sets the ``_aborted`` flag
          so the scheduling loop stops.
        - **skip**: Creates a :class:`NodeFailure` object, stores it in
          ``_node_outputs`` for downstream propagation, marks the node as
          ``FAILED``, and continues execution.
        - **retry**: Re-executes the node up to ``max_retries`` times.  On
          exhaustion, falls through to the ``fallback`` policy.

        Args:
            step: The composition step that failed.
            error: The exception raised during execution.
        """
        node_id = step.node_id
        policy_mode = step.failure.mode

        if policy_mode == "abort":
            self._node_statuses[node_id] = NodeStatus.FAILED
            error_record = ExecutionRecord(error=str(error))
            self._node_records[node_id] = error_record
            self._fire_node_fail_callback(node_id, str(error))

            try:
                assert self._live_record is not None  # noqa: S101
                handle_abort(node_id, error, self._live_record)
            except CompositionAbortError:
                # Signal the main loop to stop dispatching new nodes.
                self._aborted = True

        elif policy_mode == "skip":
            failure = handle_skip(node_id, error, self._run_dir)
            self._node_statuses[node_id] = NodeStatus.FAILED
            error_record = ExecutionRecord(error=str(error))
            self._node_records[node_id] = error_record
            # Store the failure path so downstream nodes can detect it via
            # file-based data passing.  handle_skip saves to
            # <run_dir>/<node_id>/result.json.
            self._node_failures[node_id] = (
                self._run_dir / node_id / "result.json"
            )
            del failure  # NodeFailure object no longer needed in-memory.
            self._fire_node_fail_callback(node_id, str(error))
            # Fire the skip callback for progress display.
            if self._on_node_skip is not None:
                try:
                    self._on_node_skip(node_id)
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "on_node_skip callback raised", exc_info=True
                    )

        elif policy_mode == "retry":
            assert self._live_record is not None  # noqa: S101
            try:
                result = handle_retry(
                    node_id=node_id,
                    error=error,
                    step=step,
                    runner_detector=self._runner_detector,
                    workflow_loader=load_workflow_file,
                    binder=self._binder,
                    run_dir=self._run_dir,
                    record=self._live_record,
                )
            except CompositionAbortError:
                # Retry exhausted, fallback was abort.
                self._node_statuses[node_id] = NodeStatus.FAILED
                error_record = ExecutionRecord(error=str(error))
                self._node_records[node_id] = error_record
                self._fire_node_fail_callback(node_id, str(error))
                self._aborted = True
                return

            # handle_retry returns ExecutionResult on success or NodeFailure
            # if fallback was skip.
            if isinstance(result, NodeFailure):
                self._node_statuses[node_id] = NodeStatus.FAILED
                error_record = ExecutionRecord(error=str(error))
                self._node_records[node_id] = error_record
                # Store the failure path (handle_retry with skip fallback
                # saves the NodeFailure to <run_dir>/<node_id>/result.json).
                self._node_failures[node_id] = (
                    self._run_dir / node_id / "result.json"
                )
                self._fire_node_fail_callback(node_id, str(error))
            else:
                # Retry succeeded -- record the successful execution.
                exec_record = result.execution_record
                self._write_node_output(node_id, exec_record)
                # Store the output path for downstream data passing.
                self._node_outputs[node_id] = (
                    self._run_dir / node_id / "result.json"
                )
                self._node_records[node_id] = exec_record
                self._node_statuses[node_id] = NodeStatus.COMPLETED
                _duration = time.time() - self._node_start_times.get(
                    node_id, time.time()
                )
                if self._on_node_complete is not None:
                    try:
                        self._on_node_complete(node_id, _duration)
                    except Exception:  # noqa: BLE001
                        logger.debug(
                            "on_node_complete callback raised",
                            exc_info=True,
                        )

        else:
            # Unknown policy mode -- treat as abort.
            logger.warning(
                "Unknown failure policy '%s' for node '%s'; treating as abort.",
                policy_mode,
                node_id,
            )
            self._node_statuses[node_id] = NodeStatus.FAILED
            error_record = ExecutionRecord(error=str(error))
            self._node_records[node_id] = error_record
            self._fire_node_fail_callback(node_id, str(error))
            self._aborted = True

    def _fire_node_fail_callback(
        self, node_id: str, error_msg: str
    ) -> None:
        """Fire the on_node_fail callback, swallowing any exceptions.

        Args:
            node_id: The node that failed.
            error_msg: Human-readable error description.
        """
        if self._on_node_fail is not None:
            try:
                self._on_node_fail(node_id, error_msg)
            except Exception:  # noqa: BLE001
                logger.debug("on_node_fail callback raised", exc_info=True)

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

        Rules:

        - ``COMPLETED``: All nodes completed successfully.
        - ``FAILED``: An abort policy was triggered (``_aborted`` is set) or
          all nodes failed.
        - ``PARTIAL``: Some nodes completed and some failed under the skip
          policy (i.e. partial results are available).

        Returns:
            The appropriate :class:`CompositionStatus`.
        """
        statuses = set(self._node_statuses.values())

        if not statuses:
            return CompositionStatus.COMPLETED

        if statuses == {NodeStatus.COMPLETED}:
            return CompositionStatus.COMPLETED

        # If an abort was triggered, the composition is definitively failed.
        if self._aborted:
            return CompositionStatus.FAILED

        if NodeStatus.FAILED in statuses:
            # Some nodes completed and some failed with skip policy --
            # this is a partial result.
            if NodeStatus.COMPLETED in statuses:
                return CompositionStatus.PARTIAL
            return CompositionStatus.FAILED

        # Mix of COMPLETED / SKIPPED / NOT_REACHED.
        if NodeStatus.COMPLETED in statuses:
            return CompositionStatus.PARTIAL

        return CompositionStatus.FAILED
