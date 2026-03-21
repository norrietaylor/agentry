"""Unit tests for T01.2: Pydantic v2 models for all seven workflow blocks.

Tests cover:
- IdentityBlock validates name, version (semver), and description.
- Input discriminated unions: GitDiffInput, RepositoryRefInput, DocumentRefInput, StringInput.
- ToolsBlock accepts and stores capability lists.
- ModelBlock validates provider, model_id, temperature, max_tokens, system_prompt, retry.
- SafetyBlock validates resource constraints (timeout).
- OutputBlock validates schema, side_effects, output_paths, budget.
- CompositionBlock validates steps with depends_on (parsed, not executed).
- WorkflowDefinition composes all seven blocks.
- WorkflowDefinition rejects unknown keys at any nesting level (extra='forbid').
- WorkflowDefinition validates $variable references resolve to inputs or well-knowns.
- Semver validation rejects invalid version strings.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentry.models import (
    BudgetConfig,
    CompositionBlock,
    CompositionStep,
    DocumentRefInput,
    FilesystemConfig,
    GitDiffInput,
    IdentityBlock,
    ModelBlock,
    NetworkConfig,
    OutputBlock,
    RepositoryRefInput,
    ResourceConfig,
    RetryConfig,
    SafetyBlock,
    SandboxConfig,
    SideEffect,
    ToolsBlock,
    TrustLevel,
    WorkflowDefinition,
)
from agentry.models.inputs import StringInput

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _minimal_identity() -> dict:
    """Return a minimal valid identity block dict."""
    return {"name": "test-workflow", "version": "1.0.0", "description": "A test workflow"}


def _minimal_workflow(**overrides) -> dict:
    """Return a minimal valid workflow definition dict."""
    data: dict = {"identity": _minimal_identity()}
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# IdentityBlock
# ---------------------------------------------------------------------------


class TestIdentityBlock:
    def test_valid_identity(self) -> None:
        block = IdentityBlock(**_minimal_identity())
        assert block.name == "test-workflow"
        assert block.version == "1.0.0"
        assert block.description == "A test workflow"

    def test_semver_major_minor_patch(self) -> None:
        block = IdentityBlock(name="w", version="0.2.1", description="d")
        assert block.version == "0.2.1"

    def test_semver_with_prerelease(self) -> None:
        block = IdentityBlock(name="w", version="1.0.0-beta", description="d")
        assert block.version == "1.0.0-beta"

    def test_semver_with_build_metadata(self) -> None:
        block = IdentityBlock(name="w", version="1.0.0+build.123", description="d")
        assert block.version == "1.0.0+build.123"

    def test_invalid_semver_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Invalid semantic version"):
            IdentityBlock(name="w", version="not-a-version", description="d")

    def test_invalid_semver_missing_patch(self) -> None:
        with pytest.raises(ValidationError, match="Invalid semantic version"):
            IdentityBlock(name="w", version="1.0", description="d")

    def test_invalid_semver_letters(self) -> None:
        with pytest.raises(ValidationError, match="Invalid semantic version"):
            IdentityBlock(name="w", version="abc", description="d")

    def test_unknown_key_rejected(self) -> None:
        with pytest.raises(ValidationError, match="extra_field"):
            IdentityBlock(
                name="w", version="1.0.0", description="d", extra_field="bad"  # type: ignore[call-arg]
            )

    def test_missing_required_name(self) -> None:
        with pytest.raises(ValidationError):
            IdentityBlock(version="1.0.0", description="d")  # type: ignore[call-arg]

    def test_missing_required_version(self) -> None:
        with pytest.raises(ValidationError):
            IdentityBlock(name="w", description="d")  # type: ignore[call-arg]

    def test_missing_required_description(self) -> None:
        with pytest.raises(ValidationError):
            IdentityBlock(name="w", version="1.0.0")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Input discriminated unions
# ---------------------------------------------------------------------------


class TestGitDiffInput:
    def test_valid_git_diff(self) -> None:
        inp = GitDiffInput(type="git-diff", required=True, ref="HEAD~1")
        assert inp.type == "git-diff"
        assert inp.required is True
        assert inp.ref == "HEAD~1"

    def test_defaults(self) -> None:
        inp = GitDiffInput(type="git-diff")
        assert inp.required is True
        assert inp.ref == "HEAD~1"
        assert inp.description == ""

    def test_unknown_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GitDiffInput(type="git-diff", extra="bad")  # type: ignore[call-arg]


class TestRepositoryRefInput:
    def test_valid_repository_ref(self) -> None:
        inp = RepositoryRefInput(type="repository-ref", required=True)
        assert inp.type == "repository-ref"

    def test_defaults(self) -> None:
        inp = RepositoryRefInput(type="repository-ref")
        assert inp.required is True


class TestDocumentRefInput:
    def test_valid_document_ref(self) -> None:
        inp = DocumentRefInput(type="document-ref", path="docs/guide.md")
        assert inp.type == "document-ref"
        assert inp.path == "docs/guide.md"

    def test_defaults(self) -> None:
        inp = DocumentRefInput(type="document-ref")
        assert inp.required is False
        assert inp.path == ""


class TestStringInput:
    def test_valid_string_input(self) -> None:
        inp = StringInput(type="string", required=True, description="Issue text")
        assert inp.type == "string"
        assert inp.required is True

    def test_default_value(self) -> None:
        inp = StringInput(type="string", default="hello")
        assert inp.default == "hello"


# ---------------------------------------------------------------------------
# ToolsBlock
# ---------------------------------------------------------------------------


class TestToolsBlock:
    def test_empty_capabilities(self) -> None:
        block = ToolsBlock()
        assert block.capabilities == []

    def test_with_capabilities(self) -> None:
        block = ToolsBlock(capabilities=["repository:read", "shell:execute"])
        assert "repository:read" in block.capabilities
        assert len(block.capabilities) == 2

    def test_unknown_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ToolsBlock(capabilities=[], extra="bad")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# ModelBlock
# ---------------------------------------------------------------------------


class TestModelBlock:
    def test_defaults(self) -> None:
        block = ModelBlock()
        assert block.provider == "anthropic"
        assert block.temperature == 0.2
        assert block.max_tokens == 4096
        assert block.system_prompt == ""

    def test_custom_values(self) -> None:
        block = ModelBlock(
            provider="anthropic",
            model_id="claude-opus-4-20250514",
            temperature=0.5,
            max_tokens=8192,
            system_prompt="prompts/review.md",
        )
        assert block.model_id == "claude-opus-4-20250514"
        assert block.temperature == 0.5

    def test_temperature_out_of_range(self) -> None:
        with pytest.raises(ValidationError, match="temperature"):
            ModelBlock(temperature=3.0)

    def test_max_tokens_zero_rejected(self) -> None:
        with pytest.raises(ValidationError, match="max_tokens"):
            ModelBlock(max_tokens=0)

    def test_retry_config_defaults(self) -> None:
        block = ModelBlock()
        assert block.retry.max_attempts == 3
        assert block.retry.backoff == "exponential"

    def test_retry_config_custom(self) -> None:
        block = ModelBlock(retry=RetryConfig(max_attempts=5, backoff="linear"))
        assert block.retry.max_attempts == 5
        assert block.retry.backoff == "linear"

    def test_unknown_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelBlock(extra_field="bad")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# SafetyBlock
# ---------------------------------------------------------------------------


class TestSafetyBlock:
    def test_defaults(self) -> None:
        block = SafetyBlock()
        assert block.resources.timeout == 300

    def test_custom_timeout(self) -> None:
        block = SafetyBlock(resources=ResourceConfig(timeout=600))
        assert block.resources.timeout == 600

    def test_timeout_zero_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timeout"):
            ResourceConfig(timeout=0)

    def test_unknown_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SafetyBlock(extra="bad")  # type: ignore[call-arg]

    # Phase 2 fields -----------------------------------------------------------

    def test_default_trust_is_sandboxed(self) -> None:
        block = SafetyBlock()
        assert block.trust == TrustLevel.sandboxed

    def test_elevated_trust(self) -> None:
        block = SafetyBlock(trust="elevated")
        assert block.trust == TrustLevel.elevated

    def test_invalid_trust_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SafetyBlock(trust="root")  # type: ignore[arg-type]

    def test_resource_defaults_include_cpu_and_memory(self) -> None:
        cfg = ResourceConfig()
        assert cfg.cpu == 1.0
        assert cfg.memory == "2GB"

    def test_resource_custom_cpu_and_memory(self) -> None:
        cfg = ResourceConfig(cpu=0.5, memory="512MB")
        assert cfg.cpu == 0.5
        assert cfg.memory == "512MB"

    def test_resource_cpu_zero_rejected(self) -> None:
        with pytest.raises(ValidationError, match="cpu"):
            ResourceConfig(cpu=0.0)

    def test_filesystem_defaults_empty(self) -> None:
        cfg = FilesystemConfig()
        assert cfg.read == []
        assert cfg.write == []

    def test_filesystem_custom_paths(self) -> None:
        cfg = FilesystemConfig(read=["/src/**"], write=["/out/**"])
        assert "/src/**" in cfg.read
        assert "/out/**" in cfg.write

    def test_filesystem_unknown_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FilesystemConfig(read=[], extra="bad")  # type: ignore[call-arg]

    def test_network_defaults_empty(self) -> None:
        cfg = NetworkConfig()
        assert cfg.allow == []

    def test_network_custom_allow(self) -> None:
        cfg = NetworkConfig(allow=["api.anthropic.com", "github.com"])
        assert "api.anthropic.com" in cfg.allow

    def test_network_unknown_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            NetworkConfig(allow=[], extra="bad")  # type: ignore[call-arg]

    def test_sandbox_default_base(self) -> None:
        cfg = SandboxConfig()
        assert cfg.base == "agentry/sandbox:1.0"

    def test_sandbox_custom_base(self) -> None:
        cfg = SandboxConfig(base="myorg/sandbox:2.0")
        assert cfg.base == "myorg/sandbox:2.0"

    def test_sandbox_unknown_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SandboxConfig(base="img", extra="bad")  # type: ignore[call-arg]

    def test_safety_block_defaults_all_phase2_fields(self) -> None:
        block = SafetyBlock()
        assert block.trust == TrustLevel.sandboxed
        assert block.resources.cpu == 1.0
        assert block.resources.memory == "2GB"
        assert block.filesystem.read == []
        assert block.filesystem.write == []
        assert block.network.allow == []
        assert block.sandbox.base == "agentry/sandbox:1.0"

    def test_safety_block_full_phase2_config(self) -> None:
        block = SafetyBlock(
            trust="elevated",
            resources=ResourceConfig(timeout=600, cpu=2.0, memory="4GB"),
            filesystem=FilesystemConfig(read=["/src/**"], write=["/out/**"]),
            network=NetworkConfig(allow=["api.anthropic.com"]),
            sandbox=SandboxConfig(base="myorg/sandbox:2.0"),
        )
        assert block.trust == TrustLevel.elevated
        assert block.resources.cpu == 2.0
        assert block.resources.memory == "4GB"
        assert "/src/**" in block.filesystem.read
        assert "api.anthropic.com" in block.network.allow
        assert block.sandbox.base == "myorg/sandbox:2.0"

    def test_backward_compat_phase1_only_resources(self) -> None:
        """Phase 1 workflows with only resources.timeout must still parse."""
        block = SafetyBlock(resources=ResourceConfig(timeout=120))
        assert block.resources.timeout == 120
        # Phase 2 defaults remain intact
        assert block.trust == TrustLevel.sandboxed
        assert block.filesystem.read == []

    def test_backward_compat_empty_safety_block(self) -> None:
        """Empty safety block (no fields) must parse with all defaults."""
        block = SafetyBlock()
        assert block.resources.timeout == 300


# ---------------------------------------------------------------------------
# OutputBlock
# ---------------------------------------------------------------------------


class TestOutputBlock:
    def test_defaults(self) -> None:
        block = OutputBlock()
        assert block.schema_def == {}
        assert block.side_effects == []
        assert block.output_paths == []

    def test_with_schema(self) -> None:
        schema = {"type": "object", "properties": {"findings": {"type": "array"}}}
        block = OutputBlock(schema=schema)
        assert block.schema_def == schema

    def test_with_side_effects(self) -> None:
        block = OutputBlock(
            side_effects=[SideEffect(type="terminal", description="Print to stdout")]
        )
        assert len(block.side_effects) == 1
        assert block.side_effects[0].type == "terminal"

    def test_with_output_paths(self) -> None:
        block = OutputBlock(output_paths=["findings.json"])
        assert "findings.json" in block.output_paths

    def test_budget_max_findings(self) -> None:
        block = OutputBlock(budget=BudgetConfig(max_findings=10))
        assert block.budget.max_findings == 10

    def test_budget_max_findings_zero_rejected(self) -> None:
        with pytest.raises(ValidationError, match="max_findings"):
            BudgetConfig(max_findings=0)

    def test_unknown_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            OutputBlock(extra="bad")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# CompositionBlock
# ---------------------------------------------------------------------------


class TestCompositionBlock:
    def test_defaults(self) -> None:
        block = CompositionBlock()
        assert block.steps == []

    def test_with_steps(self) -> None:
        block = CompositionBlock(
            steps=[
                CompositionStep(name="review", workflow="code-review.yaml"),
                CompositionStep(
                    name="fix",
                    workflow="bug-fix.yaml",
                    depends_on=["review"],
                ),
            ]
        )
        assert len(block.steps) == 2
        assert block.steps[1].depends_on == ["review"]

    def test_unknown_key_on_step_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CompositionStep(name="s", workflow="w.yaml", extra="bad")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# WorkflowDefinition: composition of all blocks
# ---------------------------------------------------------------------------


class TestWorkflowDefinition:
    def test_minimal_workflow(self) -> None:
        wf = WorkflowDefinition(**_minimal_workflow())
        assert wf.identity.name == "test-workflow"
        assert wf.inputs == {}
        assert wf.tools.capabilities == []

    def test_full_workflow(self) -> None:
        data = _minimal_workflow(
            inputs={
                "diff": {"type": "git-diff", "required": True, "ref": "HEAD~1"},
                "repo": {"type": "repository-ref", "required": True},
            },
            tools={"capabilities": ["repository:read"]},
            model={
                "provider": "anthropic",
                "model_id": "claude-sonnet-4-20250514",
                "temperature": 0.2,
                "max_tokens": 4096,
                "system_prompt": "prompts/review.md",
                "retry": {"max_attempts": 3, "backoff": "exponential"},
            },
            safety={"resources": {"timeout": 300}},
            output={
                "schema": {"type": "object"},
                "side_effects": [],
                "output_paths": ["findings.json"],
                "budget": {"max_findings": 10},
            },
            composition={
                "steps": [
                    {"name": "review", "workflow": "code-review.yaml"},
                ]
            },
        )
        wf = WorkflowDefinition(**data)
        assert wf.identity.version == "1.0.0"
        assert "diff" in wf.inputs
        assert wf.output.budget.max_findings == 10
        assert len(wf.composition.steps) == 1

    def test_unknown_top_level_key_rejected(self) -> None:
        data = _minimal_workflow(extra_block={"foo": "bar"})
        with pytest.raises(ValidationError, match="extra_block"):
            WorkflowDefinition(**data)

    def test_unknown_nested_key_rejected(self) -> None:
        data = _minimal_workflow(
            model={
                "provider": "anthropic",
                "extra_field": "bad",
            }
        )
        with pytest.raises(ValidationError, match="extra_field"):
            WorkflowDefinition(**data)


# ---------------------------------------------------------------------------
# Variable reference validation
# ---------------------------------------------------------------------------


class TestVariableReferenceValidation:
    def test_valid_input_reference(self) -> None:
        """$diff resolves to declared input 'diff'."""
        data = _minimal_workflow(
            inputs={"diff": {"type": "git-diff", "ref": "HEAD~1"}},
            model={"system_prompt": "Review the $diff"},
        )
        wf = WorkflowDefinition(**data)
        assert wf.identity.name == "test-workflow"

    def test_well_known_variable_accepted(self) -> None:
        """$output_dir, $codebase, $diff, $pr_url are always valid."""
        data = _minimal_workflow(
            model={"system_prompt": "Write to $output_dir"},
        )
        wf = WorkflowDefinition(**data)
        assert wf.identity.name == "test-workflow"

    def test_undefined_variable_rejected(self) -> None:
        data = _minimal_workflow(
            model={"system_prompt": "Use $undefined_var please"},
        )
        with pytest.raises(ValidationError, match="undefined_var"):
            WorkflowDefinition(**data)

    def test_multiple_undefined_variables(self) -> None:
        data = _minimal_workflow(
            model={"system_prompt": "$alpha and $beta"},
        )
        with pytest.raises(ValidationError, match="Unresolved variable"):
            WorkflowDefinition(**data)

    def test_no_variables_is_fine(self) -> None:
        data = _minimal_workflow(
            model={"system_prompt": "No variables here"},
        )
        wf = WorkflowDefinition(**data)
        assert wf.model.system_prompt == "No variables here"


# ---------------------------------------------------------------------------
# Discriminated union via dict
# ---------------------------------------------------------------------------


class TestDiscriminatedUnionInWorkflow:
    def test_git_diff_input_via_dict(self) -> None:
        data = _minimal_workflow(
            inputs={"diff": {"type": "git-diff", "ref": "main"}},
        )
        wf = WorkflowDefinition(**data)
        assert isinstance(wf.inputs["diff"], GitDiffInput)
        assert wf.inputs["diff"].ref == "main"

    def test_repository_ref_input_via_dict(self) -> None:
        data = _minimal_workflow(
            inputs={"repo": {"type": "repository-ref"}},
        )
        wf = WorkflowDefinition(**data)
        assert isinstance(wf.inputs["repo"], RepositoryRefInput)

    def test_document_ref_input_via_dict(self) -> None:
        data = _minimal_workflow(
            inputs={"doc": {"type": "document-ref", "path": "guide.md"}},
        )
        wf = WorkflowDefinition(**data)
        assert isinstance(wf.inputs["doc"], DocumentRefInput)

    def test_string_input_via_dict(self) -> None:
        data = _minimal_workflow(
            inputs={"desc": {"type": "string", "required": True}},
        )
        wf = WorkflowDefinition(**data)
        assert isinstance(wf.inputs["desc"], StringInput)

    def test_invalid_input_type_rejected(self) -> None:
        data = _minimal_workflow(
            inputs={"x": {"type": "unknown-type"}},
        )
        with pytest.raises(ValidationError):
            WorkflowDefinition(**data)

    def test_git_diff_missing_type_rejected(self) -> None:
        data = _minimal_workflow(
            inputs={"diff": {"ref": "HEAD~1"}},
        )
        with pytest.raises(ValidationError):
            WorkflowDefinition(**data)
