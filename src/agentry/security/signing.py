"""Ed25519 key generation and workflow signing for Agentry.

Provides key generation (Ed25519) and the path conventions used by the
``agentry keygen`` CLI command.  The private key is stored outside the
project directory (in ``~/.agentry/``) so it is never accidentally committed,
while the public key lives in the project's ``.agentry/`` directory so it
can be shared via version control.

Also provides :func:`sign_workflow` for signing a workflow YAML file's
``safety`` and ``output.side_effects`` blocks using the Ed25519 private key,
and :func:`verify_workflow_signature` for verifying an existing signature.

Usage::

    from agentry.security.signing import generate_keypair, sign_workflow
    from agentry.security.signing import DEFAULT_PRIVATE_KEY_PATH, DEFAULT_PUBLIC_KEY_PATH

    priv_path, pub_path = generate_keypair()
    sign_workflow("workflows/code-review.yaml", private_key_path=priv_path)
"""

from __future__ import annotations

import contextlib
import datetime
import os
from pathlib import Path
from typing import Any

import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

# ---------------------------------------------------------------------------
# Path conventions
# ---------------------------------------------------------------------------

#: Default location for the private signing key (outside the project).
DEFAULT_PRIVATE_KEY_PATH: Path = Path.home() / ".agentry" / "signing-key.pem"

#: Default location for the public key (inside the project, committable).
DEFAULT_PUBLIC_KEY_PATH: Path = Path(".agentry") / "public-key.pem"


# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------


def generate_keypair(
    private_key_path: Path | None = None,
    public_key_path: Path | None = None,
) -> tuple[Path, Path]:
    """Generate an Ed25519 keypair and persist it to disk.

    The private key directory is created with mode ``0o700`` (owner-only
    access) to reduce the risk of accidental exposure.  The private key file
    itself is written with mode ``0o600``.

    Args:
        private_key_path: Where to write the private key.  Defaults to
            ``~/.agentry/signing-key.pem``.
        public_key_path: Where to write the public key.  Defaults to
            ``.agentry/public-key.pem`` (relative to the current working
            directory, i.e. the project root).

    Returns:
        A tuple of ``(private_key_path, public_key_path)`` as resolved
        :class:`~pathlib.Path` objects.

    Raises:
        OSError: If the key files cannot be written.
    """
    priv_path = private_key_path or DEFAULT_PRIVATE_KEY_PATH
    pub_path = public_key_path or DEFAULT_PUBLIC_KEY_PATH

    # Ensure parent directories exist.
    priv_path.parent.mkdir(parents=True, exist_ok=True)
    # Restrict the private-key directory to the owner only.
    with contextlib.suppress(OSError):
        priv_path.parent.chmod(0o700)

    pub_path.parent.mkdir(parents=True, exist_ok=True)

    # Generate the key.
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    # Serialise the private key (unencrypted PEM, PKCS8).
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    # Serialise the public key (PEM, SubjectPublicKeyInfo).
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    # Write private key with restricted permissions.
    # Use os.open to atomically set the file mode on creation.
    fd = os.open(str(priv_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(private_bytes)
    except Exception:
        # fd was closed by fdopen; nothing more to do.
        raise

    # Write public key (world-readable is fine for a public key).
    pub_path.write_bytes(public_bytes)

    return priv_path, pub_path


# ---------------------------------------------------------------------------
# Workflow signing helpers
# ---------------------------------------------------------------------------

#: Blocks that are included in the signature.
SIGNED_BLOCK_NAMES: list[str] = ["safety", "output.side_effects"]

#: Algorithm identifier written into the signature block.
SIGNING_ALGORITHM: str = "ed25519"


def _serialize_block(block: Any) -> bytes:
    """Deterministically serialize a value for signing.

    Uses ``yaml.dump`` with ``default_flow_style=False`` and
    ``sort_keys=True`` so the same data always produces the same bytes.

    Args:
        block: The Python object to serialize.

    Returns:
        UTF-8 encoded YAML bytes.
    """
    return yaml.dump(
        block,
        default_flow_style=False,
        sort_keys=True,
        allow_unicode=True,
    ).encode("utf-8")


def _extract_signed_blocks(workflow: dict[str, Any]) -> dict[str, Any]:
    """Extract the blocks that should be signed from a workflow definition.

    The signed blocks are ``safety`` (top-level key) and
    ``output.side_effects`` (the ``side_effects`` key nested under ``output``).

    Args:
        workflow: The parsed workflow YAML as a Python dict.

    Returns:
        A mapping of canonical block name to block value.  Missing blocks
        are represented as ``None``.
    """
    return {
        "safety": workflow.get("safety"),
        "output.side_effects": (workflow.get("output") or {}).get("side_effects"),
    }


def _build_signing_payload(blocks: dict[str, Any]) -> bytes:
    """Concatenate serialized block bytes for signing.

    Each block is prefixed with its name and a newline so the payload is
    unambiguous even if a block serialises to an empty string.

    Args:
        blocks: Ordered mapping of block_name -> block_value.

    Returns:
        Raw bytes to sign.
    """
    parts: list[bytes] = []
    for name, value in blocks.items():
        parts.append(f"block:{name}\n".encode())
        parts.append(_serialize_block(value))
    return b"".join(parts)


def sign_workflow(
    workflow_path: Path | str,
    *,
    private_key_path: Path | None = None,
    output_path: Path | str | None = None,
) -> Path:
    """Sign a workflow YAML file and append a ``signature`` block.

    Signs the ``safety`` block and the ``output.side_effects`` block using
    the Ed25519 private key located at *private_key_path*.  The signature
    block is appended to the YAML file (or written to *output_path* if
    supplied).

    The signature block format::

        signature:
          algorithm: ed25519
          signed_blocks:
            - safety
            - output.side_effects
          signature: <hex-encoded signature>
          timestamp: <ISO 8601 UTC timestamp>

    Args:
        workflow_path: Path to the workflow YAML file to sign.
        private_key_path: Path to the PEM-encoded Ed25519 private key.
            Defaults to ``DEFAULT_PRIVATE_KEY_PATH``.
        output_path: Where to write the signed workflow.  Defaults to
            overwriting *workflow_path* in place.

    Returns:
        The path to the signed workflow file.

    Raises:
        FileNotFoundError: If the workflow file or private key is not found.
        ValueError: If the private key is not an Ed25519 key.
    """
    workflow_path = Path(workflow_path)
    priv_path = private_key_path or DEFAULT_PRIVATE_KEY_PATH
    out_path = Path(output_path) if output_path else workflow_path

    if not workflow_path.exists():
        raise FileNotFoundError(f"Workflow file not found: {workflow_path}")
    if not priv_path.exists():
        raise FileNotFoundError(f"Private key not found: {priv_path}")

    # Load the workflow, stripping any existing signature block.
    with workflow_path.open() as fh:
        workflow: dict[str, Any] = yaml.safe_load(fh) or {}
    workflow.pop("signature", None)

    # Load the private key.
    private_key_raw = serialization.load_pem_private_key(
        priv_path.read_bytes(), password=None
    )
    if not isinstance(private_key_raw, Ed25519PrivateKey):
        raise ValueError(f"Key at {priv_path} is not an Ed25519 private key")
    private_key: Ed25519PrivateKey = private_key_raw

    # Build the signing payload from the designated blocks.
    blocks = _extract_signed_blocks(workflow)
    payload = _build_signing_payload(blocks)

    # Sign.
    signature_bytes = private_key.sign(payload)
    signature_hex = signature_bytes.hex()

    # ISO 8601 UTC timestamp (no microseconds for readability).
    timestamp = datetime.datetime.now(tz=datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    # Append the signature block to the workflow dict.
    workflow["signature"] = {
        "algorithm": SIGNING_ALGORITHM,
        "signed_blocks": list(blocks.keys()),
        "signature": signature_hex,
        "timestamp": timestamp,
    }

    # Write the updated workflow.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as fh:
        yaml.dump(workflow, fh, default_flow_style=False, sort_keys=False, allow_unicode=True)

    return out_path


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


class SignatureVerificationError(Exception):
    """Raised when a workflow signature is invalid or cannot be verified."""


def verify_workflow_signature(
    workflow_path: Path | str,
    *,
    public_key_path: Path | None = None,
) -> str:
    """Verify the Ed25519 signature in a workflow YAML file.

    Args:
        workflow_path: Path to the signed workflow YAML file.
        public_key_path: Path to the PEM-encoded Ed25519 public key.
            Defaults to ``DEFAULT_PUBLIC_KEY_PATH``.

    Returns:
        The ISO 8601 timestamp from the signature block on success.

    Raises:
        FileNotFoundError: If the workflow or public key file is not found.
        KeyError: If the workflow has no ``signature`` block.
        SignatureVerificationError: If the signature is invalid.
        ValueError: If the public key is not an Ed25519 key.
    """
    workflow_path = Path(workflow_path)
    pub_path = public_key_path or DEFAULT_PUBLIC_KEY_PATH

    if not workflow_path.exists():
        raise FileNotFoundError(f"Workflow file not found: {workflow_path}")
    if not pub_path.exists():
        raise FileNotFoundError(f"Public key not found: {pub_path}")

    with workflow_path.open() as fh:
        workflow: dict[str, Any] = yaml.safe_load(fh) or {}

    if "signature" not in workflow:
        raise KeyError("Workflow has no 'signature' block")

    sig_block = workflow["signature"]
    signature_hex: str = sig_block["signature"]
    timestamp: str = sig_block["timestamp"]

    # Reconstruct the workflow as it was when signed (no signature block).
    unsigned_workflow = {k: v for k, v in workflow.items() if k != "signature"}

    # Re-derive the signing payload.
    blocks = _extract_signed_blocks(unsigned_workflow)
    payload = _build_signing_payload(blocks)

    # Load the public key.
    public_key_raw = serialization.load_pem_public_key(pub_path.read_bytes())
    if not isinstance(public_key_raw, Ed25519PublicKey):
        raise ValueError(f"Key at {pub_path} is not an Ed25519 public key")
    public_key: Ed25519PublicKey = public_key_raw

    # Verify.
    try:
        public_key.verify(bytes.fromhex(signature_hex), payload)
    except InvalidSignature as exc:
        raise SignatureVerificationError(
            f"Safety block signature invalid. The safety block was modified since it was signed on {timestamp}"
        ) from exc

    return timestamp
