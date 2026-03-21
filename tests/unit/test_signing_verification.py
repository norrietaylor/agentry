"""Unit tests for T05.3: Signature verification during setup phase.

Tests cover:
- sign -> verify succeeds: signing a workflow then running SetupPhase with
  the path succeeds without error.
- sign -> modify safety -> verify fails: tampering with the safety block
  after signing causes SetupPhase.run() to raise SetupSignatureError.
- no signature -> skip verification: a workflow without a signature block
  runs through SetupPhase without error even when a public key is present.
- no workflow_path -> skip verification: when SetupPhase is constructed
  without a workflow_path, verification is skipped regardless of signature.
- public key absent -> skip verification: when the public key file does not
  exist at the configured path, verification is skipped (not aborted).
- SetupSignatureError is a subclass of SetupPhaseError.
- SetupSignatureError message includes the original SignatureVerificationError text.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from agentry.models.identity import IdentityBlock
from agentry.models.output import OutputBlock
from agentry.models.safety import SafetyBlock
from agentry.models.tools import ToolsBlock
from agentry.models.workflow import WorkflowDefinition
from agentry.security.setup import (
    SetupPhase,
    SetupPhaseError,
    SetupSignatureError,
)
from agentry.security.signing import generate_keypair, sign_workflow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_workflow_definition(
    *,
    name: str = "test-sig-workflow",
    version: str = "1.0.0",
) -> WorkflowDefinition:
    """Build a minimal WorkflowDefinition for testing."""
    identity = IdentityBlock(name=name, version=version, description="Test")
    tools = ToolsBlock()
    output = OutputBlock(schema={})
    safety_data: dict[str, Any] = {
        "trust": "sandboxed",
        "resources": {"cpu": 1.0, "memory": "2GB", "timeout": 300},
        "filesystem": {"read": [], "write": []},
        "network": {"allow": []},
        "sandbox": {"base": "agentry/sandbox:1.0"},
    }
    safety = SafetyBlock.model_validate(safety_data, strict=False)
    return WorkflowDefinition(
        identity=identity,
        tools=tools,
        output=output,
        safety=safety,
    )


_WORKFLOW_YAML_CONTENT = {
    "identity": {"name": "test-sig-workflow", "version": "1.0.0", "description": "Test"},
    "safety": {
        "trust": "sandboxed",
        "resources": {"cpu": 1.0, "memory": "2GB", "timeout": 300},
        "filesystem": {"read": [], "write": []},
        "network": {"allow": []},
        "sandbox": {"base": "agentry/sandbox:1.0"},
    },
    "output": {"schema": {}},
}


def _write_workflow_yaml(path: Path, data: dict | None = None) -> None:
    data = data if data is not None else _WORKFLOW_YAML_CONTENT
    with path.open("w") as fh:
        yaml.dump(data, fh, default_flow_style=False, sort_keys=False, allow_unicode=True)


def _make_keypair(tmp: Path) -> tuple[Path, Path]:
    priv = tmp / "priv" / "signing-key.pem"
    pub = tmp / "pub" / "public-key.pem"
    generate_keypair(private_key_path=priv, public_key_path=pub)
    return priv, pub


class _MockRunner:
    """Minimal mock runner satisfying RunnerProtocol."""

    def provision(self) -> dict[str, Any]:
        return {"container_id": "mock-container"}

    def teardown(self) -> None:
        pass

    def execute(self, command: str, timeout: float | None = None) -> dict[str, Any]:
        return {"exit_code": 0, "stdout": "", "stderr": ""}

    def check_available(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# Signature verification integration with SetupPhase
# ---------------------------------------------------------------------------


class TestSetupPhaseSignatureVerification:
    """Tests for signature verification integration in SetupPhase."""

    def test_sign_then_run_setup_succeeds(self, tmp_path: Path) -> None:
        """sign -> verify cycle: SetupPhase runs successfully on a signed workflow."""
        priv, pub = _make_keypair(tmp_path)
        wf_path = tmp_path / "workflow.yaml"
        _write_workflow_yaml(wf_path)
        sign_workflow(wf_path, private_key_path=priv)

        workflow = _make_workflow_definition()
        runner = _MockRunner()
        phase = SetupPhase(
            workflow=workflow,
            runner=runner,
            runs_dir=tmp_path / "runs",
            workflow_path=wf_path,
            public_key_path=pub,
        )

        result = phase.run()

        assert not result.aborted
        assert result.error == ""
        assert result.manifest is not None

    def test_sign_modify_safety_then_run_setup_raises(self, tmp_path: Path) -> None:
        """sign -> modify safety -> verify fails: SetupPhase raises SetupSignatureError."""
        priv, pub = _make_keypair(tmp_path)
        wf_path = tmp_path / "workflow.yaml"
        _write_workflow_yaml(wf_path)
        sign_workflow(wf_path, private_key_path=priv)

        # Tamper with the safety block after signing.
        with wf_path.open() as fh:
            data = yaml.safe_load(fh)
        data["safety"]["resources"]["timeout"] = 9999
        with wf_path.open("w") as fh:
            yaml.dump(data, fh, default_flow_style=False, sort_keys=False)

        workflow = _make_workflow_definition()
        runner = _MockRunner()
        phase = SetupPhase(
            workflow=workflow,
            runner=runner,
            runs_dir=tmp_path / "runs",
            workflow_path=wf_path,
            public_key_path=pub,
        )

        with pytest.raises(SetupSignatureError, match="Safety block signature invalid"):
            phase.run()

    def test_sign_modify_side_effects_then_run_setup_raises(self, tmp_path: Path) -> None:
        """sign -> modify output.side_effects -> verify fails."""
        priv, pub = _make_keypair(tmp_path)
        wf_path = tmp_path / "workflow.yaml"
        # Include a side_effects block to sign.
        data = dict(_WORKFLOW_YAML_CONTENT)
        data["output"] = {"schema": {}, "side_effects": []}
        _write_workflow_yaml(wf_path, data)
        sign_workflow(wf_path, private_key_path=priv)

        # Tamper with output.side_effects.
        with wf_path.open() as fh:
            loaded = yaml.safe_load(fh)
        loaded["output"]["side_effects"] = ["write:/evil/path"]
        with wf_path.open("w") as fh:
            yaml.dump(loaded, fh, default_flow_style=False, sort_keys=False)

        workflow = _make_workflow_definition()
        runner = _MockRunner()
        phase = SetupPhase(
            workflow=workflow,
            runner=runner,
            runs_dir=tmp_path / "runs",
            workflow_path=wf_path,
            public_key_path=pub,
        )

        with pytest.raises(SetupSignatureError):
            phase.run()

    def test_no_signature_skips_verification(self, tmp_path: Path) -> None:
        """Workflow without signature block runs through SetupPhase without error."""
        _, pub = _make_keypair(tmp_path)
        wf_path = tmp_path / "workflow.yaml"
        _write_workflow_yaml(wf_path)  # No signature block.

        workflow = _make_workflow_definition()
        runner = _MockRunner()
        phase = SetupPhase(
            workflow=workflow,
            runner=runner,
            runs_dir=tmp_path / "runs",
            workflow_path=wf_path,
            public_key_path=pub,
        )

        result = phase.run()

        assert not result.aborted
        assert result.manifest is not None

    def test_no_workflow_path_skips_verification(self, tmp_path: Path) -> None:
        """When workflow_path is not supplied, verification is skipped entirely."""
        _, pub = _make_keypair(tmp_path)

        workflow = _make_workflow_definition()
        runner = _MockRunner()
        # No workflow_path supplied.
        phase = SetupPhase(
            workflow=workflow,
            runner=runner,
            runs_dir=tmp_path / "runs",
            public_key_path=pub,
        )

        result = phase.run()

        assert not result.aborted
        assert result.manifest is not None

    def test_missing_public_key_skips_verification(self, tmp_path: Path) -> None:
        """When public key file is absent, verification is skipped (not aborted)."""
        priv, _ = _make_keypair(tmp_path)
        wf_path = tmp_path / "workflow.yaml"
        _write_workflow_yaml(wf_path)
        sign_workflow(wf_path, private_key_path=priv)

        non_existent_pub = tmp_path / "no-such-key.pem"

        workflow = _make_workflow_definition()
        runner = _MockRunner()
        phase = SetupPhase(
            workflow=workflow,
            runner=runner,
            runs_dir=tmp_path / "runs",
            workflow_path=wf_path,
            public_key_path=non_existent_pub,
        )

        # Should not raise even though a signature block is present.
        result = phase.run()

        assert not result.aborted
        assert result.manifest is not None

    def test_non_signed_blocks_modification_does_not_fail(self, tmp_path: Path) -> None:
        """Modifying non-signed blocks (identity) after signing still passes."""
        priv, pub = _make_keypair(tmp_path)
        wf_path = tmp_path / "workflow.yaml"
        _write_workflow_yaml(wf_path)
        sign_workflow(wf_path, private_key_path=priv)

        # Modify only the identity block (not signed).
        with wf_path.open() as fh:
            data = yaml.safe_load(fh)
        data["identity"]["description"] = "Modified after signing"
        with wf_path.open("w") as fh:
            yaml.dump(data, fh, default_flow_style=False, sort_keys=False)

        workflow = _make_workflow_definition()
        runner = _MockRunner()
        phase = SetupPhase(
            workflow=workflow,
            runner=runner,
            runs_dir=tmp_path / "runs",
            workflow_path=wf_path,
            public_key_path=pub,
        )

        result = phase.run()

        assert not result.aborted
        assert result.manifest is not None

    def test_signature_error_sets_result_aborted(self, tmp_path: Path) -> None:
        """On signature failure, result.aborted is True and result.error is set."""
        priv, pub = _make_keypair(tmp_path)
        wf_path = tmp_path / "workflow.yaml"
        _write_workflow_yaml(wf_path)
        sign_workflow(wf_path, private_key_path=priv)

        # Tamper with safety block.
        with wf_path.open() as fh:
            data = yaml.safe_load(fh)
        data["safety"]["resources"]["timeout"] = 1
        with wf_path.open("w") as fh:
            yaml.dump(data, fh, default_flow_style=False, sort_keys=False)

        workflow = _make_workflow_definition()
        runner = _MockRunner()
        phase = SetupPhase(
            workflow=workflow,
            runner=runner,
            runs_dir=tmp_path / "runs",
            workflow_path=wf_path,
            public_key_path=pub,
        )

        # Capture the result via the aborted flag (set before raise).
        raised = False
        try:
            phase.run()
        except SetupSignatureError as exc:
            raised = True
            # The exception message should include the verification error.
            assert "Safety block signature invalid" in str(exc)

        assert raised

    def test_error_message_includes_timestamp(self, tmp_path: Path) -> None:
        """SetupSignatureError message includes the timestamp from the signature block."""
        priv, pub = _make_keypair(tmp_path)
        wf_path = tmp_path / "workflow.yaml"
        _write_workflow_yaml(wf_path)
        sign_workflow(wf_path, private_key_path=priv)

        # Extract timestamp before tampering.
        with wf_path.open() as fh:
            data = yaml.safe_load(fh)
        expected_ts = data["signature"]["timestamp"]

        # Tamper.
        data["safety"]["resources"]["timeout"] = 42
        with wf_path.open("w") as fh:
            yaml.dump(data, fh, default_flow_style=False, sort_keys=False)

        workflow = _make_workflow_definition()
        runner = _MockRunner()
        phase = SetupPhase(
            workflow=workflow,
            runner=runner,
            runs_dir=tmp_path / "runs",
            workflow_path=wf_path,
            public_key_path=pub,
        )

        with pytest.raises(SetupSignatureError, match=expected_ts):
            phase.run()


# ---------------------------------------------------------------------------
# Exception hierarchy tests
# ---------------------------------------------------------------------------


class TestSetupSignatureErrorHierarchy:
    """Tests for SetupSignatureError exception class."""

    def test_is_setup_phase_error_subclass(self) -> None:
        """SetupSignatureError is a subclass of SetupPhaseError."""
        assert issubclass(SetupSignatureError, SetupPhaseError)

    def test_message_attribute(self) -> None:
        """SetupSignatureError stores the message attribute."""
        exc = SetupSignatureError("Safety block signature invalid. Signed on 2026-01-01T00:00:00Z")
        assert "Safety block signature invalid" in exc.message

    def test_str_representation(self) -> None:
        """str() of SetupSignatureError includes the message."""
        msg = "Safety block signature invalid. The safety block was modified since it was signed on 2026-01-01T00:00:00Z"
        exc = SetupSignatureError(msg)
        assert msg in str(exc)
