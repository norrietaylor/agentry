"""Unit tests for T05.2: Workflow signing and 'agentry sign' CLI command.

Tests cover:
- sign_workflow() appends a signature block to the workflow YAML.
- Signature block contains algorithm, signed_blocks, signature, timestamp.
- Only 'safety' and 'output.side_effects' are included in signed_blocks.
- Signature is hex-encoded.
- Timestamp is ISO 8601 format.
- Deterministic serialization: signing twice produces the same signature.
- Sign/verify round-trip succeeds for unmodified workflow.
- verify_workflow_signature() raises SignatureVerificationError when safety block modified.
- verify_workflow_signature() raises KeyError for workflow without signature block.
- sign_workflow() raises FileNotFoundError for missing workflow file.
- sign_workflow() raises FileNotFoundError for missing private key.
- sign_workflow() writes to --output path when supplied.
- sign_workflow() overwrites existing signature block (re-sign).
- 'agentry sign' CLI command exits 0 on success.
- 'agentry sign' CLI command text output names the signed file.
- 'agentry sign' CLI command JSON output contains status ok and signed_blocks.
- 'agentry sign' CLI command exits 1 when workflow not found.
- 'agentry sign' CLI command exits 1 when private key not found.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from agentry.cli import main
from agentry.security.signing import (
    SIGNED_BLOCK_NAMES,
    SIGNING_ALGORITHM,
    SignatureVerificationError,
    _build_signing_payload,
    _extract_signed_blocks,
    _serialize_block,
    generate_keypair,
    sign_workflow,
    verify_workflow_signature,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINIMAL_WORKFLOW = {
    "identity": {"name": "test-workflow", "version": "1.0.0"},
    "safety": {"resources": {"timeout": 300}},
    "output": {"side_effects": [], "schema": {"type": "object"}},
}

_WORKFLOW_NO_SAFETY = {
    "identity": {"name": "no-safety", "version": "1.0.0"},
    "output": {"schema": {"type": "object"}},
}


def _write_workflow(path: Path, data: dict | None = None) -> None:
    data = data if data is not None else _MINIMAL_WORKFLOW
    with path.open("w") as fh:
        yaml.dump(data, fh, default_flow_style=False, sort_keys=False, allow_unicode=True)


def _make_keypair(tmp: Path) -> tuple[Path, Path]:
    priv = tmp / "priv" / "signing-key.pem"
    pub = tmp / "pub" / "public-key.pem"
    generate_keypair(private_key_path=priv, public_key_path=pub)
    return priv, pub


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


class TestSerializeBlock:
    def test_serialize_dict_is_deterministic(self) -> None:
        data = {"b": 2, "a": 1, "c": {"z": 26, "m": 13}}
        assert _serialize_block(data) == _serialize_block(data)

    def test_serialize_same_content_different_order_is_equal(self) -> None:
        a = {"z": 1, "a": 2}
        b = {"a": 2, "z": 1}
        assert _serialize_block(a) == _serialize_block(b)

    def test_serialize_none_returns_bytes(self) -> None:
        result = _serialize_block(None)
        assert isinstance(result, bytes)

    def test_serialize_list_is_deterministic(self) -> None:
        data = [1, 2, 3]
        assert _serialize_block(data) == _serialize_block(data)


class TestExtractSignedBlocks:
    def test_extracts_safety_block(self) -> None:
        wf = {"safety": {"resources": {"timeout": 300}}, "output": {}}
        blocks = _extract_signed_blocks(wf)
        assert blocks["safety"] == {"resources": {"timeout": 300}}

    def test_extracts_output_side_effects(self) -> None:
        wf = {"output": {"side_effects": ["write:/tmp/out"]}}
        blocks = _extract_signed_blocks(wf)
        assert blocks["output.side_effects"] == ["write:/tmp/out"]

    def test_missing_safety_gives_none(self) -> None:
        wf: dict = {"output": {}}
        blocks = _extract_signed_blocks(wf)
        assert blocks["safety"] is None

    def test_missing_side_effects_gives_none(self) -> None:
        wf: dict = {"safety": {}}
        blocks = _extract_signed_blocks(wf)
        assert blocks["output.side_effects"] is None


class TestBuildSigningPayload:
    def test_payload_is_bytes(self) -> None:
        blocks = {"safety": {"timeout": 300}, "output.side_effects": []}
        assert isinstance(_build_signing_payload(blocks), bytes)

    def test_payload_includes_block_names(self) -> None:
        blocks = {"safety": {"timeout": 300}, "output.side_effects": []}
        payload = _build_signing_payload(blocks)
        assert b"block:safety" in payload
        assert b"block:output.side_effects" in payload

    def test_payload_deterministic(self) -> None:
        blocks = {"safety": {"a": 1}, "output.side_effects": None}
        assert _build_signing_payload(blocks) == _build_signing_payload(blocks)


# ---------------------------------------------------------------------------
# sign_workflow() unit tests
# ---------------------------------------------------------------------------


class TestSignWorkflow:
    def test_appends_signature_block(self, tmp_path: Path) -> None:
        priv, pub = _make_keypair(tmp_path)
        wf = tmp_path / "workflow.yaml"
        _write_workflow(wf)
        sign_workflow(wf, private_key_path=priv)
        with wf.open() as f:
            data = yaml.safe_load(f)
        assert "signature" in data

    def test_signature_block_has_algorithm(self, tmp_path: Path) -> None:
        priv, pub = _make_keypair(tmp_path)
        wf = tmp_path / "workflow.yaml"
        _write_workflow(wf)
        sign_workflow(wf, private_key_path=priv)
        with wf.open() as f:
            data = yaml.safe_load(f)
        assert data["signature"]["algorithm"] == SIGNING_ALGORITHM

    def test_signature_block_has_signed_blocks(self, tmp_path: Path) -> None:
        priv, pub = _make_keypair(tmp_path)
        wf = tmp_path / "workflow.yaml"
        _write_workflow(wf)
        sign_workflow(wf, private_key_path=priv)
        with wf.open() as f:
            data = yaml.safe_load(f)
        assert data["signature"]["signed_blocks"] == SIGNED_BLOCK_NAMES

    def test_signature_block_has_hex_signature(self, tmp_path: Path) -> None:
        priv, pub = _make_keypair(tmp_path)
        wf = tmp_path / "workflow.yaml"
        _write_workflow(wf)
        sign_workflow(wf, private_key_path=priv)
        with wf.open() as f:
            data = yaml.safe_load(f)
        sig_hex: str = data["signature"]["signature"]
        # Should be a valid hex string
        assert re.fullmatch(r"[0-9a-f]+", sig_hex), "signature should be lowercase hex"

    def test_signature_hex_decodes_to_64_bytes(self, tmp_path: Path) -> None:
        """Ed25519 signatures are 64 bytes = 128 hex chars."""
        priv, pub = _make_keypair(tmp_path)
        wf = tmp_path / "workflow.yaml"
        _write_workflow(wf)
        sign_workflow(wf, private_key_path=priv)
        with wf.open() as f:
            data = yaml.safe_load(f)
        assert len(bytes.fromhex(data["signature"]["signature"])) == 64

    def test_signature_block_has_iso8601_timestamp(self, tmp_path: Path) -> None:
        priv, pub = _make_keypair(tmp_path)
        wf = tmp_path / "workflow.yaml"
        _write_workflow(wf)
        sign_workflow(wf, private_key_path=priv)
        with wf.open() as f:
            data = yaml.safe_load(f)
        ts: str = data["signature"]["timestamp"]
        # Minimal ISO 8601 UTC check
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", ts), (
            f"timestamp {ts!r} does not match ISO 8601 UTC format"
        )

    def test_returns_workflow_path(self, tmp_path: Path) -> None:
        priv, pub = _make_keypair(tmp_path)
        wf = tmp_path / "workflow.yaml"
        _write_workflow(wf)
        result = sign_workflow(wf, private_key_path=priv)
        assert result == wf

    def test_writes_to_output_path_when_supplied(self, tmp_path: Path) -> None:
        priv, pub = _make_keypair(tmp_path)
        wf = tmp_path / "workflow.yaml"
        out = tmp_path / "signed.yaml"
        _write_workflow(wf)
        result = sign_workflow(wf, private_key_path=priv, output_path=out)
        assert result == out
        assert out.exists()

    def test_original_file_unchanged_when_output_path_supplied(self, tmp_path: Path) -> None:
        priv, pub = _make_keypair(tmp_path)
        wf = tmp_path / "workflow.yaml"
        out = tmp_path / "signed.yaml"
        _write_workflow(wf)
        original_text = wf.read_text()
        sign_workflow(wf, private_key_path=priv, output_path=out)
        assert wf.read_text() == original_text

    def test_raises_file_not_found_for_missing_workflow(self, tmp_path: Path) -> None:
        priv, pub = _make_keypair(tmp_path)
        missing = tmp_path / "missing.yaml"
        with pytest.raises(FileNotFoundError, match="missing.yaml"):
            sign_workflow(missing, private_key_path=priv)

    def test_raises_file_not_found_for_missing_private_key(self, tmp_path: Path) -> None:
        wf = tmp_path / "workflow.yaml"
        _write_workflow(wf)
        missing_key = tmp_path / "no-key.pem"
        with pytest.raises(FileNotFoundError, match="no-key.pem"):
            sign_workflow(wf, private_key_path=missing_key)

    def test_re_signing_replaces_existing_signature(self, tmp_path: Path) -> None:
        priv, pub = _make_keypair(tmp_path)
        wf = tmp_path / "workflow.yaml"
        _write_workflow(wf)
        sign_workflow(wf, private_key_path=priv)
        sign_workflow(wf, private_key_path=priv)
        with wf.open() as f:
            data = yaml.safe_load(f)
        # Only one signature block present (not nested)
        assert isinstance(data["signature"], dict)
        assert "algorithm" in data["signature"]

    def test_signing_workflow_without_safety_block(self, tmp_path: Path) -> None:
        priv, pub = _make_keypair(tmp_path)
        wf = tmp_path / "workflow.yaml"
        _write_workflow(wf, _WORKFLOW_NO_SAFETY)
        # Should not raise – missing blocks are treated as None
        sign_workflow(wf, private_key_path=priv)
        with wf.open() as f:
            data = yaml.safe_load(f)
        assert "signature" in data


# ---------------------------------------------------------------------------
# Deterministic signing tests
# ---------------------------------------------------------------------------


class TestDeterministicSigning:
    def test_same_workflow_same_key_same_signature(self, tmp_path: Path) -> None:
        priv, pub = _make_keypair(tmp_path)
        wf = tmp_path / "workflow.yaml"
        _write_workflow(wf)

        # Sign once and capture the signature.
        sign_workflow(wf, private_key_path=priv)
        with wf.open() as f:
            first = yaml.safe_load(f)
        sig1: str = first["signature"]["signature"]

        # The workflow file now contains the signature block. Re-sign (which
        # strips the existing block before computing the new one) and compare.
        sign_workflow(wf, private_key_path=priv)
        with wf.open() as f:
            second = yaml.safe_load(f)
        sig2: str = second["signature"]["signature"]

        assert sig1 == sig2, "Same workflow + same key should produce identical signature"

    def test_serialize_block_same_content_different_key_order(self) -> None:
        a = {"z": [1, 2], "a": {"nested": True}}
        b = {"a": {"nested": True}, "z": [1, 2]}
        assert _serialize_block(a) == _serialize_block(b)


# ---------------------------------------------------------------------------
# verify_workflow_signature() tests
# ---------------------------------------------------------------------------


class TestVerifyWorkflowSignature:
    def test_verify_passes_for_unmodified_workflow(self, tmp_path: Path) -> None:
        priv, pub = _make_keypair(tmp_path)
        wf = tmp_path / "workflow.yaml"
        _write_workflow(wf)
        sign_workflow(wf, private_key_path=priv)
        ts = verify_workflow_signature(wf, public_key_path=pub)
        assert isinstance(ts, str)
        assert "T" in ts  # ISO 8601 contains 'T'

    def test_verify_returns_timestamp(self, tmp_path: Path) -> None:
        priv, pub = _make_keypair(tmp_path)
        wf = tmp_path / "workflow.yaml"
        _write_workflow(wf)
        sign_workflow(wf, private_key_path=priv)
        with wf.open() as f:
            data = yaml.safe_load(f)
        expected_ts = data["signature"]["timestamp"]
        ts = verify_workflow_signature(wf, public_key_path=pub)
        assert ts == expected_ts

    def test_verify_raises_on_tampered_safety_block(self, tmp_path: Path) -> None:
        priv, pub = _make_keypair(tmp_path)
        wf = tmp_path / "workflow.yaml"
        _write_workflow(wf)
        sign_workflow(wf, private_key_path=priv)

        # Tamper with the safety block.
        with wf.open() as f:
            data = yaml.safe_load(f)
        data["safety"]["resources"]["timeout"] = 9999
        with wf.open("w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

        with pytest.raises(SignatureVerificationError, match="Safety block signature invalid"):
            verify_workflow_signature(wf, public_key_path=pub)

    def test_verify_raises_on_tampered_side_effects(self, tmp_path: Path) -> None:
        priv, pub = _make_keypair(tmp_path)
        wf = tmp_path / "workflow.yaml"
        _write_workflow(wf)
        sign_workflow(wf, private_key_path=priv)

        # Tamper with output.side_effects.
        with wf.open() as f:
            data = yaml.safe_load(f)
        data["output"]["side_effects"] = ["write:/evil"]
        with wf.open("w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

        with pytest.raises(SignatureVerificationError):
            verify_workflow_signature(wf, public_key_path=pub)

    def test_verify_succeeds_when_non_signed_block_modified(self, tmp_path: Path) -> None:
        priv, pub = _make_keypair(tmp_path)
        wf = tmp_path / "workflow.yaml"
        _write_workflow(wf)
        sign_workflow(wf, private_key_path=priv)

        # Modify the identity block (not signed).
        with wf.open() as f:
            data = yaml.safe_load(f)
        data["identity"]["description"] = "changed description"
        with wf.open("w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

        # Should not raise.
        verify_workflow_signature(wf, public_key_path=pub)

    def test_verify_raises_key_error_for_unsigned_workflow(self, tmp_path: Path) -> None:
        _, pub = _make_keypair(tmp_path)
        wf = tmp_path / "workflow.yaml"
        _write_workflow(wf)
        with pytest.raises(KeyError):
            verify_workflow_signature(wf, public_key_path=pub)

    def test_verify_raises_file_not_found_for_missing_workflow(self, tmp_path: Path) -> None:
        _, pub = _make_keypair(tmp_path)
        with pytest.raises(FileNotFoundError):
            verify_workflow_signature(tmp_path / "missing.yaml", public_key_path=pub)

    def test_verify_raises_file_not_found_for_missing_public_key(self, tmp_path: Path) -> None:
        priv, pub = _make_keypair(tmp_path)
        wf = tmp_path / "workflow.yaml"
        _write_workflow(wf)
        sign_workflow(wf, private_key_path=priv)
        with pytest.raises(FileNotFoundError):
            verify_workflow_signature(wf, public_key_path=tmp_path / "no-key.pem")

    def test_error_message_includes_timestamp(self, tmp_path: Path) -> None:
        priv, pub = _make_keypair(tmp_path)
        wf = tmp_path / "workflow.yaml"
        _write_workflow(wf)
        sign_workflow(wf, private_key_path=priv)

        with wf.open() as f:
            data = yaml.safe_load(f)
        expected_ts = data["signature"]["timestamp"]

        # Tamper.
        data["safety"]["resources"]["timeout"] = 1
        with wf.open("w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

        with pytest.raises(SignatureVerificationError, match=re.escape(expected_ts)):
            verify_workflow_signature(wf, public_key_path=pub)


# ---------------------------------------------------------------------------
# 'agentry sign' CLI command tests
# ---------------------------------------------------------------------------


class TestSignCLI:
    """Tests for the 'agentry sign' CLI command."""

    def test_sign_exits_zero_on_success(self, tmp_path: Path) -> None:
        priv, pub = _make_keypair(tmp_path)
        wf = tmp_path / "workflow.yaml"
        _write_workflow(wf)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["sign", str(wf), "--private-key", str(priv)],
        )
        assert result.exit_code == 0, result.output

    def test_sign_appends_signature_to_file(self, tmp_path: Path) -> None:
        priv, pub = _make_keypair(tmp_path)
        wf = tmp_path / "workflow.yaml"
        _write_workflow(wf)
        runner = CliRunner()
        runner.invoke(main, ["sign", str(wf), "--private-key", str(priv)])
        with wf.open() as f:
            data = yaml.safe_load(f)
        assert "signature" in data

    def test_sign_text_output_names_signed_file(self, tmp_path: Path) -> None:
        priv, pub = _make_keypair(tmp_path)
        wf = tmp_path / "workflow.yaml"
        _write_workflow(wf)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--output-format", "text", "sign", str(wf), "--private-key", str(priv)],
        )
        assert result.exit_code == 0, result.output
        assert str(wf) in result.output

    def test_sign_text_output_mentions_signed_blocks(self, tmp_path: Path) -> None:
        priv, pub = _make_keypair(tmp_path)
        wf = tmp_path / "workflow.yaml"
        _write_workflow(wf)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--output-format", "text", "sign", str(wf), "--private-key", str(priv)],
        )
        assert result.exit_code == 0, result.output
        assert "safety" in result.output

    def test_sign_json_output_status_ok(self, tmp_path: Path) -> None:
        priv, pub = _make_keypair(tmp_path)
        wf = tmp_path / "workflow.yaml"
        _write_workflow(wf)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--output-format", "json", "sign", str(wf), "--private-key", str(priv)],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["status"] == "ok"

    def test_sign_json_output_contains_workflow_path(self, tmp_path: Path) -> None:
        priv, pub = _make_keypair(tmp_path)
        wf = tmp_path / "workflow.yaml"
        _write_workflow(wf)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--output-format", "json", "sign", str(wf), "--private-key", str(priv)],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["workflow"] == str(wf)

    def test_sign_json_output_contains_signed_blocks(self, tmp_path: Path) -> None:
        priv, pub = _make_keypair(tmp_path)
        wf = tmp_path / "workflow.yaml"
        _write_workflow(wf)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--output-format", "json", "sign", str(wf), "--private-key", str(priv)],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["signed_blocks"] == SIGNED_BLOCK_NAMES

    def test_sign_exits_one_when_workflow_not_found(self, tmp_path: Path) -> None:
        priv, _ = _make_keypair(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["sign", str(tmp_path / "missing.yaml"), "--private-key", str(priv)],
        )
        assert result.exit_code == 1

    def test_sign_exits_one_when_private_key_not_found(self, tmp_path: Path) -> None:
        wf = tmp_path / "workflow.yaml"
        _write_workflow(wf)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["sign", str(wf), "--private-key", str(tmp_path / "no-key.pem")],
        )
        assert result.exit_code == 1

    def test_sign_output_option_writes_to_alternate_path(self, tmp_path: Path) -> None:
        priv, _ = _make_keypair(tmp_path)
        wf = tmp_path / "workflow.yaml"
        out = tmp_path / "signed.yaml"
        _write_workflow(wf)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["sign", str(wf), "--private-key", str(priv), "--output", str(out)],
        )
        assert result.exit_code == 0, result.output
        assert out.exists()

    def test_sign_is_registered_as_subcommand(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "sign" in result.output
