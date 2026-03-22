"""Unit tests for T02: RunnerDetector resolves agent by name and injects into runner.

Tests cover:
- RunnerDetector accepts AgentRegistry instead of llm_client.
- get_runner() with trust: elevated resolves agent by name and injects into InProcessRunner.
- get_runner() forwards agent_kwargs to the registry factory.
- get_runner() with trust: sandboxed and Docker available returns DockerRunner.
- get_runner() with trust: sandboxed and Docker unavailable raises RuntimeError.
- Default trust level is sandboxed.
- InProcessRunner from detector satisfies RunnerProtocol.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentry.models.safety import SafetyBlock
from agentry.runners.protocol import RunnerProtocol


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_registry():
    """Return the default AgentRegistry."""
    from agentry.agents.registry import AgentRegistry

    return AgentRegistry.default()


def _mock_registry(agent: object = None):
    """Return a mock AgentRegistry that returns *agent* from get()."""
    from agentry.agents.registry import AgentRegistry

    mock = MagicMock(spec=AgentRegistry)
    mock.get.return_value = agent or MagicMock()
    return mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRunnerDetectorAgentResolution:
    """T02 proof: RunnerDetector resolves agent by name and injects into runner."""

    def test_init_accepts_agent_registry(self) -> None:
        """RunnerDetector can be constructed with an AgentRegistry."""
        from agentry.runners.detector import RunnerDetector

        detector = RunnerDetector(agent_registry=_default_registry())
        assert detector.agent_registry is not None

    def test_elevated_trust_returns_in_process_runner(self) -> None:
        """get_runner() returns InProcessRunner when trust: elevated."""
        from agentry.runners.detector import RunnerDetector
        from agentry.runners.in_process import InProcessRunner

        detector = RunnerDetector(agent_registry=_default_registry())
        runner = detector.get_runner(SafetyBlock(trust="elevated"))

        assert isinstance(runner, InProcessRunner)

    def test_elevated_trust_resolves_agent_by_name(self) -> None:
        """get_runner() calls registry.get(agent_name) to resolve the agent."""
        from agentry.runners.detector import RunnerDetector

        mock_agent = MagicMock()
        registry = _mock_registry(mock_agent)

        detector = RunnerDetector(
            agent_registry=registry, agent_name="claude-code"
        )
        runner = detector.get_runner(SafetyBlock(trust="elevated"))

        registry.get.assert_called_once_with("claude-code")

    def test_elevated_trust_injects_resolved_agent_into_runner(self) -> None:
        """The resolved agent is injected as runner.agent."""
        from agentry.runners.detector import RunnerDetector
        from agentry.runners.in_process import InProcessRunner

        mock_agent = MagicMock()
        registry = _mock_registry(mock_agent)

        detector = RunnerDetector(
            agent_registry=registry, agent_name="claude-code"
        )
        runner = detector.get_runner(SafetyBlock(trust="elevated"))

        assert isinstance(runner, InProcessRunner)
        assert runner.agent is mock_agent

    def test_elevated_trust_forwards_agent_kwargs(self) -> None:
        """get_runner() forwards agent_kwargs to the registry factory."""
        from agentry.runners.detector import RunnerDetector

        registry = _mock_registry()

        detector = RunnerDetector(
            agent_registry=registry,
            agent_name="claude-code",
            agent_kwargs={"model": "claude-opus-4-5"},
        )
        detector.get_runner(SafetyBlock(trust="elevated"))

        registry.get.assert_called_once_with(
            "claude-code", model="claude-opus-4-5"
        )

    def test_default_agent_name_is_claude_code(self) -> None:
        """RunnerDetector defaults to agent_name='claude-code'."""
        from agentry.runners.detector import RunnerDetector

        detector = RunnerDetector(agent_registry=_default_registry())
        assert detector.agent_name == "claude-code"

    def test_elevated_trust_does_not_check_docker(self) -> None:
        """get_runner() with elevated trust does not probe Docker."""
        from agentry.runners.detector import RunnerDetector

        mock_docker_client = MagicMock()
        mock_docker_client.ping.side_effect = Exception("Should not be called")

        detector = RunnerDetector(
            agent_registry=_default_registry(),
            docker_client=mock_docker_client,
        )
        runner = detector.get_runner(SafetyBlock(trust="elevated"))

        mock_docker_client.ping.assert_not_called()
        assert runner is not None

    def test_elevated_trust_resolves_claude_code_agent(self) -> None:
        """Default registry resolves 'claude-code' to ClaudeCodeAgent."""
        from agentry.agents.claude_code import ClaudeCodeAgent
        from agentry.runners.detector import RunnerDetector
        from agentry.runners.in_process import InProcessRunner

        detector = RunnerDetector(
            agent_registry=_default_registry(), agent_name="claude-code"
        )
        runner = detector.get_runner(SafetyBlock(trust="elevated"))

        assert isinstance(runner, InProcessRunner)
        assert isinstance(runner.agent, ClaudeCodeAgent)

    def test_sandboxed_trust_docker_available_returns_docker_runner(self) -> None:
        """get_runner() returns DockerRunner when sandboxed and Docker available."""
        from agentry.runners.detector import RunnerDetector
        from agentry.runners.docker_runner import DockerRunner

        mock_docker_client = MagicMock()
        mock_docker_client.ping.return_value = True

        detector = RunnerDetector(
            agent_registry=_default_registry(),
            docker_client=mock_docker_client,
        )
        runner = detector.get_runner(SafetyBlock(trust="sandboxed"))

        assert isinstance(runner, DockerRunner)

    def test_sandboxed_trust_docker_unavailable_raises(self) -> None:
        """get_runner() raises RuntimeError when sandboxed but Docker unavailable."""
        from agentry.runners.detector import RunnerDetector

        mock_docker_client = MagicMock()
        mock_docker_client.ping.side_effect = Exception("Docker not available")

        detector = RunnerDetector(
            agent_registry=_default_registry(),
            docker_client=mock_docker_client,
        )

        with pytest.raises(RuntimeError) as exc_info:
            detector.get_runner(SafetyBlock(trust="sandboxed"))

        error_msg = str(exc_info.value)
        assert "Docker is required for sandboxed execution" in error_msg
        assert "trust: elevated" in error_msg

    def test_default_trust_is_sandboxed(self) -> None:
        """SafetyBlock default trust is sandboxed; detector raises without Docker."""
        from agentry.runners.detector import RunnerDetector

        safety = SafetyBlock()
        assert safety.trust.value == "sandboxed"

        mock_docker_client = MagicMock()
        mock_docker_client.ping.side_effect = Exception("No Docker")

        detector = RunnerDetector(
            agent_registry=_default_registry(),
            docker_client=mock_docker_client,
        )

        with pytest.raises(RuntimeError):
            detector.get_runner(safety)

    def test_returned_runner_satisfies_protocol(self) -> None:
        """All runners returned by get_runner() satisfy RunnerProtocol."""
        from agentry.runners.detector import RunnerDetector

        mock_docker_client = MagicMock()
        mock_docker_client.ping.return_value = True

        detector = RunnerDetector(
            agent_registry=_default_registry(),
            docker_client=mock_docker_client,
        )

        runner_elevated = detector.get_runner(SafetyBlock(trust="elevated"))
        runner_sandboxed = detector.get_runner(SafetyBlock(trust="sandboxed"))

        assert isinstance(runner_elevated, RunnerProtocol)
        assert isinstance(runner_sandboxed, RunnerProtocol)
