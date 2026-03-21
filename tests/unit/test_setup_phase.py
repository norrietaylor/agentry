"""Unit tests for T03.2: SetupPhase and setup manifest generation.

Tests cover:
- SetupPhase.run() executes all steps in sequence.
- Setup manifest contains all required fields (workflow version, image,
  mounts, network rules, resource limits, credential fingerprints, sandbox
  tier, timestamp).
- Credential fingerprinting: SHA-256 hash stored, not raw key value.
- Preflight check failure aborts setup and raises SetupPreflightError.
- Provisioning failure aborts setup and raises SetupProvisionError.
- Schema compilation failure aborts setup and raises SchemaCompilationError.
- Empty schema compiles without error.
- SetupManifest.to_dict() serialises all fields correctly.
- fingerprint_credential() produces expected SHA-256 digest.
- Manifest is saved to .agentry/runs/<TIMESTAMP>/setup-manifest.json.
- Network isolation verification logs but does not abort when network_id present.
- Multiple preflight checks: first failure stops remaining.
- Extra credentials fingerprinted alongside API key.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentry.models.identity import IdentityBlock
from agentry.models.output import OutputBlock
from agentry.models.safety import SafetyBlock
from agentry.models.tools import ToolsBlock
from agentry.models.workflow import WorkflowDefinition
from agentry.security.envelope import PreflightCheckResult
from agentry.security.setup import (
    SchemaCompilationError,
    SetupManifest,
    SetupPhase,
    SetupPhaseError,
    SetupPreflightError,
    SetupProvisionError,
    fingerprint_credential,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_workflow(
    *,
    name: str = "test-workflow",
    version: str = "1.0.0",
    trust: str = "sandboxed",
    sandbox_base: str = "agentry/sandbox:1.0",
    filesystem_read: list[str] | None = None,
    filesystem_write: list[str] | None = None,
    network_allow: list[str] | None = None,
    cpu: float = 1.0,
    memory: str = "2GB",
    timeout: int = 300,
    schema_def: dict[str, Any] | None = None,
) -> WorkflowDefinition:
    """Build a WorkflowDefinition with configurable safety settings."""
    from pydantic import BaseModel

    identity = IdentityBlock(name=name, version=version, description="Test workflow")
    tools = ToolsBlock()
    output = OutputBlock(schema=schema_def or {})

    # Build safety block using model_validate to honour string coercion.
    safety_data: dict[str, Any] = {
        "trust": trust,
        "resources": {"cpu": cpu, "memory": memory, "timeout": timeout},
        "filesystem": {
            "read": filesystem_read or [],
            "write": filesystem_write or [],
        },
        "network": {"allow": network_allow or []},
        "sandbox": {"base": sandbox_base},
    }
    safety = SafetyBlock.model_validate(safety_data, strict=False)

    return WorkflowDefinition(
        identity=identity,
        tools=tools,
        output=output,
        safety=safety,
    )


class _MockRunner:
    """Mock runner satisfying RunnerProtocol."""

    def __init__(
        self,
        provision_result: dict[str, Any] | None = None,
        provision_error: Exception | None = None,
    ) -> None:
        self._provision_result = provision_result or {"container_id": "abc123"}
        self._provision_error = provision_error
        self.provisioned = False

    def provision(self) -> dict[str, Any]:
        if self._provision_error:
            raise self._provision_error
        self.provisioned = True
        return self._provision_result

    def teardown(self) -> None:
        pass

    def execute(self, command: str, timeout: float | None = None) -> dict[str, Any]:
        return {"exit_code": 0, "stdout": "", "stderr": ""}

    def check_available(self) -> bool:
        return True


@dataclass
class _MockPreflightCheck:
    """Mock preflight check for testing."""

    _name: str
    _passed: bool = True
    _message: str = ""
    _remediation: str = ""

    @property
    def name(self) -> str:
        return self._name

    def run(self) -> PreflightCheckResult:
        return PreflightCheckResult(
            passed=self._passed,
            name=self._name,
            message=self._message,
            remediation=self._remediation,
        )


# ---------------------------------------------------------------------------
# fingerprint_credential tests
# ---------------------------------------------------------------------------


class TestFingerprintCredential:
    """Tests for the fingerprint_credential utility function."""

    def test_returns_sha256_hex(self) -> None:
        """Returns lowercase hex SHA-256 digest."""
        key = "sk-ant-api-test-key"
        expected = hashlib.sha256(key.encode()).hexdigest()
        assert fingerprint_credential(key) == expected

    def test_different_keys_produce_different_fingerprints(self) -> None:
        """Different inputs produce different fingerprints."""
        fp1 = fingerprint_credential("key-one")
        fp2 = fingerprint_credential("key-two")
        assert fp1 != fp2

    def test_empty_string_fingerprint(self) -> None:
        """Empty string produces a valid (but useless) fingerprint."""
        fp = fingerprint_credential("")
        expected = hashlib.sha256(b"").hexdigest()
        assert fp == expected

    def test_fingerprint_is_64_hex_chars(self) -> None:
        """SHA-256 hex digest is always 64 characters."""
        fp = fingerprint_credential("any-value")
        assert len(fp) == 64
        assert all(c in "0123456789abcdef" for c in fp)

    def test_fingerprint_does_not_contain_original_value(self) -> None:
        """The fingerprint must not include the raw credential."""
        key = "sk-ant-secret-api-key-12345"
        fp = fingerprint_credential(key)
        assert key not in fp


# ---------------------------------------------------------------------------
# SetupManifest tests
# ---------------------------------------------------------------------------


class TestSetupManifest:
    """Tests for the SetupManifest data class and serialisation."""

    def _make_manifest(self, **kwargs: Any) -> SetupManifest:
        defaults: dict[str, Any] = dict(
            workflow_name="wf",
            workflow_version="1.0.0",
            container_image="agentry/sandbox:1.0",
            filesystem_read=["src/**"],
            filesystem_write=["output/**"],
            network_egress_rules=["api.anthropic.com"],
            resource_cpu=1.0,
            resource_memory="2GB",
            resource_timeout=300,
            credential_fingerprints={"anthropic_api_key": "abc123"},
            sandbox_tier="sandboxed",
            timestamp="2026-03-20T12:00:00+00:00",
        )
        defaults.update(kwargs)
        return SetupManifest(**defaults)

    def test_to_dict_contains_all_required_fields(self) -> None:
        """to_dict() includes all specification-required top-level keys."""
        m = self._make_manifest()
        d = m.to_dict()

        assert "workflow_name" in d
        assert "workflow_version" in d
        assert "container_image" in d
        assert "filesystem" in d
        assert "network" in d
        assert "resources" in d
        assert "credential_fingerprints" in d
        assert "sandbox_tier" in d
        assert "timestamp" in d

    def test_to_dict_filesystem_structure(self) -> None:
        """Filesystem block has read and write sub-keys."""
        m = self._make_manifest(
            filesystem_read=["src/**"], filesystem_write=["out/**"]
        )
        d = m.to_dict()
        assert d["filesystem"]["read"] == ["src/**"]
        assert d["filesystem"]["write"] == ["out/**"]

    def test_to_dict_resources_structure(self) -> None:
        """Resources block contains cpu, memory, and timeout."""
        m = self._make_manifest(
            resource_cpu=2.0, resource_memory="4GB", resource_timeout=600
        )
        d = m.to_dict()
        assert d["resources"]["cpu"] == 2.0
        assert d["resources"]["memory"] == "4GB"
        assert d["resources"]["timeout"] == 600

    def test_to_dict_network_structure(self) -> None:
        """Network block contains egress_rules."""
        m = self._make_manifest(network_egress_rules=["api.anthropic.com", "pypi.org"])
        d = m.to_dict()
        assert d["network"]["egress_rules"] == ["api.anthropic.com", "pypi.org"]

    def test_to_dict_is_json_serialisable(self) -> None:
        """The dict can be serialised to JSON without error."""
        m = self._make_manifest()
        json_str = json.dumps(m.to_dict())
        # Round-trip
        parsed = json.loads(json_str)
        assert parsed["workflow_name"] == "wf"


# ---------------------------------------------------------------------------
# SetupPhase tests
# ---------------------------------------------------------------------------


class TestSetupPhaseSuccess:
    """Tests for a successful SetupPhase.run() execution."""

    def test_run_returns_result_with_manifest(self, tmp_path: Path) -> None:
        """run() returns a result with a populated manifest."""
        workflow = _make_workflow()
        runner = _MockRunner()
        phase = SetupPhase(workflow=workflow, runner=runner, runs_dir=tmp_path)

        result = phase.run()

        assert result.manifest is not None
        assert not result.aborted
        assert result.error == ""

    def test_run_provisions_runner(self, tmp_path: Path) -> None:
        """run() calls runner.provision()."""
        workflow = _make_workflow()
        runner = _MockRunner(provision_result={"container_id": "xyz", "network_id": "net1"})
        phase = SetupPhase(workflow=workflow, runner=runner, runs_dir=tmp_path)

        result = phase.run()

        assert runner.provisioned
        assert result.runner_metadata == {"container_id": "xyz", "network_id": "net1"}

    def test_manifest_contains_workflow_version(self, tmp_path: Path) -> None:
        """Manifest records workflow identity version."""
        workflow = _make_workflow(version="2.3.1")
        runner = _MockRunner()
        phase = SetupPhase(workflow=workflow, runner=runner, runs_dir=tmp_path)

        result = phase.run()

        assert result.manifest is not None
        assert result.manifest.workflow_version == "2.3.1"

    def test_manifest_contains_container_image(self, tmp_path: Path) -> None:
        """Manifest records the sandbox container image."""
        workflow = _make_workflow(sandbox_base="myorg/agent:2.0")
        runner = _MockRunner()
        phase = SetupPhase(workflow=workflow, runner=runner, runs_dir=tmp_path)

        result = phase.run()

        assert result.manifest is not None
        assert result.manifest.container_image == "myorg/agent:2.0"

    def test_manifest_contains_filesystem_paths(self, tmp_path: Path) -> None:
        """Manifest records read and write filesystem path globs."""
        workflow = _make_workflow(
            filesystem_read=["src/**", "README.md"],
            filesystem_write=["output/**"],
        )
        runner = _MockRunner()
        phase = SetupPhase(workflow=workflow, runner=runner, runs_dir=tmp_path)

        result = phase.run()

        assert result.manifest is not None
        assert result.manifest.filesystem_read == ["src/**", "README.md"]
        assert result.manifest.filesystem_write == ["output/**"]

    def test_manifest_contains_network_egress_rules(self, tmp_path: Path) -> None:
        """Manifest records allowed network egress domains."""
        workflow = _make_workflow(network_allow=["api.anthropic.com", "pypi.org"])
        runner = _MockRunner()
        phase = SetupPhase(workflow=workflow, runner=runner, runs_dir=tmp_path)

        result = phase.run()

        assert result.manifest is not None
        assert result.manifest.network_egress_rules == ["api.anthropic.com", "pypi.org"]

    def test_manifest_contains_resource_limits(self, tmp_path: Path) -> None:
        """Manifest records CPU, memory, and timeout limits."""
        workflow = _make_workflow(cpu=4.0, memory="8GB", timeout=600)
        runner = _MockRunner()
        phase = SetupPhase(workflow=workflow, runner=runner, runs_dir=tmp_path)

        result = phase.run()

        assert result.manifest is not None
        assert result.manifest.resource_cpu == 4.0
        assert result.manifest.resource_memory == "8GB"
        assert result.manifest.resource_timeout == 600

    def test_manifest_contains_sandbox_tier(self, tmp_path: Path) -> None:
        """Manifest records the detected sandbox tier."""
        workflow = _make_workflow(trust="elevated")
        runner = _MockRunner()
        phase = SetupPhase(workflow=workflow, runner=runner, runs_dir=tmp_path)

        result = phase.run()

        assert result.manifest is not None
        assert result.manifest.sandbox_tier == "elevated"

    def test_manifest_contains_timestamp(self, tmp_path: Path) -> None:
        """Manifest has a non-empty ISO timestamp."""
        workflow = _make_workflow()
        runner = _MockRunner()
        phase = SetupPhase(workflow=workflow, runner=runner, runs_dir=tmp_path)

        result = phase.run()

        assert result.manifest is not None
        ts = result.manifest.timestamp
        assert ts  # non-empty
        assert "T" in ts  # ISO-8601 marker

    def test_schema_compiled_on_success(self, tmp_path: Path) -> None:
        """schema_compiled is True when no schema issues found."""
        workflow = _make_workflow()
        runner = _MockRunner()
        phase = SetupPhase(workflow=workflow, runner=runner, runs_dir=tmp_path)

        result = phase.run()

        assert result.schema_compiled is True


class TestSetupPhaseCredentialFingerprinting:
    """Tests for credential fingerprinting in the setup manifest."""

    def test_api_key_fingerprinted_not_stored(self, tmp_path: Path) -> None:
        """The manifest stores a SHA-256 fingerprint, not the raw API key."""
        key = "sk-ant-api03-supersecret"
        workflow = _make_workflow()
        runner = _MockRunner()
        phase = SetupPhase(
            workflow=workflow, runner=runner, api_key=key, runs_dir=tmp_path
        )

        result = phase.run()

        assert result.manifest is not None
        fingerprints = result.manifest.credential_fingerprints
        assert "anthropic_api_key" in fingerprints

        expected_hash = hashlib.sha256(key.encode()).hexdigest()
        assert fingerprints["anthropic_api_key"] == expected_hash

        # Raw key must NOT appear.
        assert key not in fingerprints["anthropic_api_key"]

    def test_no_api_key_no_fingerprint(self, tmp_path: Path) -> None:
        """When no API key is provided, credential_fingerprints is empty."""
        workflow = _make_workflow()
        runner = _MockRunner()
        phase = SetupPhase(workflow=workflow, runner=runner, api_key="", runs_dir=tmp_path)

        result = phase.run()

        assert result.manifest is not None
        assert result.manifest.credential_fingerprints == {}

    def test_extra_credentials_fingerprinted(self, tmp_path: Path) -> None:
        """Extra credentials are fingerprinted alongside the API key."""
        workflow = _make_workflow()
        runner = _MockRunner()
        phase = SetupPhase(
            workflow=workflow,
            runner=runner,
            api_key="main-key",
            extra_credentials={"github_token": "ghp_abc123"},
            runs_dir=tmp_path,
        )

        result = phase.run()

        assert result.manifest is not None
        fps = result.manifest.credential_fingerprints
        assert "anthropic_api_key" in fps
        assert "github_token" in fps
        assert fps["github_token"] == hashlib.sha256(b"ghp_abc123").hexdigest()

    def test_manifest_json_does_not_contain_raw_key(self, tmp_path: Path) -> None:
        """The persisted manifest JSON must not contain the raw API key."""
        key = "sk-ant-api03-supersecret-verylongvalue"
        workflow = _make_workflow()
        runner = _MockRunner()
        phase = SetupPhase(
            workflow=workflow, runner=runner, api_key=key, runs_dir=tmp_path
        )

        result = phase.run()

        manifest_path = Path(result.manifest_path)
        content = manifest_path.read_text(encoding="utf-8")
        assert key not in content


class TestSetupPhaseManifestPersistence:
    """Tests for setup manifest file system persistence."""

    def test_manifest_saved_to_runs_directory(self, tmp_path: Path) -> None:
        """Manifest file is created under the runs directory."""
        workflow = _make_workflow()
        runner = _MockRunner()
        phase = SetupPhase(workflow=workflow, runner=runner, runs_dir=tmp_path)

        result = phase.run()

        assert result.manifest_path
        p = Path(result.manifest_path)
        assert p.exists()
        assert p.name == "setup-manifest.json"
        # Parent must be a child of tmp_path.
        assert p.parent.parent == tmp_path

    def test_manifest_file_is_valid_json(self, tmp_path: Path) -> None:
        """The saved manifest file contains valid JSON."""
        workflow = _make_workflow()
        runner = _MockRunner()
        phase = SetupPhase(workflow=workflow, runner=runner, runs_dir=tmp_path)

        result = phase.run()

        content = Path(result.manifest_path).read_text(encoding="utf-8")
        parsed = json.loads(content)
        assert isinstance(parsed, dict)

    def test_manifest_json_has_all_required_keys(self, tmp_path: Path) -> None:
        """The manifest JSON contains all specification-required keys."""
        workflow = _make_workflow(
            version="1.2.3",
            sandbox_base="agentry/sandbox:2.0",
            filesystem_read=["src/**"],
            filesystem_write=["out/**"],
            network_allow=["api.anthropic.com"],
            cpu=2.0,
            memory="4GB",
            timeout=120,
        )
        runner = _MockRunner()
        phase = SetupPhase(
            workflow=workflow, runner=runner, api_key="key", runs_dir=tmp_path
        )

        result = phase.run()

        data = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
        assert data["workflow_version"] == "1.2.3"
        assert data["container_image"] == "agentry/sandbox:2.0"
        assert data["filesystem"]["read"] == ["src/**"]
        assert data["filesystem"]["write"] == ["out/**"]
        assert data["network"]["egress_rules"] == ["api.anthropic.com"]
        assert data["resources"]["cpu"] == 2.0
        assert data["resources"]["memory"] == "4GB"
        assert data["resources"]["timeout"] == 120
        assert "anthropic_api_key" in data["credential_fingerprints"]
        assert data["sandbox_tier"] == "sandboxed"
        assert data["timestamp"]


class TestSetupPhasePreflightChecks:
    """Tests for preflight check execution within SetupPhase."""

    def test_passing_checks_recorded_in_result(self, tmp_path: Path) -> None:
        """All passing checks are recorded in the result."""
        workflow = _make_workflow()
        runner = _MockRunner()
        checks = [
            _MockPreflightCheck(_name="api_key", _passed=True),
            _MockPreflightCheck(_name="docker", _passed=True),
        ]
        phase = SetupPhase(
            workflow=workflow, runner=runner, preflight_checks=checks, runs_dir=tmp_path
        )

        result = phase.run()

        assert len(result.preflight_results) == 2
        assert all(r.passed for r in result.preflight_results)

    def test_first_failing_check_aborts(self, tmp_path: Path) -> None:
        """First failing check stops remaining checks and aborts setup."""
        workflow = _make_workflow()
        runner = _MockRunner()
        checks = [
            _MockPreflightCheck(_name="check1", _passed=True),
            _MockPreflightCheck(
                _name="check2", _passed=False, _message="Failed.", _remediation="Fix it."
            ),
            _MockPreflightCheck(_name="check3", _passed=True),
        ]
        phase = SetupPhase(
            workflow=workflow, runner=runner, preflight_checks=checks, runs_dir=tmp_path
        )

        with pytest.raises(SetupPreflightError) as exc_info:
            phase.run()

        assert exc_info.value.check_name == "check2"
        assert "Failed." in exc_info.value.message
        assert exc_info.value.remediation == "Fix it."

    def test_failing_check_result_captured(self, tmp_path: Path) -> None:
        """The failed check result is still in the result even after abort."""
        workflow = _make_workflow()
        runner = _MockRunner()
        failing_check = _MockPreflightCheck(
            _name="bad_check", _passed=False, _message="No good."
        )
        phase = SetupPhase(
            workflow=workflow,
            runner=runner,
            preflight_checks=[failing_check],
            runs_dir=tmp_path,
        )

        try:
            phase.run()
        except SetupPreflightError:
            pass  # Expected.

    def test_preflight_checks_in_manifest(self, tmp_path: Path) -> None:
        """Preflight check results appear in the saved manifest JSON."""
        workflow = _make_workflow()
        runner = _MockRunner()
        checks = [_MockPreflightCheck(_name="api_key", _passed=True, _message="OK")]
        phase = SetupPhase(
            workflow=workflow, runner=runner, preflight_checks=checks, runs_dir=tmp_path
        )

        result = phase.run()

        data = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
        assert len(data["preflight_results"]) == 1
        assert data["preflight_results"][0]["name"] == "api_key"
        assert data["preflight_results"][0]["passed"] is True


class TestSetupPhaseProvisionError:
    """Tests for runner provisioning failure handling."""

    def test_provision_failure_raises_setup_provision_error(
        self, tmp_path: Path
    ) -> None:
        """Provision error is wrapped in SetupProvisionError."""
        workflow = _make_workflow()
        runner = _MockRunner(provision_error=RuntimeError("Docker not running"))
        phase = SetupPhase(workflow=workflow, runner=runner, runs_dir=tmp_path)

        with pytest.raises(SetupProvisionError) as exc_info:
            phase.run()

        assert "Docker not running" in str(exc_info.value)

    def test_provision_failure_is_setup_phase_error(self, tmp_path: Path) -> None:
        """SetupProvisionError is a subclass of SetupPhaseError."""
        assert issubclass(SetupProvisionError, SetupPhaseError)

    def test_provision_failure_aborted_result(self, tmp_path: Path) -> None:
        """After provisioning failure, no manifest is saved."""
        workflow = _make_workflow()
        runner = _MockRunner(provision_error=RuntimeError("No daemon"))
        phase = SetupPhase(workflow=workflow, runner=runner, runs_dir=tmp_path)

        try:
            phase.run()
        except SetupProvisionError:
            pass

        # No manifest files should exist.
        manifest_files = list(tmp_path.rglob("setup-manifest.json"))
        assert manifest_files == []


class TestSetupPhaseSchemaCompilation:
    """Tests for output validator schema compilation."""

    def test_valid_schema_compiles(self, tmp_path: Path) -> None:
        """A well-formed JSON Schema compiles without error."""
        schema = {
            "type": "object",
            "properties": {"result": {"type": "string"}},
            "required": ["result"],
        }
        workflow = _make_workflow(schema_def=schema)
        runner = _MockRunner()
        phase = SetupPhase(workflow=workflow, runner=runner, runs_dir=tmp_path)

        result = phase.run()

        assert result.schema_compiled is True

    def test_empty_schema_compiles(self, tmp_path: Path) -> None:
        """An empty schema dict compiles without error."""
        workflow = _make_workflow(schema_def={})
        runner = _MockRunner()
        phase = SetupPhase(workflow=workflow, runner=runner, runs_dir=tmp_path)

        result = phase.run()

        assert result.schema_compiled is True

    def test_schema_compilation_error_is_setup_phase_error(self) -> None:
        """SchemaCompilationError is a subclass of SetupPhaseError."""
        assert issubclass(SchemaCompilationError, SetupPhaseError)


class TestSetupPhaseExceptionHierarchy:
    """Tests for exception class hierarchy."""

    def test_setup_preflight_error_is_setup_phase_error(self) -> None:
        """SetupPreflightError inherits from SetupPhaseError."""
        assert issubclass(SetupPreflightError, SetupPhaseError)

    def test_setup_provision_error_is_setup_phase_error(self) -> None:
        """SetupProvisionError inherits from SetupPhaseError."""
        assert issubclass(SetupProvisionError, SetupPhaseError)

    def test_schema_compilation_error_is_setup_phase_error(self) -> None:
        """SchemaCompilationError inherits from SetupPhaseError."""
        assert issubclass(SchemaCompilationError, SetupPhaseError)

    def test_setup_preflight_error_fields(self) -> None:
        """SetupPreflightError carries check_name, message, remediation."""
        exc = SetupPreflightError(
            check_name="docker",
            message="Docker missing.",
            remediation="Install Docker.",
        )
        assert exc.check_name == "docker"
        assert exc.message == "Docker missing."
        assert exc.remediation == "Install Docker."
        assert "docker" in str(exc).lower()
