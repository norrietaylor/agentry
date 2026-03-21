"""Unit tests for T02.3: CompositionRecord dataclass.

Tests cover:
- to_dict() serialization: all fields present with correct types.
- save() writes JSON to composition-record.json and is loadable.
- wall_clock_seconds property: correct calculation.
- Per-node status map: verify statuses for mixed completed/failed/skipped/not_reached.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from agentry.composition.record import (
    CompositionRecord,
    CompositionStatus,
    NodeStatus,
    make_composition_record,
)
from agentry.executor import ExecutionRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_exec_record(error: str = "") -> ExecutionRecord:
    """Return a minimal ExecutionRecord for testing."""
    return ExecutionRecord(
        final_content="done",
        wall_clock_start=1000.0,
        wall_clock_end=1005.0,
        error=error,
    )


# ---------------------------------------------------------------------------
# to_dict() serialization
# ---------------------------------------------------------------------------


class TestCompositionRecordToDict:
    """CompositionRecord.to_dict() produces JSON-compatible dictionaries."""

    def test_to_dict_contains_overall_status(self) -> None:
        record = CompositionRecord(
            overall_status=CompositionStatus.COMPLETED,
        )
        d = record.to_dict()
        assert d["overall_status"] == "completed"

    def test_to_dict_overall_status_failed(self) -> None:
        record = CompositionRecord(
            overall_status=CompositionStatus.FAILED,
        )
        d = record.to_dict()
        assert d["overall_status"] == "failed"

    def test_to_dict_overall_status_partial(self) -> None:
        record = CompositionRecord(
            overall_status=CompositionStatus.PARTIAL,
        )
        d = record.to_dict()
        assert d["overall_status"] == "partial"

    def test_to_dict_contains_wall_clock_timing(self) -> None:
        record = CompositionRecord(
            wall_clock_start=1000.0,
            wall_clock_end=1010.0,
        )
        d = record.to_dict()
        timing = d["wall_clock_timing"]
        assert timing["start"] == 1000.0
        assert timing["end"] == 1010.0
        assert timing["duration_seconds"] == pytest.approx(10.0)

    def test_to_dict_contains_node_statuses_as_strings(self) -> None:
        record = CompositionRecord(
            node_statuses={
                "step-a": NodeStatus.COMPLETED,
                "step-b": NodeStatus.FAILED,
            },
        )
        d = record.to_dict()
        assert d["node_statuses"]["step-a"] == "completed"
        assert d["node_statuses"]["step-b"] == "failed"

    def test_to_dict_all_node_status_values(self) -> None:
        record = CompositionRecord(
            node_statuses={
                "a": NodeStatus.COMPLETED,
                "b": NodeStatus.FAILED,
                "c": NodeStatus.SKIPPED,
                "d": NodeStatus.NOT_REACHED,
            },
        )
        d = record.to_dict()
        assert d["node_statuses"]["a"] == "completed"
        assert d["node_statuses"]["b"] == "failed"
        assert d["node_statuses"]["c"] == "skipped"
        assert d["node_statuses"]["d"] == "not_reached"

    def test_to_dict_contains_node_records(self) -> None:
        exec_record = _make_exec_record()
        record = CompositionRecord(
            node_statuses={"step-a": NodeStatus.COMPLETED},
            node_records={"step-a": exec_record},
        )
        d = record.to_dict()
        assert "step-a" in d["node_records"]
        # ExecutionRecord serializes via its own to_dict()
        assert isinstance(d["node_records"]["step-a"], dict)

    def test_to_dict_none_node_record_serializes_as_none(self) -> None:
        record = CompositionRecord(
            node_statuses={"step-a": NodeStatus.NOT_REACHED},
            node_records={"step-a": None},
        )
        d = record.to_dict()
        assert d["node_records"]["step-a"] is None

    def test_to_dict_is_json_serializable(self) -> None:
        record = CompositionRecord(
            node_statuses={
                "a": NodeStatus.COMPLETED,
                "b": NodeStatus.FAILED,
            },
            node_records={"a": _make_exec_record(), "b": None},
            overall_status=CompositionStatus.PARTIAL,
            wall_clock_start=1000.0,
            wall_clock_end=1010.0,
        )
        d = record.to_dict()
        # Should not raise
        json_str = json.dumps(d)
        assert len(json_str) > 0

    def test_to_dict_has_all_required_keys(self) -> None:
        record = CompositionRecord()
        d = record.to_dict()
        assert set(d.keys()) == {
            "overall_status",
            "wall_clock_timing",
            "node_statuses",
            "node_records",
        }


# ---------------------------------------------------------------------------
# save() writes JSON
# ---------------------------------------------------------------------------


class TestCompositionRecordSave:
    """CompositionRecord.save() writes a valid composition-record.json."""

    def test_save_creates_file(self, tmp_path: Path) -> None:
        record = CompositionRecord(
            overall_status=CompositionStatus.COMPLETED,
        )
        output_path = record.save(tmp_path)
        assert output_path == tmp_path / "composition-record.json"
        assert output_path.exists()

    def test_save_file_is_valid_json(self, tmp_path: Path) -> None:
        record = CompositionRecord(
            node_statuses={"step-a": NodeStatus.COMPLETED},
            overall_status=CompositionStatus.COMPLETED,
            wall_clock_start=1000.0,
            wall_clock_end=1005.0,
        )
        output_path = record.save(tmp_path)
        content = output_path.read_text()
        # Should not raise
        data = json.loads(content)
        assert isinstance(data, dict)

    def test_save_file_content_matches_to_dict(self, tmp_path: Path) -> None:
        record = CompositionRecord(
            node_statuses={"step-a": NodeStatus.COMPLETED},
            overall_status=CompositionStatus.COMPLETED,
            wall_clock_start=1000.0,
            wall_clock_end=1005.0,
        )
        output_path = record.save(tmp_path)
        data = json.loads(output_path.read_text())
        assert data == record.to_dict()

    def test_save_creates_run_dir_if_missing(self, tmp_path: Path) -> None:
        new_dir = tmp_path / "new_run_dir" / "nested"
        record = CompositionRecord()
        record.save(new_dir)
        assert (new_dir / "composition-record.json").exists()

    def test_save_overwrites_existing_file(self, tmp_path: Path) -> None:
        record1 = CompositionRecord(overall_status=CompositionStatus.FAILED)
        record1.save(tmp_path)

        record2 = CompositionRecord(overall_status=CompositionStatus.COMPLETED)
        record2.save(tmp_path)

        data = json.loads((tmp_path / "composition-record.json").read_text())
        assert data["overall_status"] == "completed"


# ---------------------------------------------------------------------------
# wall_clock_seconds property
# ---------------------------------------------------------------------------


class TestWallClockSecondsProperty:
    """CompositionRecord.wall_clock_seconds computes duration correctly."""

    def test_wall_clock_seconds_basic(self) -> None:
        record = CompositionRecord(
            wall_clock_start=1000.0,
            wall_clock_end=1010.0,
        )
        assert record.wall_clock_seconds == pytest.approx(10.0)

    def test_wall_clock_seconds_subsecond(self) -> None:
        record = CompositionRecord(
            wall_clock_start=1000.0,
            wall_clock_end=1000.5,
        )
        assert record.wall_clock_seconds == pytest.approx(0.5)

    def test_wall_clock_seconds_zero_when_equal(self) -> None:
        record = CompositionRecord(
            wall_clock_start=1000.0,
            wall_clock_end=1000.0,
        )
        assert record.wall_clock_seconds == pytest.approx(0.0)

    def test_wall_clock_seconds_default_zero(self) -> None:
        record = CompositionRecord()
        assert record.wall_clock_seconds == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Per-node status map
# ---------------------------------------------------------------------------


class TestPerNodeStatusMap:
    """Verify per-node status map with mixed statuses."""

    def test_all_completed(self) -> None:
        record = CompositionRecord(
            node_statuses={
                "a": NodeStatus.COMPLETED,
                "b": NodeStatus.COMPLETED,
                "c": NodeStatus.COMPLETED,
            },
        )
        assert all(s == NodeStatus.COMPLETED for s in record.node_statuses.values())

    def test_mixed_statuses(self) -> None:
        record = CompositionRecord(
            node_statuses={
                "a": NodeStatus.COMPLETED,
                "b": NodeStatus.FAILED,
                "c": NodeStatus.SKIPPED,
                "d": NodeStatus.NOT_REACHED,
            },
        )
        assert record.node_statuses["a"] == NodeStatus.COMPLETED
        assert record.node_statuses["b"] == NodeStatus.FAILED
        assert record.node_statuses["c"] == NodeStatus.SKIPPED
        assert record.node_statuses["d"] == NodeStatus.NOT_REACHED

    def test_status_map_empty_by_default(self) -> None:
        record = CompositionRecord()
        assert record.node_statuses == {}

    def test_status_map_serializes_to_string_values(self) -> None:
        record = CompositionRecord(
            node_statuses={
                "a": NodeStatus.COMPLETED,
                "b": NodeStatus.FAILED,
                "c": NodeStatus.SKIPPED,
                "d": NodeStatus.NOT_REACHED,
            },
        )
        d = record.to_dict()
        assert d["node_statuses"] == {
            "a": "completed",
            "b": "failed",
            "c": "skipped",
            "d": "not_reached",
        }


# ---------------------------------------------------------------------------
# make_composition_record factory
# ---------------------------------------------------------------------------


class TestMakeCompositionRecord:
    """make_composition_record() convenience factory."""

    def test_factory_sets_wall_clock_start(self) -> None:
        before = time.time()
        record = make_composition_record()
        after = time.time()
        assert before <= record.wall_clock_start <= after

    def test_factory_sets_wall_clock_end_to_zero(self) -> None:
        record = make_composition_record()
        assert record.wall_clock_end == 0.0

    def test_factory_accepts_override_start(self) -> None:
        record = make_composition_record(wall_clock_start=5000.0)
        assert record.wall_clock_start == 5000.0

    def test_factory_accepts_node_statuses(self) -> None:
        statuses = {"a": NodeStatus.COMPLETED}
        record = make_composition_record(node_statuses=statuses)
        assert record.node_statuses == statuses

    def test_factory_defaults_overall_status_completed(self) -> None:
        record = make_composition_record()
        assert record.overall_status == CompositionStatus.COMPLETED
