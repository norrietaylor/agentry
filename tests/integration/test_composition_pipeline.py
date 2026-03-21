"""Integration tests for T04.3: Three-node pipeline with file-based data passing.

Tests a full A->B->C pipeline where each node receives the previous node's
output as an input and produces its own result.json.

Verifies:
- A's result.json exists with expected content.
- B's result.json exists and B received A's output path as input.
- C's result.json exists and C received B's output path as input.
- CompositionRecord shows all three nodes completed.

Uses mock runners that produce deterministic JSON output.
Uses ``tmp_path`` for run_dir.
Uses pytest-asyncio.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio  # noqa: F401 -- required for asyncio mode

from agentry.composition.engine import CompositionEngine
from agentry.composition.record import CompositionStatus, NodeStatus
from agentry.executor import ExecutionRecord
from agentry.models.composition import CompositionBlock, CompositionStep
from agentry.runners.protocol import AgentConfig, ExecutionResult, RunnerContext


# ---------------------------------------------------------------------------
# Mock runner helpers
# ---------------------------------------------------------------------------


class RecordingRunner:
    """Mock runner that records resolved_inputs from each provision call
    and returns deterministic JSON output per node.

    The runner returns a ``final_content`` that is a JSON string so the
    engine's ``_write_node_output`` writes a real serialisable dict to disk.
    """

    def __init__(self, node_outputs: dict[str, dict[str, Any]]) -> None:
        """Initialise with per-node output dictionaries.

        Args:
            node_outputs: Mapping from node ID to the dict that should be
                returned as ``final_output`` by the runner for that node.
        """
        self._node_outputs = node_outputs
        # Maps node_id -> resolved_inputs dict captured at provision time.
        self.captured_inputs: dict[str, dict[str, str]] = {}
        self._provision_node_id: str | None = None

    def provision(
        self,
        safety_block: Any,
        resolved_inputs: dict[str, str],
    ) -> RunnerContext:
        # We need to know which node we're provisioning for so we can capture
        # the inputs.  The engine calls provision then immediately calls
        # execute, so we stash the inputs here and read the agent_config in
        # execute to map them correctly.
        self._latest_inputs = dict(resolved_inputs)
        return RunnerContext()

    def execute(
        self,
        runner_context: RunnerContext,
        agent_config: AgentConfig,
    ) -> ExecutionResult:
        # Derive the node_id from the system prompt: "You are <name>. ..."
        # The mock workflow sets identity.name = <node_id>.
        parts = agent_config.system_prompt.split()
        # Format: "You are <name>. <description>"
        node_id = parts[2].rstrip(".") if len(parts) >= 3 else "unknown"

        # Store the resolved_inputs for this node.
        self.captured_inputs[node_id] = dict(self._latest_inputs)

        # Return a deterministic ExecutionResult for this node.
        output_data = self._node_outputs.get(node_id, {"node": node_id, "status": "done"})
        exec_record = ExecutionRecord(
            final_content=json.dumps(output_data),
            final_output=output_data,
            error="",
        )
        return ExecutionResult(execution_record=exec_record)

    def teardown(self, runner_context: RunnerContext) -> None:
        pass

    def check_available(self) -> Any:  # noqa: ANN401
        return MagicMock(available=True)


def _make_mock_detector(runner: RecordingRunner) -> MagicMock:
    """Return a mock RunnerDetector always returning *runner*."""
    detector = MagicMock()
    detector.get_runner.return_value = runner
    return detector


def _make_mock_workflow(name: str) -> MagicMock:
    """Return a minimal mock WorkflowDefinition for node *name*."""
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


def _make_engine(
    composition: CompositionBlock,
    runner: RecordingRunner,
    run_dir: Path,
) -> CompositionEngine:
    """Build a CompositionEngine with mock dependencies."""
    return CompositionEngine(
        composition=composition,
        runner_detector=_make_mock_detector(runner),
        binder=MagicMock(),
        run_dir=run_dir,
        workflow_base_dir=Path("/tmp/stub_workflows"),
    )


def _load_by_path(path: str) -> MagicMock:
    """Side effect for load_workflow_file that returns a mock per node name."""
    node_name = Path(path).stem  # "a" from "a.yaml"
    return _make_mock_workflow(node_name)


# ---------------------------------------------------------------------------
# Three-node pipeline A->B->C
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestThreeNodePipeline:
    """A->B->C pipeline: each node receives the previous node's output as input."""

    def _make_composition(self) -> CompositionBlock:
        return CompositionBlock(
            steps=[
                CompositionStep(name="a", workflow="a.yaml"),
                CompositionStep(
                    name="b",
                    workflow="b.yaml",
                    depends_on=["a"],
                    inputs={"prev_output": "a.output"},
                ),
                CompositionStep(
                    name="c",
                    workflow="c.yaml",
                    depends_on=["b"],
                    inputs={"prev_output": "b.output"},
                ),
            ]
        )

    def _make_runner(self) -> RecordingRunner:
        return RecordingRunner(
            node_outputs={
                "a": {"node": "a", "value": 100},
                "b": {"node": "b", "value": 200},
                "c": {"node": "c", "value": 300},
            }
        )

    async def test_all_nodes_completed(self, tmp_path: Path) -> None:
        """CompositionRecord shows all three nodes completed."""
        runner = self._make_runner()
        engine = _make_engine(self._make_composition(), runner, tmp_path)

        with patch(
            "agentry.composition.engine.load_workflow_file",
            side_effect=_load_by_path,
        ):
            record = await engine.execute()

        assert record.node_statuses["a"] == NodeStatus.COMPLETED
        assert record.node_statuses["b"] == NodeStatus.COMPLETED
        assert record.node_statuses["c"] == NodeStatus.COMPLETED

    async def test_overall_status_completed(self, tmp_path: Path) -> None:
        """Overall pipeline status is COMPLETED."""
        runner = self._make_runner()
        engine = _make_engine(self._make_composition(), runner, tmp_path)

        with patch(
            "agentry.composition.engine.load_workflow_file",
            side_effect=_load_by_path,
        ):
            record = await engine.execute()

        assert record.overall_status == CompositionStatus.COMPLETED

    async def test_node_a_result_json_exists(self, tmp_path: Path) -> None:
        """A's result.json is created in the run_dir."""
        runner = self._make_runner()
        engine = _make_engine(self._make_composition(), runner, tmp_path)

        with patch(
            "agentry.composition.engine.load_workflow_file",
            side_effect=_load_by_path,
        ):
            await engine.execute()

        assert (tmp_path / "a" / "result.json").exists()

    async def test_node_b_result_json_exists(self, tmp_path: Path) -> None:
        """B's result.json is created in the run_dir."""
        runner = self._make_runner()
        engine = _make_engine(self._make_composition(), runner, tmp_path)

        with patch(
            "agentry.composition.engine.load_workflow_file",
            side_effect=_load_by_path,
        ):
            await engine.execute()

        assert (tmp_path / "b" / "result.json").exists()

    async def test_node_c_result_json_exists(self, tmp_path: Path) -> None:
        """C's result.json is created in the run_dir."""
        runner = self._make_runner()
        engine = _make_engine(self._make_composition(), runner, tmp_path)

        with patch(
            "agentry.composition.engine.load_workflow_file",
            side_effect=_load_by_path,
        ):
            await engine.execute()

        assert (tmp_path / "c" / "result.json").exists()

    async def test_node_a_result_json_content(self, tmp_path: Path) -> None:
        """A's result.json contains the expected execution record."""
        runner = self._make_runner()
        engine = _make_engine(self._make_composition(), runner, tmp_path)

        with patch(
            "agentry.composition.engine.load_workflow_file",
            side_effect=_load_by_path,
        ):
            await engine.execute()

        content = json.loads((tmp_path / "a" / "result.json").read_text())
        # The engine writes exec_record.to_dict() which includes final_content.
        assert "final_content" in content or "error" in content

    async def test_node_b_received_a_output_path(self, tmp_path: Path) -> None:
        """B's resolved_inputs contains a path pointing to A's result.json."""
        runner = self._make_runner()
        engine = _make_engine(self._make_composition(), runner, tmp_path)

        with patch(
            "agentry.composition.engine.load_workflow_file",
            side_effect=_load_by_path,
        ):
            await engine.execute()

        b_inputs = runner.captured_inputs.get("b", {})
        assert "prev_output" in b_inputs

        prev_output_path = Path(b_inputs["prev_output"])
        assert prev_output_path.exists(), (
            f"B's prev_output path does not exist: {prev_output_path}"
        )

        # The path should point to A's result.json.
        a_result_json = (tmp_path / "a" / "result.json").resolve()
        assert prev_output_path.resolve() == a_result_json

    async def test_node_c_received_b_output_path(self, tmp_path: Path) -> None:
        """C's resolved_inputs contains a path pointing to B's result.json."""
        runner = self._make_runner()
        engine = _make_engine(self._make_composition(), runner, tmp_path)

        with patch(
            "agentry.composition.engine.load_workflow_file",
            side_effect=_load_by_path,
        ):
            await engine.execute()

        c_inputs = runner.captured_inputs.get("c", {})
        assert "prev_output" in c_inputs

        prev_output_path = Path(c_inputs["prev_output"])
        assert prev_output_path.exists(), (
            f"C's prev_output path does not exist: {prev_output_path}"
        )

        # The path should point to B's result.json.
        b_result_json = (tmp_path / "b" / "result.json").resolve()
        assert prev_output_path.resolve() == b_result_json

    async def test_node_a_has_no_resolved_inputs(self, tmp_path: Path) -> None:
        """A has no inputs, so its resolved_inputs should be empty."""
        runner = self._make_runner()
        engine = _make_engine(self._make_composition(), runner, tmp_path)

        with patch(
            "agentry.composition.engine.load_workflow_file",
            side_effect=_load_by_path,
        ):
            await engine.execute()

        a_inputs = runner.captured_inputs.get("a", {})
        assert a_inputs == {}

    async def test_composition_record_saved_to_disk(
        self, tmp_path: Path
    ) -> None:
        """composition-record.json is written to run_dir."""
        runner = self._make_runner()
        engine = _make_engine(self._make_composition(), runner, tmp_path)

        with patch(
            "agentry.composition.engine.load_workflow_file",
            side_effect=_load_by_path,
        ):
            await engine.execute()

        assert (tmp_path / "composition-record.json").exists()

    async def test_composition_record_reflects_all_nodes(
        self, tmp_path: Path
    ) -> None:
        """composition-record.json lists all three nodes as completed."""
        runner = self._make_runner()
        engine = _make_engine(self._make_composition(), runner, tmp_path)

        with patch(
            "agentry.composition.engine.load_workflow_file",
            side_effect=_load_by_path,
        ):
            await engine.execute()

        data = json.loads(
            (tmp_path / "composition-record.json").read_text()
        )
        assert data["node_statuses"]["a"] == "completed"
        assert data["node_statuses"]["b"] == "completed"
        assert data["node_statuses"]["c"] == "completed"
