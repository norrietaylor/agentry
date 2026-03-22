"""Composition execution record and status enums.

Tracks the outcomes of all nodes in a composed workflow execution,
providing per-node status, per-node execution records, an overall
status, and wall-clock timing.

Usage::

    from agentry.composition.record import (
        CompositionRecord,
        CompositionStatus,
        NodeStatus,
    )

    record = CompositionRecord(
        node_statuses={"step-a": NodeStatus.COMPLETED, "step-b": NodeStatus.FAILED},
        node_records={"step-a": exec_record, "step-b": None},
        overall_status=CompositionStatus.PARTIAL,
        wall_clock_start=start,
        wall_clock_end=end,
    )
    record.save(run_dir)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentry.models.execution import ExecutionRecord


class NodeStatus(str, Enum):
    """Status of a single node in a composed workflow.

    Values are lowercase strings so they serialise cleanly to JSON.
    """

    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    NOT_REACHED = "not_reached"


class CompositionStatus(str, Enum):
    """Overall status of a composed workflow run.

    Attributes:
        COMPLETED: All nodes finished successfully.
        FAILED: One or more nodes failed and the failure was fatal.
        PARTIAL: Some nodes completed and some were skipped or not reached
            due to a non-fatal failure policy.
    """

    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


@dataclass
class CompositionRecord:
    """Complete record of a composed workflow execution.

    Attributes:
        node_statuses: Per-node status map keyed by node ID.
        node_records: Per-node :class:`~agentry.executor.ExecutionRecord`
            (or ``None`` if the node was not executed).
        overall_status: Aggregate status for the whole composition.
        wall_clock_start: Start timestamp in seconds since epoch.
        wall_clock_end: End timestamp in seconds since epoch.
    """

    node_statuses: dict[str, NodeStatus] = field(default_factory=dict)
    node_records: dict[str, ExecutionRecord | None] = field(default_factory=dict)
    overall_status: CompositionStatus = CompositionStatus.COMPLETED
    wall_clock_start: float = 0.0
    wall_clock_end: float = 0.0

    @property
    def wall_clock_seconds(self) -> float:
        """Total wall-clock duration in seconds."""
        return self.wall_clock_end - self.wall_clock_start

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary.

        Returns:
            A plain dict suitable for ``json.dumps``.
        """
        node_records_serialized: dict[str, Any] = {}
        for node_id, exec_record in self.node_records.items():
            if exec_record is None:
                node_records_serialized[node_id] = None
            else:
                node_records_serialized[node_id] = exec_record.to_dict()

        return {
            "overall_status": self.overall_status.value,
            "wall_clock_timing": {
                "start": self.wall_clock_start,
                "end": self.wall_clock_end,
                "duration_seconds": self.wall_clock_seconds,
            },
            "node_statuses": {k: v.value for k, v in self.node_statuses.items()},
            "node_records": node_records_serialized,
        }

    def save(self, run_dir: Path) -> Path:
        """Write the composition record to ``<run_dir>/composition-record.json``.

        Args:
            run_dir: Directory to write into.  It will be created if it does
                not already exist.

        Returns:
            The path of the written file.
        """
        run_dir.mkdir(parents=True, exist_ok=True)
        output_path = run_dir / "composition-record.json"
        output_path.write_text(json.dumps(self.to_dict(), indent=2))
        return output_path


def make_composition_record(
    node_statuses: dict[str, NodeStatus] | None = None,
    node_records: dict[str, ExecutionRecord | None] | None = None,
    overall_status: CompositionStatus = CompositionStatus.COMPLETED,
    wall_clock_start: float | None = None,
    wall_clock_end: float | None = None,
) -> CompositionRecord:
    """Convenience factory for :class:`CompositionRecord`.

    Fills ``wall_clock_start`` with ``time.time()`` when not provided, and
    ``wall_clock_end`` with ``0.0`` (caller should set it upon completion).

    Args:
        node_statuses: Initial per-node status map.
        node_records: Initial per-node execution records.
        overall_status: Aggregate status.
        wall_clock_start: Override for the start timestamp.
        wall_clock_end: Override for the end timestamp.

    Returns:
        A new :class:`CompositionRecord` instance.
    """
    return CompositionRecord(
        node_statuses=node_statuses or {},
        node_records=node_records or {},
        overall_status=overall_status,
        wall_clock_start=wall_clock_start if wall_clock_start is not None else time.time(),
        wall_clock_end=wall_clock_end if wall_clock_end is not None else 0.0,
    )
