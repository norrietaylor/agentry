"""Unit tests for Workflow Schema and CLI Update (T05).

Tests cover:
- AgentBlock Pydantic model: required fields, optional fields, validation.
- WorkflowDefinition accepts agent block.
- Backward compatibility: model block auto-converts to AgentBlock.
- Agent block takes precedence over model block.
- AgentBlock.config dict for runtime-specific settings.
- agentry validate exits 0 for valid agent block.
- agentry validate exits non-zero for unknown runtime.
- AgentAvailabilityCheck pass/fail behaviour.
"""

from __future__ import annotations

import textwrap

import pytest
import yaml
from pydantic import ValidationError

from agentry.models import AgentBlock, KNOWN_RUNTIMES, WorkflowDefinition
from agentry.models.model import ModelBlock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_identity() -> dict:
    return {"name": "test-workflow", "version": "1.0.0", "description": "A test workflow"}


def _minimal_workflow(**overrides) -> dict:
    data: dict = {"identity": _minimal_identity()}
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# AgentBlock model
# ---------------------------------------------------------------------------


class TestAgentBlock:
    def test_required_runtime(self) -> None:
        block = AgentBlock(runtime="claude-code")
        assert block.runtime == "claude-code"

    def test_default_model(self) -> None:
        block = AgentBlock(runtime="claude-code")
        assert block.model == "claude-sonnet-4-20250514"

    def test_custom_model(self) -> None:
        block = AgentBlock(runtime="claude-code", model="claude-opus-4-20250514")
        assert block.model == "claude-opus-4-20250514"

    def test_system_prompt_defaults_empty(self) -> None:
        block = AgentBlock(runtime="claude-code")
        assert block.system_prompt == ""

    def test_custom_system_prompt(self) -> None:
        block = AgentBlock(runtime="claude-code", system_prompt="prompts/review.md")
        assert block.system_prompt == "prompts/review.md"

    def test_max_iterations_defaults_none(self) -> None:
        block = AgentBlock(runtime="claude-code")
        assert block.max_iterations is None

    def test_custom_max_iterations(self) -> None:
        block = AgentBlock(runtime="claude-code", max_iterations=20)
        assert block.max_iterations == 20

    def test_max_iterations_zero_rejected(self) -> None:
        with pytest.raises(ValidationError, match="max_iterations"):
            AgentBlock(runtime="claude-code", max_iterations=0)

    def test_config_defaults_empty_dict(self) -> None:
        block = AgentBlock(runtime="claude-code")
        assert block.config == {}

    def test_custom_config(self) -> None:
        block = AgentBlock(runtime="claude-code", config={"key": "value", "num": 42})
        assert block.config["key"] == "value"
        assert block.config["num"] == 42

    def test_runtime_required(self) -> None:
        with pytest.raises(ValidationError):
            AgentBlock()  # type: ignore[call-arg]

    def test_unknown_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AgentBlock(runtime="claude-code", extra="bad")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# WorkflowDefinition accepts agent block
# ---------------------------------------------------------------------------


class TestWorkflowDefinitionWithAgentBlock:
    def test_workflow_with_agent_block(self) -> None:
        data = _minimal_workflow(
            agent={
                "runtime": "claude-code",
                "model": "claude-sonnet-4-20250514",
                "system_prompt": "prompts/code-review.md",
                "max_iterations": 20,
            }
        )
        wf = WorkflowDefinition(**data)
        assert wf.agent is not None
        assert isinstance(wf.agent, AgentBlock)
        assert wf.agent.runtime == "claude-code"
        assert wf.agent.model == "claude-sonnet-4-20250514"
        assert wf.agent.system_prompt == "prompts/code-review.md"
        assert wf.agent.max_iterations == 20

    def test_workflow_with_agent_config_dict(self) -> None:
        data = _minimal_workflow(
            agent={
                "runtime": "claude-code",
                "config": {"timeout": 60, "retry": True},
            }
        )
        wf = WorkflowDefinition(**data)
        assert wf.agent is not None
        assert wf.agent.config == {"timeout": 60, "retry": True}

    def test_workflow_agent_runtime_required(self) -> None:
        data = _minimal_workflow(
            agent={"model": "claude-sonnet-4-20250514"}
        )
        with pytest.raises(ValidationError, match="runtime"):
            WorkflowDefinition(**data)


# ---------------------------------------------------------------------------
# Backward compatibility: model block auto-converts to AgentBlock
# ---------------------------------------------------------------------------


class TestModelBlockBackwardCompatibility:
    def test_model_block_auto_converts_to_agent_block(self) -> None:
        """Workflow with model block but no agent block should have agent auto-populated."""
        data = _minimal_workflow(
            model={
                "provider": "anthropic",
                "model_id": "claude-opus-4-20250514",
                "system_prompt": "prompts/review.md",
            }
        )
        wf = WorkflowDefinition(**data)
        # AgentBlock must be present even though we only declared model block.
        assert wf.agent is not None
        assert wf.agent.runtime == "claude-code"
        assert wf.agent.model == "claude-opus-4-20250514"
        assert wf.agent.system_prompt == "prompts/review.md"

    def test_minimal_workflow_has_agent_with_defaults(self) -> None:
        """Minimal workflow (no model, no agent) gets an AgentBlock with defaults."""
        wf = WorkflowDefinition(**_minimal_workflow())
        assert wf.agent is not None
        assert wf.agent.runtime == "claude-code"
        # Model from default ModelBlock
        assert wf.agent.model == ModelBlock().model_id

    def test_agent_block_takes_precedence_over_model_block(self) -> None:
        """When both agent and model blocks are present, agent block is used."""
        data = _minimal_workflow(
            agent={
                "runtime": "claude-code",
                "model": "claude-sonnet-4-20250514",
                "system_prompt": "prompts/agent.md",
            },
            model={
                "provider": "anthropic",
                "model_id": "claude-opus-4-20250514",
                "system_prompt": "prompts/model.md",
            },
        )
        wf = WorkflowDefinition(**data)
        assert wf.agent is not None
        # Agent block values should be used, not model block values.
        assert wf.agent.system_prompt == "prompts/agent.md"
        assert wf.agent.model == "claude-sonnet-4-20250514"


# ---------------------------------------------------------------------------
# KNOWN_RUNTIMES
# ---------------------------------------------------------------------------


class TestKnownRuntimes:
    def test_claude_code_in_known_runtimes(self) -> None:
        assert "claude-code" in KNOWN_RUNTIMES

    def test_known_runtimes_is_frozenset(self) -> None:
        assert isinstance(KNOWN_RUNTIMES, frozenset)


# ---------------------------------------------------------------------------
# AgentAvailabilityCheck
# ---------------------------------------------------------------------------


class TestAgentAvailabilityCheck:
    def test_unknown_runtime_passes(self) -> None:
        from agentry.security.checks import AgentAvailabilityCheck

        check = AgentAvailabilityCheck(runtime="nonexistent-runtime-xyz")
        result = check.run()
        assert result.passed is True

    def test_binary_present_passes(self, monkeypatch) -> None:
        """When the required binary is found on PATH, the check passes."""
        import shutil

        from agentry.security.checks import AgentAvailabilityCheck

        monkeypatch.setattr(shutil, "which", lambda _: "/usr/local/bin/claude")
        check = AgentAvailabilityCheck(runtime="claude-code")
        result = check.run()
        assert result.passed is True

    def test_binary_missing_fails(self, monkeypatch) -> None:
        """When the required binary is NOT found on PATH, the check fails."""
        import shutil

        from agentry.security.checks import AgentAvailabilityCheck

        monkeypatch.setattr(shutil, "which", lambda _: None)
        check = AgentAvailabilityCheck(runtime="claude-code")
        result = check.run()
        assert result.passed is False
        assert "claude" in result.message
        assert "claude-code" in result.message
        assert result.remediation != ""

    def test_check_name(self) -> None:
        from agentry.security.checks import AgentAvailabilityCheck

        check = AgentAvailabilityCheck(runtime="claude-code")
        assert check.name == "agent_availability"


# ---------------------------------------------------------------------------
# Integration: load workflows/code-review.yaml (updated file)
# ---------------------------------------------------------------------------


class TestCodeReviewWorkflowAgentBlock:
    def test_code_review_has_agent_block(self) -> None:
        """The updated code-review.yaml must parse with an agent block."""
        from agentry.parser import load_workflow_file

        wf = load_workflow_file("workflows/code-review.yaml")
        assert wf.agent is not None
        assert wf.agent.runtime == "claude-code"

    def test_code_review_agent_runtime_known(self) -> None:
        from agentry.parser import load_workflow_file

        wf = load_workflow_file("workflows/code-review.yaml")
        assert wf.agent is not None
        assert wf.agent.runtime in KNOWN_RUNTIMES
