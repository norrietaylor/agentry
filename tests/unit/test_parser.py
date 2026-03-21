"""Unit tests for T01.3: YAML parser and validation error reporting.

Tests cover:
- load_workflow_file: parses valid YAML into a WorkflowDefinition.
- load_workflow_file: raises WorkflowLoadError on validation failures.
- load_workflow_file: raises FileNotFoundError for missing files.
- validate_workflow_file: returns [] for valid files.
- validate_workflow_file: returns list of error strings for invalid files.
- Error messages include file path, field path, and remediation hint.
- Unknown keys report the key path (e.g. model.extra_field).
- Missing discriminator field error is reported.
- Unresolved variable references are reported.
- Semver validation errors are reported.
- CLI validate command exits 0 on success and 1 on failure.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from agentry.cli import main as cli
from agentry.models.workflow import WorkflowDefinition
from agentry.parser import WorkflowLoadError, load_workflow_file, validate_workflow_file

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
VALID_WORKFLOW = str(FIXTURES_DIR / "valid-workflow.yaml")
INVALID_WORKFLOW = str(FIXTURES_DIR / "invalid-workflow.yaml")


def _write_yaml(tmp_path: Path, name: str, data: dict) -> str:
    """Write a dict as YAML and return the file path string."""
    p = tmp_path / name
    p.write_text(yaml.dump(data))
    return str(p)


def _minimal_data() -> dict:
    return {
        "identity": {
            "name": "test",
            "version": "1.0.0",
            "description": "A test workflow",
        }
    }


# ---------------------------------------------------------------------------
# load_workflow_file: success
# ---------------------------------------------------------------------------


class TestLoadWorkflowFileSuccess:
    def test_valid_fixture_returns_workflow_definition(self) -> None:
        wf = load_workflow_file(VALID_WORKFLOW)
        assert isinstance(wf, WorkflowDefinition)
        assert wf.identity.name == "code-review"
        assert wf.identity.version == "1.0.0"

    def test_minimal_valid_workflow(self, tmp_path: Path) -> None:
        path = _write_yaml(tmp_path, "min.yaml", _minimal_data())
        wf = load_workflow_file(path)
        assert wf.identity.name == "test"

    def test_all_seven_blocks_parsed(self) -> None:
        wf = load_workflow_file(VALID_WORKFLOW)
        assert wf.identity is not None
        assert wf.inputs is not None
        assert wf.tools is not None
        assert wf.model is not None
        assert wf.safety is not None
        assert wf.output is not None
        assert wf.composition is not None

    def test_inputs_typed_correctly(self) -> None:
        from agentry.models.inputs import GitDiffInput, RepositoryRefInput

        wf = load_workflow_file(VALID_WORKFLOW)
        assert isinstance(wf.inputs["diff"], GitDiffInput)
        assert isinstance(wf.inputs["repo"], RepositoryRefInput)


# ---------------------------------------------------------------------------
# load_workflow_file: failure
# ---------------------------------------------------------------------------


class TestLoadWorkflowFileFailure:
    def test_missing_file_raises_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_workflow_file("/nonexistent/path/workflow.yaml")

    def test_invalid_yaml_raises_workflow_load_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("identity: [unclosed bracket")
        with pytest.raises(WorkflowLoadError) as exc_info:
            load_workflow_file(str(bad))
        assert "YAML parse error" in exc_info.value.errors[0]

    def test_non_mapping_yaml_raises_workflow_load_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "list.yaml"
        bad.write_text("- item1\n- item2\n")
        with pytest.raises(WorkflowLoadError) as exc_info:
            load_workflow_file(str(bad))
        assert "mapping" in exc_info.value.errors[0].lower()

    def test_invalid_semver_raises_workflow_load_error(self, tmp_path: Path) -> None:
        data = _minimal_data()
        data["identity"]["version"] = "not-a-version"
        path = _write_yaml(tmp_path, "bad_ver.yaml", data)
        with pytest.raises(WorkflowLoadError) as exc_info:
            load_workflow_file(path)
        assert len(exc_info.value.errors) >= 1

    def test_unknown_key_raises_workflow_load_error(self, tmp_path: Path) -> None:
        data = _minimal_data()
        data["model"] = {"provider": "anthropic", "extra_field": "bad"}
        path = _write_yaml(tmp_path, "extra.yaml", data)
        with pytest.raises(WorkflowLoadError) as exc_info:
            load_workflow_file(path)
        assert len(exc_info.value.errors) >= 1

    def test_unresolved_variable_raises_workflow_load_error(self, tmp_path: Path) -> None:
        data = _minimal_data()
        data["model"] = {"system_prompt": "Use $undefined_var here"}
        path = _write_yaml(tmp_path, "unresolved.yaml", data)
        with pytest.raises(WorkflowLoadError) as exc_info:
            load_workflow_file(path)
        assert len(exc_info.value.errors) >= 1

    def test_workflow_load_error_has_path(self, tmp_path: Path) -> None:
        data = _minimal_data()
        data["identity"]["version"] = "bad"
        path = _write_yaml(tmp_path, "bad.yaml", data)
        with pytest.raises(WorkflowLoadError) as exc_info:
            load_workflow_file(path)
        assert exc_info.value.path == path


# ---------------------------------------------------------------------------
# validate_workflow_file: return values
# ---------------------------------------------------------------------------


class TestValidateWorkflowFile:
    def test_valid_fixture_returns_empty_list(self) -> None:
        errors = validate_workflow_file(VALID_WORKFLOW)
        assert errors == []

    def test_invalid_fixture_returns_non_empty_list(self) -> None:
        errors = validate_workflow_file(INVALID_WORKFLOW)
        assert len(errors) >= 1

    def test_missing_file_returns_error_list(self) -> None:
        errors = validate_workflow_file("/no/such/file.yaml")
        assert len(errors) == 1
        assert "file_not_found" in errors[0] or "not found" in errors[0].lower()

    def test_unknown_key_error_contains_field_path(self, tmp_path: Path) -> None:
        data = _minimal_data()
        data["model"] = {"provider": "anthropic", "extra_field": "bad"}
        path = _write_yaml(tmp_path, "extra.yaml", data)
        errors = validate_workflow_file(path)
        assert len(errors) >= 1
        combined = "\n".join(errors)
        assert "model" in combined
        assert "extra_field" in combined

    def test_unknown_key_error_contains_file_path(self, tmp_path: Path) -> None:
        data = _minimal_data()
        data["model"] = {"extra_field": "bad"}
        path = _write_yaml(tmp_path, "bad.yaml", data)
        errors = validate_workflow_file(path)
        combined = "\n".join(errors)
        assert path in combined

    def test_error_contains_remediation_hint(self, tmp_path: Path) -> None:
        data = _minimal_data()
        data["model"] = {"extra_field": "bad"}
        path = _write_yaml(tmp_path, "hint.yaml", data)
        errors = validate_workflow_file(path)
        combined = "\n".join(errors)
        assert "hint:" in combined

    def test_semver_error_reported(self, tmp_path: Path) -> None:
        data = _minimal_data()
        data["identity"]["version"] = "not-a-version"
        path = _write_yaml(tmp_path, "semver.yaml", data)
        errors = validate_workflow_file(path)
        assert len(errors) >= 1
        combined = "\n".join(errors)
        assert "identity" in combined

    def test_missing_discriminator_reported(self, tmp_path: Path) -> None:
        data = _minimal_data()
        data["inputs"] = {"diff": {"ref": "HEAD~1"}}  # missing 'type'
        path = _write_yaml(tmp_path, "nodisc.yaml", data)
        errors = validate_workflow_file(path)
        assert len(errors) >= 1

    def test_unresolved_variable_reported(self, tmp_path: Path) -> None:
        data = _minimal_data()
        data["model"] = {"system_prompt": "Use $bad_var"}
        path = _write_yaml(tmp_path, "var.yaml", data)
        errors = validate_workflow_file(path)
        assert len(errors) >= 1
        combined = "\n".join(errors)
        assert "bad_var" in combined or "Unresolved" in combined

    def test_multiple_errors_reported(self, tmp_path: Path) -> None:
        # The invalid fixture has both a semver error and an extra_field error.
        errors = validate_workflow_file(INVALID_WORKFLOW)
        assert len(errors) >= 2

    def test_error_format_structure(self, tmp_path: Path) -> None:
        data = _minimal_data()
        data["model"] = {"extra_field": "bad"}
        path = _write_yaml(tmp_path, "struct.yaml", data)
        errors = validate_workflow_file(path)
        # Each error string should follow the pattern: "error[type]: path: field"
        assert len(errors) >= 1
        assert errors[0].startswith("error[")


# ---------------------------------------------------------------------------
# CLI integration: agentry validate
# ---------------------------------------------------------------------------


class TestCLIValidate:
    def test_valid_workflow_exits_0(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", VALID_WORKFLOW])
        assert result.exit_code == 0

    def test_valid_workflow_prints_success_to_stdout(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--output-format", "text", "validate", VALID_WORKFLOW])
        assert "Validation successful" in result.output

    def test_invalid_workflow_exits_1(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", INVALID_WORKFLOW])
        assert result.exit_code == 1

    def test_missing_file_exits_1(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", "/no/such/workflow.yaml"])
        assert result.exit_code == 1

    def test_valid_workflow_json_output(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["--output-format", "json", "validate", VALID_WORKFLOW])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "valid"
        assert "path" in data

    def test_invalid_workflow_errors_contain_field_path(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", INVALID_WORKFLOW], catch_exceptions=False)
        assert result.exit_code == 1
        # Errors go to stderr; with CliRunner mix_stderr=True (default), they're in output
        assert "model" in result.output or "model" in (result.exception or "")

    def test_invalid_workflow_errors_contain_remediation(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli, ["validate", INVALID_WORKFLOW], catch_exceptions=False)
        assert result.exit_code == 1
        assert "hint:" in result.output
