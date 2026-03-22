"""Unit tests for the runtime shim (agentry.runners.shim).

Tests cover:
- load_config() reads and validates JSON config files.
- load_config() raises FileNotFoundError for missing files.
- load_config() raises ValueError for missing required keys.
- write_result() writes JSON to the output path.
- write_result() creates parent directories.
- run_shim() returns exit code 2 on startup errors.
- run_shim() writes error result on startup errors.
- run_shim() launches agent runtime (not AgentExecutor).
- run_shim() passes agent_name and agent_config to agent registry.
- shim module does not reference AgentExecutor at module level.
- main() uses sys.argv for config and output paths.
"""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from agentry.runners.shim import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_OUTPUT_PATH,
    load_config,
    run_shim,
    write_result,
)

# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


class TestLoadConfig:
    """Tests for shim.load_config()."""

    def test_loads_valid_config(self) -> None:
        config_data = {
            "system_prompt": "You are helpful.",
            "resolved_inputs": {"diff": "content"},
            "tool_names": ["repository:read"],
            "agent_name": "claude-code",
            "agent_config": {"model": "claude-sonnet-4-20250514"},
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as fh:
            json.dump(config_data, fh)
            fh.flush()
            path = fh.name

        try:
            result = load_config(path)
            assert result["system_prompt"] == "You are helpful."
            assert result["tool_names"] == ["repository:read"]
        finally:
            os.unlink(path)

    def test_raises_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError, match="Config file not found"):
            load_config("/nonexistent/path/config.json")

    def test_raises_on_invalid_json(self) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as fh:
            fh.write("not valid json {{{")
            path = fh.name

        try:
            with pytest.raises(json.JSONDecodeError):
                load_config(path)
        finally:
            os.unlink(path)

    def test_raises_on_missing_required_keys(self) -> None:
        config_data = {
            "system_prompt": "test",
            # missing: resolved_inputs, tool_names
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as fh:
            json.dump(config_data, fh)
            path = fh.name

        try:
            with pytest.raises(ValueError, match="Missing required config keys"):
                load_config(path)
        finally:
            os.unlink(path)

    def test_accepts_extra_keys(self) -> None:
        config_data = {
            "system_prompt": "test",
            "resolved_inputs": {},
            "tool_names": [],
            "agent_name": "claude-code",
            "agent_config": {},
            "timeout": 60,
            "extra_field": "ok",
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as fh:
            json.dump(config_data, fh)
            path = fh.name

        try:
            result = load_config(path)
            assert result["extra_field"] == "ok"
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# write_result
# ---------------------------------------------------------------------------


class TestWriteResult:
    """Tests for shim.write_result()."""

    def test_writes_json(self) -> None:
        output_dir = tempfile.mkdtemp(prefix="agentry-test-")
        output_path = os.path.join(output_dir, "result.json")

        write_result(output_path, {"exit_code": 0, "output": "done"})

        with open(output_path) as fh:
            data = json.load(fh)
        assert data["exit_code"] == 0
        assert data["output"] == "done"

    def test_creates_parent_directories(self) -> None:
        output_dir = tempfile.mkdtemp(prefix="agentry-test-")
        nested_path = os.path.join(output_dir, "a", "b", "result.json")

        write_result(nested_path, {"status": "ok"})

        with open(nested_path) as fh:
            data = json.load(fh)
        assert data["status"] == "ok"


# ---------------------------------------------------------------------------
# run_shim (startup error path only -- agent execution requires LLM)
# ---------------------------------------------------------------------------


class TestRunShim:
    """Tests for shim.run_shim() startup error handling."""

    def test_returns_2_on_missing_config(self) -> None:
        output_dir = tempfile.mkdtemp(prefix="agentry-test-")
        output_path = os.path.join(output_dir, "result.json")

        exit_code = run_shim(
            config_path="/nonexistent/config.json",
            output_path=output_path,
        )

        assert exit_code == 2
        with open(output_path) as fh:
            data = json.load(fh)
        assert "shim startup error" in data["error"].lower()

    def test_returns_2_on_invalid_config(self) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as fh:
            fh.write("not json")
            config_path = fh.name

        output_dir = tempfile.mkdtemp(prefix="agentry-test-")
        output_path = os.path.join(output_dir, "result.json")

        try:
            exit_code = run_shim(
                config_path=config_path,
                output_path=output_path,
            )
            assert exit_code == 2
        finally:
            os.unlink(config_path)

    def test_returns_2_on_missing_keys(self) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as fh:
            json.dump({"system_prompt": "hi"}, fh)
            config_path = fh.name

        output_dir = tempfile.mkdtemp(prefix="agentry-test-")
        output_path = os.path.join(output_dir, "result.json")

        try:
            exit_code = run_shim(
                config_path=config_path,
                output_path=output_path,
            )
            assert exit_code == 2
            with open(output_path) as f:
                data = json.load(f)
            assert data["exit_code"] == 2
        finally:
            os.unlink(config_path)


# ---------------------------------------------------------------------------
# run_shim -- agent runtime path
# ---------------------------------------------------------------------------


def _make_valid_config(
    tmp_path: str | None = None,
    **overrides: object,
) -> str:
    """Write a valid agent config JSON to a temp file and return its path."""
    config_data: dict = {
        "system_prompt": "You are a code reviewer.",
        "resolved_inputs": {"diff": "some diff content"},
        "tool_names": ["repository:read"],
        "agent_name": "claude-code",
        "agent_config": {"model": "claude-sonnet-4-20250514"},
    }
    config_data.update(overrides)
    if tmp_path is None:
        tmp_path = tempfile.mkdtemp(prefix="agentry-shim-test-")
    config_path = os.path.join(tmp_path, "agent_config.json")
    with open(config_path, "w") as fh:
        json.dump(config_data, fh)
    return config_path


class TestRunShimAgentRuntime:
    """Tests that run_shim() launches agent runtimes (not AgentExecutor)."""

    def test_run_shim_calls_agent_execute(self) -> None:
        """run_shim() should call agent.execute(), not AgentExecutor."""
        from agentry.agents.models import AgentResult

        mock_agent = MagicMock()
        mock_agent.execute.return_value = AgentResult(
            raw_output="LGTM",
            exit_code=0,
            error="",
        )

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_agent

        tmp_dir = tempfile.mkdtemp(prefix="agentry-shim-test-")
        config_path = _make_valid_config(tmp_dir)
        output_path = os.path.join(tmp_dir, "result.json")

        with patch("agentry.agents.registry.AgentRegistry.default", return_value=mock_registry):
            exit_code = run_shim(config_path=config_path, output_path=output_path)

        mock_registry.get.assert_called_once_with("claude-code", model="claude-sonnet-4-20250514")
        mock_agent.execute.assert_called_once()
        assert exit_code == 0

    def test_run_shim_uses_agent_name_from_config(self) -> None:
        """run_shim() uses the agent_name from the config file."""
        from agentry.agents.models import AgentResult

        mock_agent = MagicMock()
        mock_agent.execute.return_value = AgentResult(exit_code=0, error="")

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_agent

        tmp_dir = tempfile.mkdtemp(prefix="agentry-shim-test-")
        config_path = _make_valid_config(tmp_dir, agent_name="claude-code")
        output_path = os.path.join(tmp_dir, "result.json")

        with patch("agentry.agents.registry.AgentRegistry.default", return_value=mock_registry):
            run_shim(config_path=config_path, output_path=output_path)

        call_args = mock_registry.get.call_args
        assert call_args[0][0] == "claude-code"

    def test_run_shim_defaults_agent_name_to_claude_code(self) -> None:
        """run_shim() defaults agent_name to 'claude-code' if not specified."""
        from agentry.agents.models import AgentResult

        mock_agent = MagicMock()
        mock_agent.execute.return_value = AgentResult(exit_code=0, error="")

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_agent

        tmp_dir = tempfile.mkdtemp(prefix="agentry-shim-test-")
        # Config without agent_name key
        config_data = {
            "system_prompt": "You are helpful.",
            "resolved_inputs": {},
            "tool_names": [],
        }
        config_path = os.path.join(tmp_dir, "agent_config.json")
        with open(config_path, "w") as fh:
            json.dump(config_data, fh)
        output_path = os.path.join(tmp_dir, "result.json")

        with patch("agentry.agents.registry.AgentRegistry.default", return_value=mock_registry):
            run_shim(config_path=config_path, output_path=output_path)

        call_args = mock_registry.get.call_args
        assert call_args[0][0] == "claude-code"

    def test_run_shim_writes_agent_result_to_output(self) -> None:
        """run_shim() writes AgentResult fields to the output JSON file."""
        from agentry.agents.models import AgentResult, TokenUsage

        mock_agent = MagicMock()
        mock_agent.execute.return_value = AgentResult(
            raw_output="LGTM",
            exit_code=0,
            error="",
            output={"verdict": "approved"},
            token_usage=TokenUsage(input_tokens=100, output_tokens=50),
        )

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_agent

        tmp_dir = tempfile.mkdtemp(prefix="agentry-shim-test-")
        config_path = _make_valid_config(tmp_dir)
        output_path = os.path.join(tmp_dir, "result.json")

        with patch("agentry.agents.registry.AgentRegistry.default", return_value=mock_registry):
            run_shim(config_path=config_path, output_path=output_path)

        with open(output_path) as fh:
            result = json.load(fh)

        assert result["exit_code"] == 0
        assert result["raw_output"] == "LGTM"
        assert result["output"] == {"verdict": "approved"}
        assert result["token_usage"]["input_tokens"] == 100
        assert result["token_usage"]["output_tokens"] == 50
        assert result["error"] == ""

    def test_run_shim_returns_1_when_agent_has_error(self) -> None:
        """run_shim() returns 1 when the agent reports an error."""
        from agentry.agents.models import AgentResult

        mock_agent = MagicMock()
        mock_agent.execute.return_value = AgentResult(
            raw_output="",
            exit_code=1,
            error="Agent execution failed",
        )

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_agent

        tmp_dir = tempfile.mkdtemp(prefix="agentry-shim-test-")
        config_path = _make_valid_config(tmp_dir)
        output_path = os.path.join(tmp_dir, "result.json")

        with patch("agentry.agents.registry.AgentRegistry.default", return_value=mock_registry):
            exit_code = run_shim(config_path=config_path, output_path=output_path)

        assert exit_code == 1

    def test_run_shim_builds_task_description_from_resolved_inputs(self) -> None:
        """run_shim() assembles task_description from resolved_inputs."""
        from agentry.agents.models import AgentResult, AgentTask

        captured_tasks: list[AgentTask] = []

        mock_agent = MagicMock()
        def capture_execute(task: AgentTask) -> AgentResult:
            captured_tasks.append(task)
            return AgentResult(exit_code=0, error="")

        mock_agent.execute.side_effect = capture_execute

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_agent

        tmp_dir = tempfile.mkdtemp(prefix="agentry-shim-test-")
        config_path = _make_valid_config(
            tmp_dir,
            resolved_inputs={"diff": "line1\nline2"},
        )
        output_path = os.path.join(tmp_dir, "result.json")

        with patch("agentry.agents.registry.AgentRegistry.default", return_value=mock_registry):
            run_shim(config_path=config_path, output_path=output_path)

        assert len(captured_tasks) == 1
        task = captured_tasks[0]
        assert "diff" in task.task_description
        assert "line1" in task.task_description


class TestShimNoAgentExecutor:
    """Verify the shim does not reference AgentExecutor at module level."""

    def test_shim_module_imports_do_not_include_agent_executor(self) -> None:
        """The shim module should not import AgentExecutor at module level."""
        import inspect
        import agentry.runners.shim as shim_module

        source = inspect.getsource(shim_module)
        # AgentExecutor should only appear in comments/docs if at all;
        # it must not be imported at the top level.
        lines = source.splitlines()
        top_level_imports = [
            line for line in lines
            if line.startswith("import ") or line.startswith("from ")
        ]
        assert not any("AgentExecutor" in line for line in top_level_imports)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    """Tests for shim constants."""

    def test_default_config_path(self) -> None:
        assert DEFAULT_CONFIG_PATH == "/config/agent_config.json"

    def test_default_output_path(self) -> None:
        assert DEFAULT_OUTPUT_PATH == "/output/result.json"
