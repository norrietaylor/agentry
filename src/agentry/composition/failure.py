"""Failure handling for composition nodes.

Provides the :class:`NodeFailure` dataclass for representing node failures,
:class:`CompositionAbortError` for signalling abort to the engine, and
handler functions for each failure policy mode (abort, skip, retry).

Usage::

    from agentry.composition.failure import (
        CompositionAbortError,
        NodeFailure,
        handle_abort,
        handle_retry,
        handle_skip,
    )
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from agentry.composition.record import CompositionRecord
    from agentry.models.composition import CompositionStep
    from agentry.runners.protocol import ExecutionResult

    #: Signature for the node execution callback passed to ``handle_retry``.
    ExecuteNodeFn = Callable[
        ["CompositionStep", Any, Any, Path], "ExecutionResult"
    ]

logger = logging.getLogger(__name__)


class CompositionAbortError(Exception):
    """Raised by the abort handler to halt composition execution.

    The engine should catch this at the top-level scheduling loop to
    stop dispatching further nodes.
    """


@dataclass
class NodeFailure:
    """Structured representation of a failed composition node.

    When a node fails under the ``skip`` policy, this object is serialised
    to the node's ``result.json`` so that downstream nodes can detect and
    handle the failure via the ``_failure`` sentinel field.

    Attributes:
        node_id: Identifier of the node that failed.
        error: Human-readable error description.
        partial_output: Any partial output the node produced before failing.
        _failure: Sentinel field for downstream detection (always ``True``).
        retry_attempts: Per-attempt error records when the retry policy is
            used.  Each entry is a dict with at least a ``"error"`` key.
    """

    node_id: str
    error: str
    partial_output: dict[str, Any] | None = None
    _failure: bool = True
    retry_attempts: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary.

        Returns:
            A plain dict suitable for ``json.dumps``.
        """
        data: dict[str, Any] = {
            "node_id": self.node_id,
            "error": self.error,
            "_failure": self._failure,
        }
        if self.partial_output is not None:
            data["partial_output"] = self.partial_output
        if self.retry_attempts:
            data["retry_attempts"] = self.retry_attempts
        return data

    def save(self, path: Path) -> None:
        """Write the failure object to a JSON file.

        Args:
            path: Destination file path.  Parent directories are created
                if they do not exist.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))


def handle_abort(
    node_id: str,
    error: Exception,
    record: CompositionRecord,
) -> None:
    """Apply the **abort** failure policy.

    Marks all nodes that have not yet been reached as ``not_reached`` in
    *record*, sets the composition overall status to ``failed``, and raises
    :class:`CompositionAbortError` to halt execution.

    Args:
        node_id: The node that failed.
        error: The exception raised during node execution.
        record: The live composition record to update.

    Raises:
        CompositionAbortError: Always raised to signal the engine to stop.
    """
    from agentry.composition.record import CompositionStatus

    logger.info(
        "Node '%s' failed. Policy: abort. Halting composition.", node_id
    )

    # Nodes that have not yet been reached already carry NOT_REACHED
    # status from engine initialisation.  We only need to set the
    # overall composition status.
    record.overall_status = CompositionStatus.FAILED

    raise CompositionAbortError(
        f"Node '{node_id}' failed with abort policy: {error}"
    )


def handle_skip(
    node_id: str,
    error: Exception,
    run_dir: Path,
) -> NodeFailure:
    """Apply the **skip** failure policy.

    Creates a :class:`NodeFailure` object, persists it to
    ``<run_dir>/<node_id>/result.json``, and returns it so the engine can
    propagate it to downstream nodes.

    Args:
        node_id: The node that failed.
        error: The exception raised during node execution.
        run_dir: Root directory for the composition run.

    Returns:
        A :class:`NodeFailure` describing the failure.
    """
    logger.info(
        "Node '%s' failed. Policy: skip. Propagating failure object to "
        "downstream nodes.",
        node_id,
    )

    failure = NodeFailure(node_id=node_id, error=str(error))
    result_path = run_dir / node_id / "result.json"
    failure.save(result_path)

    return failure


def handle_retry(
    node_id: str,
    error: Exception,
    step: CompositionStep,
    runner_detector: Any,
    workflow_loader: Any,
    binder: Any,
    run_dir: Path,
    record: CompositionRecord,
    *,
    execute_node_fn: ExecuteNodeFn | None = None,
) -> ExecutionResult | NodeFailure:
    """Apply the **retry** failure policy.

    Re-executes the node up to ``step.failure.max_retries`` times, each
    with a fresh runner.  If all retries fail, falls through to the
    ``fallback`` policy (abort or skip).

    The caller must supply *execute_node_fn* -- a callable with the
    signature ``(step, runner_detector, binder, run_dir) -> ExecutionResult``
    that provisions a fresh runner and executes the node.  This avoids
    coupling the failure module to the engine's internal execution logic.

    Args:
        node_id: The node that failed.
        error: The exception from the initial (non-retry) attempt.
        step: The composition step (carries the ``failure`` policy config).
        runner_detector: Detector used to provision runners.
        workflow_loader: Callable to load a workflow definition.
        binder: Local environment binder.
        run_dir: Root directory for the composition run.
        record: The live composition record.
        execute_node_fn: Callable that executes a single node and returns
            an :class:`~agentry.runners.protocol.ExecutionResult`.

    Returns:
        An :class:`~agentry.runners.protocol.ExecutionResult` if a retry
        succeeds, or a :class:`NodeFailure` if all retries are exhausted
        and the fallback is ``skip``.

    Raises:
        CompositionAbortError: If all retries are exhausted and the
            fallback policy is ``abort``.
    """
    max_retries = step.failure.max_retries
    fallback = step.failure.fallback
    retry_errors: list[dict[str, str]] = [{"error": str(error)}]

    logger.info(
        "Node '%s' failed. Policy: retry (max_retries=%d, fallback=%s).",
        node_id,
        max_retries,
        fallback,
    )

    for attempt in range(1, max_retries + 1):
        logger.info(
            "Retrying node '%s' (attempt %d/%d).",
            node_id,
            attempt,
            max_retries,
        )
        try:
            if execute_node_fn is not None:
                result = execute_node_fn(
                    step, runner_detector, binder, run_dir
                )
                return result
        except Exception as retry_exc:
            retry_errors.append({"error": str(retry_exc)})
            logger.warning(
                "Node '%s' retry %d/%d failed: %s",
                node_id,
                attempt,
                max_retries,
                retry_exc,
            )

    # All retries exhausted -- fall through to fallback policy.
    logger.info(
        "Node '%s' exhausted %d retries. Falling back to '%s' policy.",
        node_id,
        max_retries,
        fallback,
    )

    if fallback == "skip":
        failure = NodeFailure(
            node_id=node_id,
            error=str(error),
            retry_attempts=retry_errors,
        )
        result_path = run_dir / node_id / "result.json"
        failure.save(result_path)

        logger.info(
            "Node '%s' failed. Policy: skip (fallback). Propagating "
            "failure object to downstream nodes.",
            node_id,
        )
        return failure

    # Default fallback is abort.
    failure = NodeFailure(
        node_id=node_id,
        error=str(error),
        retry_attempts=retry_errors,
    )
    handle_abort(node_id, error, record)
    # handle_abort always raises; this line is unreachable but satisfies
    # the type checker.
    return failure  # pragma: no cover
