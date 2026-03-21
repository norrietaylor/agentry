"""Unit tests for T05.1: Ed25519 keygen and 'agentry keygen' CLI command.

Tests cover:
- generate_keypair() writes PEM-encoded private and public key files.
- Private key path uses ~/.agentry/signing-key.pem by default.
- Public key path uses .agentry/public-key.pem by default.
- Custom paths can be supplied to generate_keypair().
- Generated keypair is cryptographically valid (sign/verify round-trip).
- Private key file is restricted to mode 0o600.
- 'agentry keygen' CLI command produces keys at the expected paths.
- 'agentry keygen' text output instructs the user to commit the public key.
- 'agentry keygen' JSON output contains private_key and public_key fields.
"""

from __future__ import annotations

import stat
from pathlib import Path

from click.testing import CliRunner
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from agentry.cli import main
from agentry.security.signing import (
    DEFAULT_PRIVATE_KEY_PATH,
    DEFAULT_PUBLIC_KEY_PATH,
    generate_keypair,
)

# ---------------------------------------------------------------------------
# generate_keypair() unit tests
# ---------------------------------------------------------------------------


class TestGenerateKeypair:
    """Tests for the generate_keypair() function."""

    def test_creates_private_key_file(self, tmp_path: Path) -> None:
        priv = tmp_path / "priv" / "signing-key.pem"
        pub = tmp_path / "pub" / "public-key.pem"
        generate_keypair(private_key_path=priv, public_key_path=pub)
        assert priv.exists(), "Private key file should be created"

    def test_creates_public_key_file(self, tmp_path: Path) -> None:
        priv = tmp_path / "priv" / "signing-key.pem"
        pub = tmp_path / "pub" / "public-key.pem"
        generate_keypair(private_key_path=priv, public_key_path=pub)
        assert pub.exists(), "Public key file should be created"

    def test_private_key_is_pem_encoded(self, tmp_path: Path) -> None:
        priv = tmp_path / "signing-key.pem"
        pub = tmp_path / "public-key.pem"
        generate_keypair(private_key_path=priv, public_key_path=pub)
        data = priv.read_bytes()
        assert data.startswith(b"-----BEGIN PRIVATE KEY-----"), (
            "Private key should be PEM PKCS8"
        )

    def test_public_key_is_pem_encoded(self, tmp_path: Path) -> None:
        priv = tmp_path / "signing-key.pem"
        pub = tmp_path / "public-key.pem"
        generate_keypair(private_key_path=priv, public_key_path=pub)
        data = pub.read_bytes()
        assert data.startswith(b"-----BEGIN PUBLIC KEY-----"), (
            "Public key should be PEM SubjectPublicKeyInfo"
        )

    def test_private_key_loads_as_ed25519(self, tmp_path: Path) -> None:
        priv = tmp_path / "signing-key.pem"
        pub = tmp_path / "public-key.pem"
        generate_keypair(private_key_path=priv, public_key_path=pub)
        loaded = serialization.load_pem_private_key(priv.read_bytes(), password=None)
        assert isinstance(loaded, Ed25519PrivateKey)

    def test_public_key_loads_as_ed25519(self, tmp_path: Path) -> None:
        priv = tmp_path / "signing-key.pem"
        pub = tmp_path / "public-key.pem"
        generate_keypair(private_key_path=priv, public_key_path=pub)
        loaded = serialization.load_pem_public_key(pub.read_bytes())
        assert isinstance(loaded, Ed25519PublicKey)

    def test_sign_verify_round_trip(self, tmp_path: Path) -> None:
        """The generated keypair must be a valid cryptographic pair."""
        priv = tmp_path / "signing-key.pem"
        pub = tmp_path / "public-key.pem"
        generate_keypair(private_key_path=priv, public_key_path=pub)

        private_key: Ed25519PrivateKey = serialization.load_pem_private_key(  # type: ignore[assignment]
            priv.read_bytes(), password=None
        )
        public_key: Ed25519PublicKey = serialization.load_pem_public_key(  # type: ignore[assignment]
            pub.read_bytes()
        )

        message = b"hello agentry"
        signature = private_key.sign(message)
        # verify() raises if invalid; should not raise for a valid pair.
        public_key.verify(signature, message)

    def test_private_key_file_mode_is_restricted(self, tmp_path: Path) -> None:
        priv = tmp_path / "signing-key.pem"
        pub = tmp_path / "public-key.pem"
        generate_keypair(private_key_path=priv, public_key_path=pub)
        file_mode = stat.S_IMODE(priv.stat().st_mode)
        assert file_mode == 0o600, (
            f"Private key should have mode 0600, got {oct(file_mode)}"
        )

    def test_returns_resolved_paths(self, tmp_path: Path) -> None:
        priv = tmp_path / "priv" / "signing-key.pem"
        pub = tmp_path / "pub" / "public-key.pem"
        returned_priv, returned_pub = generate_keypair(
            private_key_path=priv, public_key_path=pub
        )
        assert returned_priv == priv
        assert returned_pub == pub

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        priv = tmp_path / "deep" / "nested" / "signing-key.pem"
        pub = tmp_path / "another" / "dir" / "public-key.pem"
        generate_keypair(private_key_path=priv, public_key_path=pub)
        assert priv.exists()
        assert pub.exists()

    def test_default_private_key_path_is_in_home(self) -> None:
        assert Path.home() / ".agentry" / "signing-key.pem" == DEFAULT_PRIVATE_KEY_PATH

    def test_default_public_key_path_is_in_dotgitroot(self) -> None:
        assert Path(".agentry") / "public-key.pem" == DEFAULT_PUBLIC_KEY_PATH


# ---------------------------------------------------------------------------
# 'agentry keygen' CLI command tests
# ---------------------------------------------------------------------------


class TestKeygenCLI:
    """Tests for the 'agentry keygen' CLI command."""

    def test_keygen_creates_private_key(self, tmp_path: Path) -> None:
        runner = CliRunner()
        priv = tmp_path / "signing-key.pem"
        pub = tmp_path / "public-key.pem"
        result = runner.invoke(
            main,
            ["keygen", "--private-key", str(priv), "--public-key", str(pub)],
        )
        assert result.exit_code == 0, result.output
        assert priv.exists()

    def test_keygen_creates_public_key(self, tmp_path: Path) -> None:
        runner = CliRunner()
        priv = tmp_path / "signing-key.pem"
        pub = tmp_path / "public-key.pem"
        result = runner.invoke(
            main,
            ["keygen", "--private-key", str(priv), "--public-key", str(pub)],
        )
        assert result.exit_code == 0, result.output
        assert pub.exists()

    def test_keygen_text_output_shows_private_key_path(self, tmp_path: Path) -> None:
        runner = CliRunner()
        priv = tmp_path / "signing-key.pem"
        pub = tmp_path / "public-key.pem"
        result = runner.invoke(
            main,
            [
                "--output-format",
                "text",
                "keygen",
                "--private-key",
                str(priv),
                "--public-key",
                str(pub),
            ],
        )
        assert result.exit_code == 0, result.output
        assert str(priv) in result.output

    def test_keygen_text_output_shows_public_key_path(self, tmp_path: Path) -> None:
        runner = CliRunner()
        priv = tmp_path / "signing-key.pem"
        pub = tmp_path / "public-key.pem"
        result = runner.invoke(
            main,
            [
                "--output-format",
                "text",
                "keygen",
                "--private-key",
                str(priv),
                "--public-key",
                str(pub),
            ],
        )
        assert result.exit_code == 0, result.output
        assert str(pub) in result.output

    def test_keygen_text_instructs_to_git_add(self, tmp_path: Path) -> None:
        runner = CliRunner()
        priv = tmp_path / "signing-key.pem"
        pub = tmp_path / "public-key.pem"
        result = runner.invoke(
            main,
            [
                "--output-format",
                "text",
                "keygen",
                "--private-key",
                str(priv),
                "--public-key",
                str(pub),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "git add" in result.output

    def test_keygen_text_warns_not_to_commit_private_key(self, tmp_path: Path) -> None:
        runner = CliRunner()
        priv = tmp_path / "signing-key.pem"
        pub = tmp_path / "public-key.pem"
        result = runner.invoke(
            main,
            [
                "--output-format",
                "text",
                "keygen",
                "--private-key",
                str(priv),
                "--public-key",
                str(pub),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "NEVER" in result.output or "never" in result.output.lower()

    def test_keygen_json_output_contains_status_ok(self, tmp_path: Path) -> None:
        runner = CliRunner()
        priv = tmp_path / "signing-key.pem"
        pub = tmp_path / "public-key.pem"
        result = runner.invoke(
            main,
            [
                "--output-format",
                "json",
                "keygen",
                "--private-key",
                str(priv),
                "--public-key",
                str(pub),
            ],
        )
        assert result.exit_code == 0, result.output
        import json

        data = json.loads(result.output)
        assert data["status"] == "ok"

    def test_keygen_json_output_contains_private_key_path(self, tmp_path: Path) -> None:
        runner = CliRunner()
        priv = tmp_path / "signing-key.pem"
        pub = tmp_path / "public-key.pem"
        result = runner.invoke(
            main,
            [
                "--output-format",
                "json",
                "keygen",
                "--private-key",
                str(priv),
                "--public-key",
                str(pub),
            ],
        )
        assert result.exit_code == 0, result.output
        import json

        data = json.loads(result.output)
        assert data["private_key"] == str(priv)

    def test_keygen_json_output_contains_public_key_path(self, tmp_path: Path) -> None:
        runner = CliRunner()
        priv = tmp_path / "signing-key.pem"
        pub = tmp_path / "public-key.pem"
        result = runner.invoke(
            main,
            [
                "--output-format",
                "json",
                "keygen",
                "--private-key",
                str(priv),
                "--public-key",
                str(pub),
            ],
        )
        assert result.exit_code == 0, result.output
        import json

        data = json.loads(result.output)
        assert data["public_key"] == str(pub)

    def test_keygen_exit_code_zero_on_success(self, tmp_path: Path) -> None:
        runner = CliRunner()
        priv = tmp_path / "signing-key.pem"
        pub = tmp_path / "public-key.pem"
        result = runner.invoke(
            main,
            ["keygen", "--private-key", str(priv), "--public-key", str(pub)],
        )
        assert result.exit_code == 0

    def test_keygen_is_registered_as_subcommand(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "keygen" in result.output
