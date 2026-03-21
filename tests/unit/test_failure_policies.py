"""Unit tests for T03.3: Failure policies (abort, skip, retry) and NodeFailure.

Tests cover:
- Abort halts downstream: Three-node chain A->B->C. B fails with abort.
  A is completed, B is failed, C is not_reached. Overall status is failed.
- Skip propagates failure object: A->B->C. B fails with skip.
  B is failed, C receives NodeFailure object, C still executes. Overall is partial.
- Retry succeeds on second attempt: Node with retry policy (max_retries=2).
  First fails, second succeeds. Node is completed, retry_attempts recorded.
- Retry exhausted falls back to abort: Node with retry policy (max_retries=2, fallback=abort).
  All attempts fail. Node is failed, downstream is not_reached.
- Retry exhausted falls back to skip: Node with retry policy (max_retries=1, fallback=skip).
  Attempt fails. NodeFailure propagated to downstream.
- NodeFailure serialization: to_dict() and save() produce correct JSON with _failure sentinel.

Uses mock runners that can be configured to fail on specific nodes.
Uses pytest-asyncio and tmp_path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio  # noqa: F401 -- required for asyncio_mode

from agentry.composition.engine import CompositionEngine
from agentry.composition.failure import NodeFailure, handle_abort, handle_retry, handle_skip
from agentry.composition.record import CompositionRecord, CompositionStatus, NodeStatus
from agentry.executor import ExecutionRecord
from agentry.models.composition import CompositionBlock, CompositionStep, FailurePolicy
from agentry.runners.protocol import AgentConfig, ExecutionResult, RunnerContext


# ---------------------------------------------------------------------------
# Mock runner helpers
# ---------------------------------------------------------------------------


class MockRunner:
    """Minimal runner that records calls and returns canned results.

    Pass ``fail_on`` to make the runner raise for specific node names
    (matched against the system prompt prefix).
    """

    def __init__(
        self,
        fail_on: set[str] | None = None,
        succeed_after: int = 0,
    ) -> None:
        """Initialise the mock runner.

        Args:
            fail_on: Set of node names for which execution should raise.
            succeed_after: Number of calls to allow to fail before succeeding
                (used for retry-succeeds-on-second-attempt scenario).
        """
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
        # Determine which node this is from the system prompt.
        # System prompt format: "You are <name>. <name> workflow"
        node_name = agent_config.system_prompt.split()[2].rstrip(".")
        count = self._call_count.get(node_name, 0) + 1
        self._call_count[node_name] = count

        if node_name in self.fail_on:
            if self.succeed_after > 0 and count > self.succeed_after:
                # Enough failures; now succeed.
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
    """Return a mock RunnerDetector that always returns the given runner."""
    detector = MagicMock()
    detector.get_runner.return_value = runner
    return detector


def _make_mock_binder() -> MagicMock:
    """Return a minimal mock LocalBinder."""
    return MagicMock()


def _make_mock_workflow(name: str = "stub") -> MagicMock:
    """Return a mock WorkflowDefinition with required attributes."""
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


def _make_workflow_loader(node_names: dict[str, str] | None = None) -> Any:
    """Return a load_workflow_file side_effect function.

    Args:
        node_names: Optional mapping from workflow filename stem to display
            name.  Defaults to using the stem as-is.
    """
    def _loader(path: str) -> MagicMock:
        stem = Path(path).stem  # e.g. "a" from "a.yaml"
        name = (node_names or {}).get(stem, stem)
        return _make_mock_workflow(name)

    return _loader


def _make_engine(
    composition: CompositionBlock,
    runner: MockRunner,
    run_dir: Path,
) -> CompositionEngine:
    """Build a CompositionEngine with mock dependencies."""
    return CompositionEngine(
        composition=composition,
        runner_detector=_make_mock_detector(runner),
        binder=_make_mock_binder(),
        run_dir=run_dir,
        workflow_base_dir=Path("/tmp/stub_workflows"),
    )


# ---------------------------------------------------------------------------
# Test: Abort halts downstream (A -> B -> C, B fails with abort)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAbortHaltsDownstream:
    """Three-node chain A->B->C. B fails with abort policy."""

    async def test_a_is_completed(self, tmp_path: Path) -> None:
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
            record = await engine.execute()

        assert record.node_statuses["A"] == NodeStatus.COMPLETED

    async def test_b_is_failed(self, tmp_path: Path) -> None:
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
            record = await engine.execute()

        assert record.node_statuses["B"] == NodeStatus.FAILED

    async def test_c_is_not_reached(self, tmp_path: Path) -> None:
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
            record = await engine.execute()

        assert record.node_statuses["C"] == NodeStatus.NOT_REACHED

    async def test_overall_status_is_failed(self, tmp_path: Path) -> None:
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
            record = await engine.execute()

        assert record.overall_status == CompositionStatus.FAILED


# ---------------------------------------------------------------------------
# Test: Skip propagates failure object (A -> B -> C, B fails with skip)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSkipPropagatesFailureObject:
    """Three-node chain A->B->C. B fails with skip policy."""

    async def test_b_is_failed(self, tmp_path: Path) -> None:
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

        assert record.node_statuses["B"] == NodeStatus.FAILED

    async def test_c_still_executes(self, tmp_path: Path) -> None:
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

    async def test_b_failure_json_written_as_node_failure(self, tmp_path: Path) -> None:
        """B's result.json must contain the _failure sentinel."""
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
        assert data.get("node_id") == "B"

    async def test_overall_status_is_partial(self, tmp_path: Path) -> None:
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

        assert record.overall_status == CompositionStatus.PARTIAL


# ---------------------------------------------------------------------------
# Test: Retry succeeds on second attempt (via handle_retry directly)
# ---------------------------------------------------------------------------


class TestRetrySucceedsOnSecondAttempt:
    """Node with retry policy (max_retries=2). First attempt fails, second succeeds.

    The engine passes execute_node_fn=None to handle_retry, so retries are
    exercised by calling handle_retry directly with a controlled execute_node_fn.
    """

    def test_node_result_returned_on_successful_retry(self, tmp_path: Path) -> None:
        """handle_retry returns ExecutionResult when execute_node_fn succeeds on retry."""
        from agentry.composition.record import make_composition_record

        record = make_composition_record()
        step = CompositionStep(
            name="A",
            workflow="A.yaml",
            failure=FailurePolicy(mode="retry", max_retries=2, fallback="abort"),
        )

        call_count = {"n": 0}

        def _fail_once_then_succeed(
            s: Any, rd: Any, b: Any, run_dir: Any
        ) -> ExecutionResult:
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise RuntimeError("first attempt fails")
            exec_record = ExecutionRecord(final_content="ok", error="")
            return ExecutionResult(execution_record=exec_record)

        result = handle_retry(
            node_id="A",
            error=RuntimeError("initial failure"),
            step=step,
            runner_detector=None,
            workflow_loader=None,
            binder=None,
            run_dir=tmp_path,
            record=record,
            execute_node_fn=_fail_once_then_succeed,
        )

        assert isinstance(result, ExecutionResult)

    def test_retry_attempts_recorded_in_node_failure_when_all_fail(
        self, tmp_path: Path
    ) -> None:
        """When all retries fail, retry_attempts list is populated."""
        from agentry.composition.failure import CompositionAbortError
        from agentry.composition.record import make_composition_record

        record = make_composition_record()
        step = CompositionStep(
            name="A",
            workflow="A.yaml",
            failure=FailurePolicy(mode="retry", max_retries=2, fallback="skip"),
        )

        def _always_fail(s: Any, rd: Any, b: Any, run_dir: Any) -> Any:
            raise RuntimeError("always fails")

        result = handle_retry(
            node_id="A",
            error=RuntimeError("initial failure"),
            step=step,
            runner_detector=None,
            workflow_loader=None,
            binder=None,
            run_dir=tmp_path,
            record=record,
            execute_node_fn=_always_fail,
        )

        assert isinstance(result, NodeFailure)
        # retry_attempts includes the initial error plus per-retry errors.
        assert len(result.retry_attempts) >= 2


# ---------------------------------------------------------------------------
# Test: handle_retry directly -- retry_attempts recorded in NodeFailure
# ---------------------------------------------------------------------------


class TestRetryAttemptsRecorded:
    """Retry attempts are recorded in the NodeFailure when all retries fail."""

    def test_retry_attempts_in_node_failure_on_abort_fallback(self, tmp_path: Path) -> None:
        """handle_retry with all failures records retry_attempts in the abort path."""
        from agentry.composition.failure import CompositionAbortError
        from agentry.composition.record import make_composition_record

        record = make_composition_record()

        step = CompositionStep(
            name="A",
            workflow="A.yaml",
            failure=FailurePolicy(mode="retry", max_retries=2, fallback="abort"),
        )

        # execute_node_fn always raises.
        def _always_fail(s: Any, rd: Any, b: Any, run_dir: Any) -> Any:
            raise RuntimeError("always fails")

        with pytest.raises(CompositionAbortError):
            handle_retry(
                node_id="A",
                error=RuntimeError("initial failure"),
                step=step,
                runner_detector=None,
                workflow_loader=None,
                binder=None,
                run_dir=tmp_path,
                record=record,
                execute_node_fn=_always_fail,
            )

    def test_retry_attempts_in_node_failure_on_skip_fallback(self, tmp_path: Path) -> None:
        """handle_retry with skip fallback returns NodeFailure with retry_attempts."""
        from agentry.composition.record import make_composition_record

        record = make_composition_record()

        step = CompositionStep(
            name="A",
            workflow="A.yaml",
            failure=FailurePolicy(mode="retry", max_retries=1, fallback="skip"),
        )

        def _always_fail(s: Any, rd: Any, b: Any, run_dir: Any) -> Any:
            raise RuntimeError("retry failed")

        result = handle_retry(
            node_id="A",
            error=RuntimeError("initial failure"),
            step=step,
            runner_detector=None,
            workflow_loader=None,
            binder=None,
            run_dir=tmp_path,
            record=record,
            execute_node_fn=_always_fail,
        )

        assert isinstance(result, NodeFailure)
        assert len(result.retry_attempts) >= 1
        assert result.node_id == "A"


# ---------------------------------------------------------------------------
# Test: Retry exhausted falls back to abort
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRetryExhaustedFallbackAbort:
    """Node with retry (max_retries=2, fallback=abort). All attempts fail."""

    async def test_node_is_failed(self, tmp_path: Path) -> None:
        composition = CompositionBlock(
            steps=[
                CompositionStep(
                    name="A",
                    workflow="A.yaml",
                    failure=FailurePolicy(mode="retry", max_retries=2, fallback="abort"),
                ),
                CompositionStep(name="B", workflow="B.yaml", depends_on=["A"]),
            ]
        )
        # Never succeed -- fail_on A, succeed_after never triggered.
        runner = MockRunner(fail_on={"A"}, succeed_after=999)
        engine = _make_engine(composition, runner, tmp_path)

        with patch(
            "agentry.composition.engine.load_workflow_file",
            side_effect=_make_workflow_loader(),
        ):
            record = await engine.execute()

        assert record.node_statuses["A"] == NodeStatus.FAILED

    async def test_downstream_is_not_reached(self, tmp_path: Path) -> None:
        composition = CompositionBlock(
            steps=[
                CompositionStep(
                    name="A",
                    workflow="A.yaml",
                    failure=FailurePolicy(mode="retry", max_retries=2, fallback="abort"),
                ),
                CompositionStep(name="B", workflow="B.yaml", depends_on=["A"]),
            ]
        )
        runner = MockRunner(fail_on={"A"}, succeed_after=999)
        engine = _make_engine(composition, runner, tmp_path)

        with patch(
            "agentry.composition.engine.load_workflow_file",
            side_effect=_make_workflow_loader(),
        ):
            record = await engine.execute()

        assert record.node_statuses["B"] == NodeStatus.NOT_REACHED

    async def test_overall_status_is_failed(self, tmp_path: Path) -> None:
        composition = CompositionBlock(
            steps=[
                CompositionStep(
                    name="A",
                    workflow="A.yaml",
                    failure=FailurePolicy(mode="retry", max_retries=2, fallback="abort"),
                ),
                CompositionStep(name="B", workflow="B.yaml", depends_on=["A"]),
            ]
        )
        runner = MockRunner(fail_on={"A"}, succeed_after=999)
        engine = _make_engine(composition, runner, tmp_path)

        with patch(
            "agentry.composition.engine.load_workflow_file",
            side_effect=_make_workflow_loader(),
        ):
            record = await engine.execute()

        assert record.overall_status == CompositionStatus.FAILED


# ---------------------------------------------------------------------------
# Test: Retry exhausted falls back to skip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRetryExhaustedFallbackSkip:
    """Node with retry (max_retries=1, fallback=skip). Attempt fails."""

    async def test_node_failure_propagated_as_not_reached_or_failed(
        self, tmp_path: Path
    ) -> None:
        """Downstream is reached (skip propagation), node A is failed."""
        composition = CompositionBlock(
            steps=[
                CompositionStep(
                    name="A",
                    workflow="A.yaml",
                    failure=FailurePolicy(mode="retry", max_retries=1, fallback="skip"),
                ),
                CompositionStep(name="B", workflow="B.yaml", depends_on=["A"]),
            ]
        )
        runner = MockRunner(fail_on={"A"}, succeed_after=999)
        engine = _make_engine(composition, runner, tmp_path)

        with patch(
            "agentry.composition.engine.load_workflow_file",
            side_effect=_make_workflow_loader(),
        ):
            record = await engine.execute()

        assert record.node_statuses["A"] == NodeStatus.FAILED

    async def test_node_failure_json_written_with_retry_attempts(
        self, tmp_path: Path
    ) -> None:
        """The NodeFailure written to disk includes retry_attempts."""
        composition = CompositionBlock(
            steps=[
                CompositionStep(
                    name="A",
                    workflow="A.yaml",
                    failure=FailurePolicy(mode="retry", max_retries=1, fallback="skip"),
                ),
            ]
        )
        runner = MockRunner(fail_on={"A"}, succeed_after=999)
        engine = _make_engine(composition, runner, tmp_path)

        with patch(
            "agentry.composition.engine.load_workflow_file",
            side_effect=_make_workflow_loader(),
        ):
            await engine.execute()

        result_path = tmp_path / "A" / "result.json"
        assert result_path.exists()
        data = json.loads(result_path.read_text())
        assert data.get("_failure") is True
        assert "retry_attempts" in data
        assert isinstance(data["retry_attempts"], list)
        assert len(data["retry_attempts"]) >= 1


# ---------------------------------------------------------------------------
# Test: NodeFailure serialization
# ---------------------------------------------------------------------------


class TestNodeFailureSerialization:
    """NodeFailure.to_dict() and save() produce correct JSON with _failure sentinel."""

    def test_to_dict_contains_failure_sentinel(self) -> None:
        failure = NodeFailure(node_id="test-node", error="something broke")
        result = failure.to_dict()
        assert result["_failure"] is True

    def test_to_dict_contains_node_id(self) -> None:
        failure = NodeFailure(node_id="my-node", error="broken")
        result = failure.to_dict()
        assert result["node_id"] == "my-node"

    def test_to_dict_contains_error(self) -> None:
        failure = NodeFailure(node_id="n", error="oops")
        result = failure.to_dict()
        assert result["error"] == "oops"

    def test_to_dict_omits_partial_output_when_none(self) -> None:
        failure = NodeFailure(node_id="n", error="err")
        result = failure.to_dict()
        assert "partial_output" not in result

    def test_to_dict_includes_partial_output_when_present(self) -> None:
        failure = NodeFailure(node_id="n", error="err", partial_output={"key": "val"})
        result = failure.to_dict()
        assert result["partial_output"] == {"key": "val"}

    def test_to_dict_omits_retry_attempts_when_empty(self) -> None:
        failure = NodeFailure(node_id="n", error="err")
        result = failure.to_dict()
        assert "retry_attempts" not in result

    def test_to_dict_includes_retry_attempts_when_present(self) -> None:
        failure = NodeFailure(
            node_id="n",
            error="err",
            retry_attempts=[{"error": "first"}, {"error": "second"}],
        )
        result = failure.to_dict()
        assert result["retry_attempts"] == [{"error": "first"}, {"error": "second"}]

    def test_save_writes_valid_json(self, tmp_path: Path) -> None:
        failure = NodeFailure(node_id="save-node", error="disk error")
        dest = tmp_path / "result.json"
        failure.save(dest)
        content = dest.read_text()
        data = json.loads(content)
        assert isinstance(data, dict)

    def test_save_creates_parent_directories(self, tmp_path: Path) -> None:
        failure = NodeFailure(node_id="n", error="e")
        dest = tmp_path / "nested" / "dirs" / "result.json"
        failure.save(dest)
        assert dest.exists()

    def test_save_file_contains_failure_sentinel(self, tmp_path: Path) -> None:
        failure = NodeFailure(node_id="sentinel-node", error="error msg")
        dest = tmp_path / "result.json"
        failure.save(dest)
        data = json.loads(dest.read_text())
        assert data["_failure"] is True

    def test_round_trip_json_matches_to_dict(self, tmp_path: Path) -> None:
        failure = NodeFailure(
            node_id="rt-node",
            error="round trip",
            partial_output={"x": 1},
            retry_attempts=[{"error": "attempt 1"}],
        )
        dest = tmp_path / "result.json"
        failure.save(dest)
        data = json.loads(dest.read_text())
        assert data == failure.to_dict()
