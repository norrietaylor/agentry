"""Unit tests for T03.3: Partial results and output preservation.

Tests cover:
- Successful outputs preserved on abort: A->B->C. B fails with abort.
  Verify A's result.json still exists in run_dir.
- Successful outputs preserved on skip: A->B->C. B fails with skip.
  Verify A's result.json exists and C can access B's NodeFailure JSON.
- Mixed status composition record: CompositionRecord with completed, failed,
  skipped, not_reached nodes.

Uses mock runners, pytest-asyncio, and tmp_path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio  # noqa: F401 -- required for asyncio_mode

from agentry.composition.engine import CompositionEngine
from agentry.composition.record import CompositionRecord, CompositionStatus, NodeStatus
from agentry.models.execution import ExecutionRecord
from agentry.models.composition import CompositionBlock, CompositionStep, FailurePolicy
from agentry.runners.protocol import AgentConfig, ExecutionResult, RunnerContext


# ---------------------------------------------------------------------------
# Mock runner helpers (shared with test_failure_policies.py pattern)
# ---------------------------------------------------------------------------


class MockRunner:
    """Minimal runner that fails on configurable node names."""

    def __init__(
        self,
        fail_on: set[str] | None = None,
        succeed_after: int = 0,
    ) -> None:
        self.fail_on: set[str] = fail_on or set()
        self.succeed_after = succeed_after
        self._call_count: dict[str, int] = {}
        self.provision_calls: list[tuple[Any, Any]] = []
        self.execute_calls: list[tuple[RunnerContext, AgentConfig]] = []
        self.teardown_calls: list[RunnerContext] = []

    def provision(
        self,
        safety_block: Any,
        resolved_inputs: dict[str, str],
    ) -> RunnerContext:
        self.provision_calls.append((safety_block, resolved_inputs))
        return RunnerContext()

    def execute(
        self,
        runner_context: RunnerContext,
        agent_config: AgentConfig,
    ) -> ExecutionResult:
        self.execute_calls.append((runner_context, agent_config))
        node_name = agent_config.system_prompt.split()[2].rstrip(".")
        count = self._call_count.get(node_name, 0) + 1
        self._call_count[node_name] = count

        if node_name in self.fail_on:
            if self.succeed_after > 0 and count > self.succeed_after:
                pass
            else:
                raise RuntimeError(f"Simulated failure for node '{node_name}'")

        exec_record = ExecutionRecord(final_content="ok", error="")
        return ExecutionResult(execution_record=exec_record)

    def teardown(self, runner_context: RunnerContext) -> None:
        self.teardown_calls.append(runner_context)

    def check_available(self) -> Any:  # noqa: ANN401
        return MagicMock(available=True)


def _make_mock_detector(runner: MockRunner) -> MagicMock:
    detector = MagicMock()
    detector.get_runner.return_value = runner
    return detector


def _make_mock_binder() -> MagicMock:
    return MagicMock()


def _make_mock_workflow(name: str = "stub") -> MagicMock:
    workflow = MagicMock()
    workflow.identity.name = name
    workflow.identity.description = f"{name} workflow"
    workflow.model.system_prompt = None
    workflow.model.model_id = "claude-sonnet-4-20250514"
    workflow.model.max_tokens = 1024
    workflow.model.temperature = 0.2
    workflow.tools.capabilities = []
    workflow.safety = MagicMock()
    return workflow


def _make_workflow_loader() -> Any:
    def _loader(path: str) -> MagicMock:
        stem = Path(path).stem
        return _make_mock_workflow(stem)

    return _loader


def _make_engine(
    composition: CompositionBlock,
    runner: MockRunner,
    run_dir: Path,
) -> CompositionEngine:
    return CompositionEngine(
        composition=composition,
        runner_detector=_make_mock_detector(runner),
        binder=_make_mock_binder(),
        run_dir=run_dir,
        workflow_base_dir=Path("/tmp/stub_workflows"),
    )


# ---------------------------------------------------------------------------
# Test: Successful outputs preserved on abort
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSuccessfulOutputsPreservedOnAbort:
    """A->B->C. B fails with abort. A's result.json survives."""

    async def test_a_result_json_exists_after_abort(self, tmp_path: Path) -> None:
        composition = CompositionBlock(
            steps=[
                CompositionStep(name="A", workflow="A.yaml"),
                CompositionStep(
                    name="B",
                    workflow="B.yaml",
                    depends_on=["A"],
                    failure=FailurePolicy(mode="abort"),
                ),
                CompositionStep(name="C", workflow="C.yaml", depends_on=["B"]),
            ]
        )
        runner = MockRunner(fail_on={"B"})
        engine = _make_engine(composition, runner, tmp_path)

        with patch(
            "agentry.composition.engine.load_workflow_file",
            side_effect=_make_workflow_loader(),
        ):
            await engine.execute()

        assert (tmp_path / "A" / "result.json").exists()

    async def test_a_result_json_is_valid_json_after_abort(
        self, tmp_path: Path
    ) -> None:
        composition = CompositionBlock(
            steps=[
                CompositionStep(name="A", workflow="A.yaml"),
                CompositionStep(
                    name="B",
                    workflow="B.yaml",
                    depends_on=["A"],
                    failure=FailurePolicy(mode="abort"),
                ),
                CompositionStep(name="C", workflow="C.yaml", depends_on=["B"]),
            ]
        )
        runner = MockRunner(fail_on={"B"})
        engine = _make_engine(composition, runner, tmp_path)

        with patch(
            "agentry.composition.engine.load_workflow_file",
            side_effect=_make_workflow_loader(),
        ):
            await engine.execute()

        content = (tmp_path / "A" / "result.json").read_text()
        data = json.loads(content)
        assert isinstance(data, dict)

    async def test_c_result_json_does_not_exist_after_abort(
        self, tmp_path: Path
    ) -> None:
        """C was never reached so no result.json should be written for it."""
        composition = CompositionBlock(
            steps=[
                CompositionStep(name="A", workflow="A.yaml"),
                CompositionStep(
                    name="B",
                    workflow="B.yaml",
                    depends_on=["A"],
                    failure=FailurePolicy(mode="abort"),
                ),
                CompositionStep(name="C", workflow="C.yaml", depends_on=["B"]),
            ]
        )
        runner = MockRunner(fail_on={"B"})
        engine = _make_engine(composition, runner, tmp_path)

        with patch(
            "agentry.composition.engine.load_workflow_file",
            side_effect=_make_workflow_loader(),
        ):
            await engine.execute()

        assert not (tmp_path / "C" / "result.json").exists()


# ---------------------------------------------------------------------------
# Test: Successful outputs preserved on skip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSuccessfulOutputsPreservedOnSkip:
    """A->B->C. B fails with skip. A's result.json exists; B's NodeFailure JSON accessible."""

    async def test_a_result_json_exists_after_skip(self, tmp_path: Path) -> None:
        composition = CompositionBlock(
            steps=[
                CompositionStep(name="A", workflow="A.yaml"),
                CompositionStep(
                    name="B",
                    workflow="B.yaml",
                    depends_on=["A"],
                    failure=FailurePolicy(mode="skip"),
                ),
                CompositionStep(name="C", workflow="C.yaml", depends_on=["B"]),
            ]
        )
        runner = MockRunner(fail_on={"B"})
        engine = _make_engine(composition, runner, tmp_path)

        with patch(
            "agentry.composition.engine.load_workflow_file",
            side_effect=_make_workflow_loader(),
        ):
            await engine.execute()

        assert (tmp_path / "A" / "result.json").exists()

    async def test_b_result_json_contains_node_failure(
        self, tmp_path: Path
    ) -> None:
        """B's result.json exists and carries the _failure sentinel."""
        composition = CompositionBlock(
            steps=[
                CompositionStep(name="A", workflow="A.yaml"),
                CompositionStep(
                    name="B",
                    workflow="B.yaml",
                    depends_on=["A"],
                    failure=FailurePolicy(mode="skip"),
                ),
                CompositionStep(name="C", workflow="C.yaml", depends_on=["B"]),
            ]
        )
        runner = MockRunner(fail_on={"B"})
        engine = _make_engine(composition, runner, tmp_path)

        with patch(
            "agentry.composition.engine.load_workflow_file",
            side_effect=_make_workflow_loader(),
        ):
            await engine.execute()

        result_path = tmp_path / "B" / "result.json"
        assert result_path.exists()
        data = json.loads(result_path.read_text())
        assert data.get("_failure") is True

    async def test_c_executes_after_b_skip(self, tmp_path: Path) -> None:
        """C is executed despite B's failure under skip policy."""
        composition = CompositionBlock(
            steps=[
                CompositionStep(name="A", workflow="A.yaml"),
                CompositionStep(
                    name="B",
                    workflow="B.yaml",
                    depends_on=["A"],
                    failure=FailurePolicy(mode="skip"),
                ),
                CompositionStep(name="C", workflow="C.yaml", depends_on=["B"]),
            ]
        )
        runner = MockRunner(fail_on={"B"})
        engine = _make_engine(composition, runner, tmp_path)

        with patch(
            "agentry.composition.engine.load_workflow_file",
            side_effect=_make_workflow_loader(),
        ):
            record = await engine.execute()

        assert record.node_statuses["C"] == NodeStatus.COMPLETED

    async def test_c_result_json_exists_after_skip(self, tmp_path: Path) -> None:
        """C's result.json is written because C executes normally."""
        composition = CompositionBlock(
            steps=[
                CompositionStep(name="A", workflow="A.yaml"),
                CompositionStep(
                    name="B",
                    workflow="B.yaml",
                    depends_on=["A"],
                    failure=FailurePolicy(mode="skip"),
                ),
                CompositionStep(name="C", workflow="C.yaml", depends_on=["B"]),
            ]
        )
        runner = MockRunner(fail_on={"B"})
        engine = _make_engine(composition, runner, tmp_path)

        with patch(
            "agentry.composition.engine.load_workflow_file",
            side_effect=_make_workflow_loader(),
        ):
            await engine.execute()

        assert (tmp_path / "C" / "result.json").exists()


# ---------------------------------------------------------------------------
# Test: Mixed status CompositionRecord
# ---------------------------------------------------------------------------


class TestMixedStatusCompositionRecord:
    """Verify CompositionRecord with completed, failed, and not_reached nodes."""

    def test_record_stores_completed_status(self) -> None:
        record = CompositionRecord(
            node_statuses={
                "A": NodeStatus.COMPLETED,
                "B": NodeStatus.FAILED,
                "C": NodeStatus.NOT_REACHED,
            },
            node_records={"A": None, "B": None, "C": None},
            overall_status=CompositionStatus.FAILED,
            wall_clock_start=0.0,
            wall_clock_end=1.0,
        )
        assert record.node_statuses["A"] == NodeStatus.COMPLETED

    def test_record_stores_failed_status(self) -> None:
        record = CompositionRecord(
            node_statuses={
                "A": NodeStatus.COMPLETED,
                "B": NodeStatus.FAILED,
                "C": NodeStatus.NOT_REACHED,
            },
            node_records={"A": None, "B": None, "C": None},
            overall_status=CompositionStatus.FAILED,
            wall_clock_start=0.0,
            wall_clock_end=1.0,
        )
        assert record.node_statuses["B"] == NodeStatus.FAILED

    def test_record_stores_not_reached_status(self) -> None:
        record = CompositionRecord(
            node_statuses={
                "A": NodeStatus.COMPLETED,
                "B": NodeStatus.FAILED,
                "C": NodeStatus.NOT_REACHED,
            },
            node_records={"A": None, "B": None, "C": None},
            overall_status=CompositionStatus.FAILED,
            wall_clock_start=0.0,
            wall_clock_end=1.0,
        )
        assert record.node_statuses["C"] == NodeStatus.NOT_REACHED

    def test_record_serializes_all_statuses(self) -> None:
        record = CompositionRecord(
            node_statuses={
                "A": NodeStatus.COMPLETED,
                "B": NodeStatus.FAILED,
                "C": NodeStatus.NOT_REACHED,
                "D": NodeStatus.SKIPPED,
            },
            node_records={"A": None, "B": None, "C": None, "D": None},
            overall_status=CompositionStatus.PARTIAL,
            wall_clock_start=0.0,
            wall_clock_end=2.0,
        )
        data = record.to_dict()
        assert data["node_statuses"]["A"] == "completed"
        assert data["node_statuses"]["B"] == "failed"
        assert data["node_statuses"]["C"] == "not_reached"
        assert data["node_statuses"]["D"] == "skipped"

    def test_record_overall_status_partial(self) -> None:
        record = CompositionRecord(
            node_statuses={
                "A": NodeStatus.COMPLETED,
                "B": NodeStatus.FAILED,
            },
            node_records={"A": None, "B": None},
            overall_status=CompositionStatus.PARTIAL,
            wall_clock_start=0.0,
            wall_clock_end=1.0,
        )
        assert record.overall_status == CompositionStatus.PARTIAL

    def test_record_to_dict_overall_status_is_string(self) -> None:
        record = CompositionRecord(
            node_statuses={"A": NodeStatus.COMPLETED},
            node_records={"A": None},
            overall_status=CompositionStatus.COMPLETED,
            wall_clock_start=0.0,
            wall_clock_end=1.0,
        )
        data = record.to_dict()
        assert data["overall_status"] == "completed"

    def test_record_save_creates_composition_record_json(
        self, tmp_path: Path
    ) -> None:
        record = CompositionRecord(
            node_statuses={
                "A": NodeStatus.COMPLETED,
                "B": NodeStatus.FAILED,
                "C": NodeStatus.NOT_REACHED,
            },
            node_records={"A": None, "B": None, "C": None},
            overall_status=CompositionStatus.FAILED,
            wall_clock_start=0.0,
            wall_clock_end=1.0,
        )
        path = record.save(tmp_path)
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["node_statuses"]["A"] == "completed"
        assert data["node_statuses"]["B"] == "failed"
        assert data["node_statuses"]["C"] == "not_reached"
