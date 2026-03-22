"""Unit tests for T01: AgentProtocol and ClaudeCodeAgent.

Tests cover:
- ClaudeCodeAgent satisfies AgentProtocol at runtime (isinstance check).
- AgentTask carries all required and optional fields.
- AgentResult carries all required fields including token_usage.
- ClaudeCodeAgent.check_available() returns True when 'claude' is on PATH.
- ClaudeCodeAgent.check_available() returns False when 'claude' is absent.
- Mock subprocess execution produces a valid AgentResult.
- ClaudeCodeAgent passes --model flag.
- ClaudeCodeAgent passes --output-format json when output_schema is set.
- Timeout enforcement: AgentResult.timed_out is True when subprocess times out.
- Token usage extracted from JSON envelope.
- AgentRegistry maps 'claude-code' to ClaudeCodeAgent factory.
- AgentRegistry raises KeyError for unknown runtime.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agentry.agents.claude_code import ClaudeCodeAgent
from agentry.agents.models import AgentResult, AgentTask, TokenUsage
from agentry.agents.protocol import AgentProtocol
from agentry.agents.registry import AgentRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(
    *,
    system_prompt: str = "You are helpful.",
    task_description: str = "Do a thing.",
    tool_names: list[str] | None = None,
    output_schema: dict | None = None,
    timeout: float | None = None,
    working_directory: str = "",
) -> AgentTask:
    return AgentTask(
        system_prompt=system_prompt,
        task_description=task_description,
        tool_names=tool_names or [],
        output_schema=output_schema,
        timeout=timeout,
        working_directory=working_directory,
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAgentProtocolConformance:
    def test_claude_code_agent_satisfies_protocol(self) -> None:
        agent = ClaudeCodeAgent()
        assert isinstance(agent, AgentProtocol)

    def test_protocol_check_requires_execute_method(self) -> None:
        class NoExecute:
            @staticmethod
            def check_available() -> bool:
                return True

        assert not isinstance(NoExecute(), AgentProtocol)

    def test_protocol_check_requires_check_available(self) -> None:
        class NoCheckAvailable:
            def execute(self, agent_task: AgentTask) -> AgentResult:
                return AgentResult()

        assert not isinstance(NoCheckAvailable(), AgentProtocol)

    def test_agent_exposes_execute_method(self) -> None:
        agent = ClaudeCodeAgent()
        assert callable(agent.execute)

    def test_agent_exposes_check_available_static(self) -> None:
        assert callable(ClaudeCodeAgent.check_available)


# ---------------------------------------------------------------------------
# AgentTask model
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAgentTask:
    def test_required_fields(self) -> None:
        task = AgentTask(
            system_prompt="sys",
            task_description="desc",
            tool_names=["shell:run"],
            working_directory="/workspace",
        )
        assert task.system_prompt == "sys"
        assert task.task_description == "desc"
        assert task.tool_names == ["shell:run"]
        assert task.working_directory == "/workspace"

    def test_optional_fields_default_to_none(self) -> None:
        task = AgentTask(
            system_prompt="s",
            task_description="d",
        )
        assert task.output_schema is None
        assert task.timeout is None
        assert task.max_iterations is None

    def test_tool_names_default_empty_list(self) -> None:
        task = AgentTask(system_prompt="s", task_description="d")
        assert task.tool_names == []

    def test_output_schema_accepted(self) -> None:
        schema = {"type": "object", "properties": {"score": {"type": "integer"}}}
        task = AgentTask(
            system_prompt="s",
            task_description="d",
            output_schema=schema,
        )
        assert task.output_schema == schema

    def test_timeout_accepted(self) -> None:
        task = AgentTask(system_prompt="s", task_description="d", timeout=30.0)
        assert task.timeout == 30.0

    def test_max_iterations_accepted(self) -> None:
        task = AgentTask(system_prompt="s", task_description="d", max_iterations=5)
        assert task.max_iterations == 5


# ---------------------------------------------------------------------------
# AgentResult model
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAgentResult:
    def test_default_values(self) -> None:
        result = AgentResult()
        assert result.output is None
        assert result.raw_output == ""
        assert result.exit_code == 0
        assert result.token_usage.input_tokens == 0
        assert result.token_usage.output_tokens == 0
        assert result.tool_invocations == []
        assert result.timed_out is False
        assert result.error == ""

    def test_explicit_values(self) -> None:
        result = AgentResult(
            output={"score": 9},
            raw_output="raw text",
            exit_code=1,
            token_usage=TokenUsage(input_tokens=100, output_tokens=50),
            tool_invocations=[{"tool": "shell:run", "result": "ok"}],
            timed_out=True,
            error="something failed",
        )
        assert result.output == {"score": 9}
        assert result.raw_output == "raw text"
        assert result.exit_code == 1
        assert result.token_usage.input_tokens == 100
        assert result.token_usage.output_tokens == 50
        assert len(result.tool_invocations) == 1
        assert result.timed_out is True
        assert result.error == "something failed"


# ---------------------------------------------------------------------------
# ClaudeCodeAgent.check_available
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckAvailable:
    def test_returns_true_when_claude_on_path(self) -> None:
        with patch("agentry.agents.claude_code.shutil.which", return_value="/usr/bin/claude"):
            assert ClaudeCodeAgent.check_available() is True

    def test_returns_false_when_claude_missing(self) -> None:
        with patch("agentry.agents.claude_code.shutil.which", return_value=None):
            assert ClaudeCodeAgent.check_available() is False


# ---------------------------------------------------------------------------
# ClaudeCodeAgent.execute — mock subprocess
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestClaudeCodeAgentExecute:
    def _make_popen_mock(
        self,
        stdout: str = "Hello",
        stderr: str = "",
        returncode: int = 0,
        timeout_side_effect: Exception | None = None,
    ) -> MagicMock:
        """Build a mock Popen that communicate() returns (stdout, stderr)."""
        import subprocess

        mock_proc = MagicMock()
        mock_proc.returncode = returncode

        if timeout_side_effect is not None:
            mock_proc.communicate.side_effect = [
                subprocess.TimeoutExpired(cmd="claude", timeout=5),
                (stdout, stderr),
            ]
        else:
            mock_proc.communicate.return_value = (stdout, stderr)

        return mock_proc

    def test_basic_execution_returns_agent_result(self) -> None:
        mock_proc = self._make_popen_mock(stdout="task done", returncode=0)

        with patch("agentry.agents.claude_code.subprocess.Popen", return_value=mock_proc):
            agent = ClaudeCodeAgent(model="claude-sonnet-4-20250514")
            result = agent.execute(_make_task())

        assert isinstance(result, AgentResult)
        assert result.exit_code == 0
        assert result.raw_output == "task done"
        assert result.timed_out is False

    def test_passes_model_flag(self) -> None:
        mock_proc = self._make_popen_mock()
        call_args_holder: list[list[str]] = []

        def capture_popen(cmd: list[str], **kwargs: object) -> MagicMock:
            call_args_holder.append(cmd)
            return mock_proc

        with patch("agentry.agents.claude_code.subprocess.Popen", side_effect=capture_popen):
            agent = ClaudeCodeAgent(model="claude-sonnet-4-20250514")
            agent.execute(_make_task())

        cmd = call_args_holder[0]
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "claude-sonnet-4-20250514"

    def test_passes_print_flag(self) -> None:
        mock_proc = self._make_popen_mock()
        call_args_holder: list[list[str]] = []

        def capture_popen(cmd: list[str], **kwargs: object) -> MagicMock:
            call_args_holder.append(cmd)
            return mock_proc

        with patch("agentry.agents.claude_code.subprocess.Popen", side_effect=capture_popen):
            ClaudeCodeAgent().execute(_make_task())

        cmd = call_args_holder[0]
        assert "claude" in cmd
        assert "-p" in cmd

    def test_passes_system_prompt_flag(self) -> None:
        mock_proc = self._make_popen_mock()
        call_args_holder: list[list[str]] = []

        def capture_popen(cmd: list[str], **kwargs: object) -> MagicMock:
            call_args_holder.append(cmd)
            return mock_proc

        with patch("agentry.agents.claude_code.subprocess.Popen", side_effect=capture_popen):
            ClaudeCodeAgent().execute(_make_task(system_prompt="Review this code"))

        cmd = call_args_holder[0]
        assert "--system-prompt" in cmd
        idx = cmd.index("--system-prompt")
        assert cmd[idx + 1] == "Review this code"

    def test_output_format_json_when_schema_set(self) -> None:
        schema = {"type": "object"}
        mock_proc = self._make_popen_mock(
            stdout=json.dumps({"type": "result", "result": {"score": 5}, "usage": {}}),
        )
        call_args_holder: list[list[str]] = []

        def capture_popen(cmd: list[str], **kwargs: object) -> MagicMock:
            call_args_holder.append(cmd)
            return mock_proc

        with patch("agentry.agents.claude_code.subprocess.Popen", side_effect=capture_popen):
            result = ClaudeCodeAgent().execute(_make_task(output_schema=schema))

        cmd = call_args_holder[0]
        assert "--output-format" in cmd
        idx = cmd.index("--output-format")
        assert cmd[idx + 1] == "json"
        assert result.output == {"score": 5}

    def test_no_output_format_without_schema(self) -> None:
        mock_proc = self._make_popen_mock()
        call_args_holder: list[list[str]] = []

        def capture_popen(cmd: list[str], **kwargs: object) -> MagicMock:
            call_args_holder.append(cmd)
            return mock_proc

        with patch("agentry.agents.claude_code.subprocess.Popen", side_effect=capture_popen):
            ClaudeCodeAgent().execute(_make_task(output_schema=None))

        cmd = call_args_holder[0]
        assert "--output-format" not in cmd

    def test_timeout_kills_subprocess(self) -> None:
        mock_proc = self._make_popen_mock(
            stdout="partial",
            timeout_side_effect=Exception(),  # will be overridden below
        )

        with patch("agentry.agents.claude_code.subprocess.Popen", return_value=mock_proc):
            result = ClaudeCodeAgent().execute(_make_task(timeout=0.001))

        # Because we mock communicate to raise TimeoutExpired on first call
        assert result.timed_out is True
        assert "timed out" in result.error.lower()

    def test_non_zero_exit_code_sets_error(self) -> None:
        mock_proc = self._make_popen_mock(
            stdout="",
            stderr="something went wrong",
            returncode=1,
        )
        with patch("agentry.agents.claude_code.subprocess.Popen", return_value=mock_proc):
            result = ClaudeCodeAgent().execute(_make_task())

        assert result.exit_code == 1
        assert result.error != ""


# ---------------------------------------------------------------------------
# Token usage extraction
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTokenUsageExtraction:
    def test_token_usage_extracted_from_json_envelope(self) -> None:
        envelope = {
            "type": "result",
            "result": "some text",
            "usage": {"input_tokens": 42, "output_tokens": 17},
        }
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (json.dumps(envelope), "")

        with patch("agentry.agents.claude_code.subprocess.Popen", return_value=mock_proc):
            result = ClaudeCodeAgent().execute(_make_task(output_schema={"type": "object"}))

        assert result.token_usage.input_tokens == 42
        assert result.token_usage.output_tokens == 17

    def test_token_usage_defaults_to_zero_without_json(self) -> None:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = ("plain text output", "")

        with patch("agentry.agents.claude_code.subprocess.Popen", return_value=mock_proc):
            result = ClaudeCodeAgent().execute(_make_task())

        assert result.token_usage.input_tokens == 0
        assert result.token_usage.output_tokens == 0


# ---------------------------------------------------------------------------
# AgentRegistry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAgentRegistry:
    def test_default_registry_has_claude_code(self) -> None:
        registry = AgentRegistry.default()
        assert "claude-code" in registry.list_runtimes()

    def test_get_factory_returns_callable(self) -> None:
        registry = AgentRegistry.default()
        factory = registry.get_factory("claude-code")
        assert callable(factory)

    def test_factory_produces_claude_code_agent(self) -> None:
        registry = AgentRegistry.default()
        factory = registry.get_factory("claude-code")
        agent = factory()
        assert isinstance(agent, ClaudeCodeAgent)

    def test_get_returns_agent_instance(self) -> None:
        registry = AgentRegistry.default()
        agent = registry.get("claude-code")
        assert isinstance(agent, AgentProtocol)

    def test_get_with_kwargs_passes_to_factory(self) -> None:
        registry = AgentRegistry.default()
        agent = registry.get("claude-code", model="claude-opus-4-5")
        assert isinstance(agent, ClaudeCodeAgent)
        assert agent._model == "claude-opus-4-5"

    def test_get_raises_key_error_for_unknown_runtime(self) -> None:
        registry = AgentRegistry.default()
        with pytest.raises(KeyError, match="unknown-agent"):
            registry.get("unknown-agent")

    def test_get_factory_raises_key_error_for_unknown(self) -> None:
        registry = AgentRegistry.default()
        with pytest.raises(KeyError, match="unknown-agent"):
            registry.get_factory("unknown-agent")

    def test_register_custom_factory(self) -> None:
        class DummyAgent:
            def execute(self, agent_task: AgentTask) -> AgentResult:
                return AgentResult()

            @staticmethod
            def check_available() -> bool:
                return True

        registry = AgentRegistry()
        registry.register("dummy", DummyAgent)
        assert "dummy" in registry.list_runtimes()
        agent = registry.get("dummy")
        assert isinstance(agent, DummyAgent)

    def test_list_runtimes_returns_sorted_names(self) -> None:
        registry = AgentRegistry()
        registry.register("zzz", lambda: None)  # type: ignore[arg-type]
        registry.register("aaa", lambda: None)  # type: ignore[arg-type]
        runtimes = registry.list_runtimes()
        assert runtimes == sorted(runtimes)
