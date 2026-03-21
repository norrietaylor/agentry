"""Unit tests for T05.4: 'agentry validate --security-audit' for workflow diffing.

Tests cover:
- security_audit() detects trust level differences between two workflow versions.
- security_audit() detects network allowlist differences.
- security_audit() detects side_effects differences.
- security_audit() detects output_paths differences.
- security_audit() detects filesystem read/write differences.
- security_audit() reports no diffs when security-relevant fields are identical.
- security_audit() warns when either workflow lacks a signature.
- security_audit() warns when both workflows lack signatures.
- security_audit() raises FileNotFoundError for missing workflow path.
- security_audit() raises ValueError for invalid YAML.
- security_audit_single() warns when a single workflow lacks a signature.
- security_audit_single() does not warn for a signed workflow.
- SecurityAuditReport.has_differences is True when diffs exist.
- SecurityAuditReport.format_text() includes field names and values.
- SecurityAuditReport.format_json() is JSON-serialisable and has expected keys.
- 'agentry validate --security-audit PATH' CLI command exits 0.
- 'agentry validate --security-audit PATH1 PATH2' CLI outputs diff of trust level.
- 'agentry validate --security-audit PATH1 PATH2' CLI outputs diff of network allow.
- 'agentry validate --security-audit PATH' warns about unsigned workflow.
- 'agentry validate --security-audit PATH1 PATH2' JSON output has expected structure.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from agentry.cli import main
from agentry.security.audit import (
    FieldDiff,
    SecurityAuditReport,
    _get_nested,
    security_audit,
    security_audit_single,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_workflow(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        yaml.dump(data, fh, default_flow_style=False, sort_keys=False, allow_unicode=True)


_WORKFLOW_V1: dict[str, Any] = {
    "identity": {"name": "test-workflow", "version": "1.0.0"},
    "safety": {
        "trust": "sandboxed",
        "network": {"allow": ["api.example.com"]},
        "filesystem": {"read": ["./src/**"], "write": ["./out/**"]},
    },
    "output": {
        "side_effects": [{"type": "git-commit", "description": "Write commit"}],
        "output_paths": ["./out/report.json"],
    },
}

_WORKFLOW_V2_TRUST_CHANGED: dict[str, Any] = {
    "identity": {"name": "test-workflow", "version": "2.0.0"},
    "safety": {
        "trust": "elevated",
        "network": {"allow": ["api.example.com"]},
        "filesystem": {"read": ["./src/**"], "write": ["./out/**"]},
    },
    "output": {
        "side_effects": [{"type": "git-commit", "description": "Write commit"}],
        "output_paths": ["./out/report.json"],
    },
}

_WORKFLOW_V2_NETWORK_CHANGED: dict[str, Any] = {
    "identity": {"name": "test-workflow", "version": "2.0.0"},
    "safety": {
        "trust": "sandboxed",
        "network": {"allow": ["api.example.com", "extra.example.com"]},
        "filesystem": {"read": ["./src/**"], "write": ["./out/**"]},
    },
    "output": {
        "side_effects": [{"type": "git-commit", "description": "Write commit"}],
        "output_paths": ["./out/report.json"],
    },
}

_WORKFLOW_V2_SIDE_EFFECTS_CHANGED: dict[str, Any] = {
    "identity": {"name": "test-workflow", "version": "2.0.0"},
    "safety": {
        "trust": "sandboxed",
        "network": {"allow": ["api.example.com"]},
        "filesystem": {"read": ["./src/**"], "write": ["./out/**"]},
    },
    "output": {
        "side_effects": [
            {"type": "git-commit", "description": "Write commit"},
            {"type": "file-write", "description": "Extra side effect"},
        ],
        "output_paths": ["./out/report.json"],
    },
}

_WORKFLOW_SIGNED: dict[str, Any] = {
    "identity": {"name": "signed-workflow", "version": "1.0.0"},
    "safety": {"trust": "sandboxed"},
    "signature": {
        "algorithm": "ed25519",
        "signed_blocks": ["safety", "output.side_effects"],
        "signature": "aabbcc",
        "timestamp": "2026-01-01T00:00:00Z",
    },
}


# ---------------------------------------------------------------------------
# _get_nested helper tests
# ---------------------------------------------------------------------------


class TestGetNested:
    def test_retrieves_top_level_key(self) -> None:
        data = {"safety": {"trust": "sandboxed"}}
        assert _get_nested(data, "safety") == {"trust": "sandboxed"}

    def test_retrieves_nested_key(self) -> None:
        data = {"safety": {"trust": "sandboxed"}}
        assert _get_nested(data, "safety.trust") == "sandboxed"

    def test_returns_none_for_missing_key(self) -> None:
        data: dict[str, Any] = {}
        assert _get_nested(data, "safety.trust") is None

    def test_returns_none_when_intermediate_not_dict(self) -> None:
        data = {"safety": "not-a-dict"}
        assert _get_nested(data, "safety.trust") is None

    def test_retrieves_deeply_nested_key(self) -> None:
        data = {"a": {"b": {"c": 42}}}
        assert _get_nested(data, "a.b.c") == 42


# ---------------------------------------------------------------------------
# SecurityAuditReport tests
# ---------------------------------------------------------------------------


class TestSecurityAuditReport:
    def test_has_differences_true_when_diffs_exist(self) -> None:
        report = SecurityAuditReport(
            path1="v1.yaml",
            path2="v2.yaml",
            diffs=[FieldDiff(field="safety.trust", old_value="sandboxed", new_value="elevated")],
        )
        assert report.has_differences is True

    def test_has_differences_false_when_no_diffs(self) -> None:
        report = SecurityAuditReport(path1="v1.yaml", path2="v2.yaml")
        assert report.has_differences is False

    def test_has_warnings_true_when_unsigned_warning(self) -> None:
        report = SecurityAuditReport(
            path1="v1.yaml",
            path2="v2.yaml",
            unsigned_warning="Workflow is not signed.",
        )
        assert report.has_warnings is True

    def test_has_warnings_false_when_no_warnings(self) -> None:
        report = SecurityAuditReport(path1="v1.yaml", path2="v2.yaml")
        assert report.has_warnings is False

    def test_format_text_includes_path_header(self) -> None:
        report = SecurityAuditReport(path1="v1.yaml", path2="v2.yaml")
        text = report.format_text()
        assert "v1.yaml" in text
        assert "v2.yaml" in text

    def test_format_text_includes_field_name_when_diffs_exist(self) -> None:
        report = SecurityAuditReport(
            path1="v1.yaml",
            path2="v2.yaml",
            diffs=[FieldDiff(field="safety.trust", old_value="sandboxed", new_value="elevated")],
        )
        text = report.format_text()
        assert "safety.trust" in text

    def test_format_text_includes_old_value(self) -> None:
        report = SecurityAuditReport(
            path1="v1.yaml",
            path2="v2.yaml",
            diffs=[FieldDiff(field="safety.trust", old_value="sandboxed", new_value="elevated")],
        )
        text = report.format_text()
        assert "sandboxed" in text

    def test_format_text_includes_new_value(self) -> None:
        report = SecurityAuditReport(
            path1="v1.yaml",
            path2="v2.yaml",
            diffs=[FieldDiff(field="safety.trust", old_value="sandboxed", new_value="elevated")],
        )
        text = report.format_text()
        assert "elevated" in text

    def test_format_text_includes_unsigned_warning(self) -> None:
        report = SecurityAuditReport(
            path1="v1.yaml",
            path2="v2.yaml",
            unsigned_warning="Workflow v1.yaml is not signed.",
        )
        text = report.format_text()
        assert "WARNING" in text
        assert "not signed" in text.lower()

    def test_format_text_no_differences_message(self) -> None:
        report = SecurityAuditReport(path1="v1.yaml", path2="v2.yaml")
        text = report.format_text()
        assert "No security-relevant field changes detected." in text

    def test_format_json_has_expected_keys(self) -> None:
        report = SecurityAuditReport(path1="v1.yaml", path2="v2.yaml")
        data = report.format_json()
        for key in ("path1", "path2", "has_differences", "diffs", "unsigned_warning", "warnings"):
            assert key in data, f"Missing key: {key}"

    def test_format_json_is_serialisable(self) -> None:
        report = SecurityAuditReport(
            path1="v1.yaml",
            path2="v2.yaml",
            diffs=[FieldDiff(field="safety.trust", old_value="sandboxed", new_value="elevated")],
        )
        # Should not raise
        json.dumps(report.format_json())

    def test_format_json_diffs_contain_field_and_values(self) -> None:
        report = SecurityAuditReport(
            path1="v1.yaml",
            path2="v2.yaml",
            diffs=[FieldDiff(field="safety.trust", old_value="sandboxed", new_value="elevated")],
        )
        data = report.format_json()
        assert len(data["diffs"]) == 1
        d = data["diffs"][0]
        assert d["field"] == "safety.trust"
        assert d["old_value"] == "sandboxed"
        assert d["new_value"] == "elevated"


# ---------------------------------------------------------------------------
# security_audit() function tests
# ---------------------------------------------------------------------------


class TestSecurityAudit:
    def test_detects_trust_level_change(self, tmp_path: Path) -> None:
        v1 = tmp_path / "v1.yaml"
        v2 = tmp_path / "v2.yaml"
        _write_workflow(v1, _WORKFLOW_V1)
        _write_workflow(v2, _WORKFLOW_V2_TRUST_CHANGED)
        report = security_audit(v1, v2)
        fields = [d.field for d in report.diffs]
        assert "safety.trust" in fields

    def test_trust_level_old_value_is_sandboxed(self, tmp_path: Path) -> None:
        v1 = tmp_path / "v1.yaml"
        v2 = tmp_path / "v2.yaml"
        _write_workflow(v1, _WORKFLOW_V1)
        _write_workflow(v2, _WORKFLOW_V2_TRUST_CHANGED)
        report = security_audit(v1, v2)
        trust_diff = next(d for d in report.diffs if d.field == "safety.trust")
        assert trust_diff.old_value == "sandboxed"

    def test_trust_level_new_value_is_elevated(self, tmp_path: Path) -> None:
        v1 = tmp_path / "v1.yaml"
        v2 = tmp_path / "v2.yaml"
        _write_workflow(v1, _WORKFLOW_V1)
        _write_workflow(v2, _WORKFLOW_V2_TRUST_CHANGED)
        report = security_audit(v1, v2)
        trust_diff = next(d for d in report.diffs if d.field == "safety.trust")
        assert trust_diff.new_value == "elevated"

    def test_detects_network_allowlist_change(self, tmp_path: Path) -> None:
        v1 = tmp_path / "v1.yaml"
        v2 = tmp_path / "v2.yaml"
        _write_workflow(v1, _WORKFLOW_V1)
        _write_workflow(v2, _WORKFLOW_V2_NETWORK_CHANGED)
        report = security_audit(v1, v2)
        fields = [d.field for d in report.diffs]
        assert "safety.network.allow" in fields

    def test_detects_side_effects_change(self, tmp_path: Path) -> None:
        v1 = tmp_path / "v1.yaml"
        v2 = tmp_path / "v2.yaml"
        _write_workflow(v1, _WORKFLOW_V1)
        _write_workflow(v2, _WORKFLOW_V2_SIDE_EFFECTS_CHANGED)
        report = security_audit(v1, v2)
        fields = [d.field for d in report.diffs]
        assert "output.side_effects" in fields

    def test_detects_output_paths_change(self, tmp_path: Path) -> None:
        v1 = tmp_path / "v1.yaml"
        v2 = tmp_path / "v2.yaml"
        _write_workflow(v1, _WORKFLOW_V1)
        v2_data = dict(_WORKFLOW_V1)
        v2_output = dict(_WORKFLOW_V1.get("output", {}))
        v2_output["output_paths"] = ["./out/report.json", "./out/extra.json"]
        v2_data["output"] = v2_output
        _write_workflow(v2, v2_data)
        report = security_audit(v1, v2)
        fields = [d.field for d in report.diffs]
        assert "output.output_paths" in fields

    def test_detects_filesystem_read_change(self, tmp_path: Path) -> None:
        v1 = tmp_path / "v1.yaml"
        v2 = tmp_path / "v2.yaml"
        _write_workflow(v1, _WORKFLOW_V1)
        v2_data = dict(_WORKFLOW_V1)
        v2_safety = dict(_WORKFLOW_V1.get("safety", {}))
        v2_fs = dict(v2_safety.get("filesystem", {}))
        v2_fs["read"] = ["./src/**", "./lib/**"]
        v2_safety["filesystem"] = v2_fs
        v2_data["safety"] = v2_safety
        _write_workflow(v2, v2_data)
        report = security_audit(v1, v2)
        fields = [d.field for d in report.diffs]
        assert "safety.filesystem.read" in fields

    def test_detects_filesystem_write_change(self, tmp_path: Path) -> None:
        v1 = tmp_path / "v1.yaml"
        v2 = tmp_path / "v2.yaml"
        _write_workflow(v1, _WORKFLOW_V1)
        v2_data = dict(_WORKFLOW_V1)
        v2_safety = dict(_WORKFLOW_V1.get("safety", {}))
        v2_fs = dict(v2_safety.get("filesystem", {}))
        v2_fs["write"] = ["./out/**", "/tmp/cache/**"]
        v2_safety["filesystem"] = v2_fs
        v2_data["safety"] = v2_safety
        _write_workflow(v2, v2_data)
        report = security_audit(v1, v2)
        fields = [d.field for d in report.diffs]
        assert "safety.filesystem.write" in fields

    def test_no_diffs_when_security_fields_identical(self, tmp_path: Path) -> None:
        v1 = tmp_path / "v1.yaml"
        v2 = tmp_path / "v2.yaml"
        _write_workflow(v1, _WORKFLOW_V1)
        # v2 differs only in identity.version (non-security field)
        v2_data = dict(_WORKFLOW_V1)
        v2_data["identity"] = {"name": "test-workflow", "version": "1.0.1"}
        _write_workflow(v2, v2_data)
        report = security_audit(v1, v2)
        assert not report.has_differences

    def test_warns_when_first_workflow_unsigned(self, tmp_path: Path) -> None:
        v1 = tmp_path / "v1.yaml"
        v2 = tmp_path / "v2.yaml"
        _write_workflow(v1, _WORKFLOW_V1)  # no signature
        _write_workflow(v2, _WORKFLOW_SIGNED)
        report = security_audit(v1, v2)
        assert report.unsigned_warning
        assert "not signed" in report.unsigned_warning.lower()

    def test_warns_when_second_workflow_unsigned(self, tmp_path: Path) -> None:
        v1 = tmp_path / "v1.yaml"
        v2 = tmp_path / "v2.yaml"
        _write_workflow(v1, _WORKFLOW_SIGNED)
        _write_workflow(v2, _WORKFLOW_V1)  # no signature
        report = security_audit(v1, v2)
        assert report.unsigned_warning
        assert "not signed" in report.unsigned_warning.lower()

    def test_warns_when_both_workflows_unsigned(self, tmp_path: Path) -> None:
        v1 = tmp_path / "v1.yaml"
        v2 = tmp_path / "v2.yaml"
        _write_workflow(v1, _WORKFLOW_V1)
        _write_workflow(v2, _WORKFLOW_V2_TRUST_CHANGED)
        report = security_audit(v1, v2)
        assert report.unsigned_warning
        # Both unsigned: message may say "Neither workflow is signed" or similar
        assert "signed" in report.unsigned_warning.lower()

    def test_no_unsigned_warning_when_both_signed(self, tmp_path: Path) -> None:
        v1 = tmp_path / "v1.yaml"
        v2 = tmp_path / "v2.yaml"
        _write_workflow(v1, _WORKFLOW_SIGNED)
        _write_workflow(v2, _WORKFLOW_SIGNED)
        report = security_audit(v1, v2)
        assert not report.unsigned_warning

    def test_raises_file_not_found_for_missing_path1(self, tmp_path: Path) -> None:
        v2 = tmp_path / "v2.yaml"
        _write_workflow(v2, _WORKFLOW_V1)
        with pytest.raises(FileNotFoundError, match="v1.yaml"):
            security_audit(tmp_path / "v1.yaml", v2)

    def test_raises_file_not_found_for_missing_path2(self, tmp_path: Path) -> None:
        v1 = tmp_path / "v1.yaml"
        _write_workflow(v1, _WORKFLOW_V1)
        with pytest.raises(FileNotFoundError, match="v2.yaml"):
            security_audit(v1, tmp_path / "v2.yaml")

    def test_raises_value_error_for_invalid_yaml(self, tmp_path: Path) -> None:
        v1 = tmp_path / "v1.yaml"
        v2 = tmp_path / "v2.yaml"
        v1.write_text("key: [unclosed")
        _write_workflow(v2, _WORKFLOW_V1)
        with pytest.raises(ValueError, match="Failed to parse"):
            security_audit(v1, v2)

    def test_report_path1_matches_input(self, tmp_path: Path) -> None:
        v1 = tmp_path / "v1.yaml"
        v2 = tmp_path / "v2.yaml"
        _write_workflow(v1, _WORKFLOW_V1)
        _write_workflow(v2, _WORKFLOW_V2_TRUST_CHANGED)
        report = security_audit(v1, v2)
        assert report.path1 == str(v1)

    def test_report_path2_matches_input(self, tmp_path: Path) -> None:
        v1 = tmp_path / "v1.yaml"
        v2 = tmp_path / "v2.yaml"
        _write_workflow(v1, _WORKFLOW_V1)
        _write_workflow(v2, _WORKFLOW_V2_TRUST_CHANGED)
        report = security_audit(v1, v2)
        assert report.path2 == str(v2)


# ---------------------------------------------------------------------------
# security_audit_single() tests
# ---------------------------------------------------------------------------


class TestSecurityAuditSingle:
    def test_warns_when_workflow_lacks_signature(self, tmp_path: Path) -> None:
        path = tmp_path / "workflow.yaml"
        _write_workflow(path, _WORKFLOW_V1)
        report = security_audit_single(path)
        assert report.unsigned_warning
        assert "not signed" in report.unsigned_warning.lower()

    def test_no_warning_when_workflow_is_signed(self, tmp_path: Path) -> None:
        path = tmp_path / "workflow.yaml"
        _write_workflow(path, _WORKFLOW_SIGNED)
        report = security_audit_single(path)
        assert not report.unsigned_warning

    def test_no_diffs_for_single_workflow(self, tmp_path: Path) -> None:
        path = tmp_path / "workflow.yaml"
        _write_workflow(path, _WORKFLOW_V1)
        report = security_audit_single(path)
        assert not report.has_differences

    def test_raises_file_not_found_for_missing_path(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            security_audit_single(tmp_path / "missing.yaml")

    def test_raises_value_error_for_invalid_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("key: [unclosed")
        with pytest.raises(ValueError, match="Failed to parse"):
            security_audit_single(path)


# ---------------------------------------------------------------------------
# 'agentry validate --security-audit' CLI tests
# ---------------------------------------------------------------------------


class TestValidateSecurityAuditCLI:
    """Tests for the 'agentry validate --security-audit' CLI command."""

    def test_security_audit_single_exits_zero(self, tmp_path: Path) -> None:
        path = tmp_path / "workflow.yaml"
        _write_workflow(path, _WORKFLOW_V1)
        runner = CliRunner()
        result = runner.invoke(main, ["validate", "--security-audit", str(path)])
        assert result.exit_code == 0, result.output

    def test_security_audit_single_warns_unsigned(self, tmp_path: Path) -> None:
        path = tmp_path / "workflow.yaml"
        _write_workflow(path, _WORKFLOW_V1)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--output-format", "text", "validate", "--security-audit", str(path)],
        )
        assert result.exit_code == 0, result.output
        assert "not signed" in result.output.lower()

    def test_security_audit_single_no_warning_when_signed(self, tmp_path: Path) -> None:
        path = tmp_path / "workflow.yaml"
        _write_workflow(path, _WORKFLOW_SIGNED)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--output-format", "text", "validate", "--security-audit", str(path)],
        )
        assert result.exit_code == 0, result.output
        assert "not signed" not in result.output.lower()

    def test_security_audit_two_paths_exits_zero(self, tmp_path: Path) -> None:
        v1 = tmp_path / "v1.yaml"
        v2 = tmp_path / "v2.yaml"
        _write_workflow(v1, _WORKFLOW_V1)
        _write_workflow(v2, _WORKFLOW_V2_TRUST_CHANGED)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["validate", "--security-audit", str(v1), str(v2)],
        )
        assert result.exit_code == 0, result.output

    def test_security_audit_two_paths_shows_trust_diff(self, tmp_path: Path) -> None:
        v1 = tmp_path / "v1.yaml"
        v2 = tmp_path / "v2.yaml"
        _write_workflow(v1, _WORKFLOW_V1)
        _write_workflow(v2, _WORKFLOW_V2_TRUST_CHANGED)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--output-format", "text", "validate", "--security-audit", str(v1), str(v2)],
        )
        assert result.exit_code == 0, result.output
        assert "safety.trust" in result.output

    def test_security_audit_two_paths_shows_network_diff(self, tmp_path: Path) -> None:
        v1 = tmp_path / "v1.yaml"
        v2 = tmp_path / "v2.yaml"
        _write_workflow(v1, _WORKFLOW_V1)
        _write_workflow(v2, _WORKFLOW_V2_NETWORK_CHANGED)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--output-format", "text", "validate", "--security-audit", str(v1), str(v2)],
        )
        assert result.exit_code == 0, result.output
        assert "safety.network.allow" in result.output

    def test_security_audit_two_paths_shows_side_effects_diff(self, tmp_path: Path) -> None:
        v1 = tmp_path / "v1.yaml"
        v2 = tmp_path / "v2.yaml"
        _write_workflow(v1, _WORKFLOW_V1)
        _write_workflow(v2, _WORKFLOW_V2_SIDE_EFFECTS_CHANGED)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--output-format", "text", "validate", "--security-audit", str(v1), str(v2)],
        )
        assert result.exit_code == 0, result.output
        assert "output.side_effects" in result.output

    def test_security_audit_two_paths_json_output(self, tmp_path: Path) -> None:
        v1 = tmp_path / "v1.yaml"
        v2 = tmp_path / "v2.yaml"
        _write_workflow(v1, _WORKFLOW_V1)
        _write_workflow(v2, _WORKFLOW_V2_TRUST_CHANGED)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--output-format", "json", "validate", "--security-audit", str(v1), str(v2)],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "diffs" in data
        assert "has_differences" in data

    def test_security_audit_two_paths_json_has_differences_true(self, tmp_path: Path) -> None:
        v1 = tmp_path / "v1.yaml"
        v2 = tmp_path / "v2.yaml"
        _write_workflow(v1, _WORKFLOW_V1)
        _write_workflow(v2, _WORKFLOW_V2_TRUST_CHANGED)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--output-format", "json", "validate", "--security-audit", str(v1), str(v2)],
        )
        data = json.loads(result.output)
        assert data["has_differences"] is True

    def test_security_audit_json_has_differences_false_when_no_changes(
        self, tmp_path: Path
    ) -> None:
        v1 = tmp_path / "v1.yaml"
        v2 = tmp_path / "v2.yaml"
        _write_workflow(v1, _WORKFLOW_V1)
        identical = dict(_WORKFLOW_V1)
        identical["identity"] = {"name": "test-workflow", "version": "1.0.1"}
        _write_workflow(v2, identical)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--output-format", "json", "validate", "--security-audit", str(v1), str(v2)],
        )
        data = json.loads(result.output)
        assert data["has_differences"] is False

    def test_security_audit_exits_one_when_path_missing(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["validate", "--security-audit", str(tmp_path / "missing.yaml")],
        )
        assert result.exit_code == 1

    def test_security_audit_two_paths_exits_one_when_path1_missing(
        self, tmp_path: Path
    ) -> None:
        v2 = tmp_path / "v2.yaml"
        _write_workflow(v2, _WORKFLOW_V1)
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "validate",
                "--security-audit",
                str(tmp_path / "missing.yaml"),
                str(v2),
            ],
        )
        assert result.exit_code == 1

    def test_validate_still_works_without_security_audit_flag(self, tmp_path: Path) -> None:
        """Normal validate mode should still work after refactoring."""
        path = tmp_path / "workflow.yaml"
        _write_workflow(
            path,
            {
                "identity": {"name": "wf", "version": "1.0.0"},
                "safety": {"trust": "sandboxed"},
            },
        )
        runner = CliRunner()
        result = runner.invoke(main, ["validate", str(path)])
        # Should succeed (0) or fail validation (1) but NOT crash with an
        # unexpected exception (only SystemExit is acceptable).
        assert result.exit_code in (0, 1)
        assert result.exception is None or isinstance(result.exception, SystemExit)

    def test_security_audit_is_listed_in_validate_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["validate", "--help"])
        assert "--security-audit" in result.output

    def test_security_audit_no_paths_exits_one(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["validate", "--security-audit"])
        assert result.exit_code == 1

    def test_security_audit_more_than_two_paths_exits_one(self, tmp_path: Path) -> None:
        paths = [tmp_path / f"v{i}.yaml" for i in range(3)]
        for p in paths:
            _write_workflow(p, _WORKFLOW_V1)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["validate", "--security-audit"] + [str(p) for p in paths],
        )
        assert result.exit_code == 1
