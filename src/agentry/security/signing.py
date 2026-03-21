"""Ed25519 key generation and workflow signing for Agentry.

Provides key generation (Ed25519) and the path conventions used by the
``agentry keygen`` CLI command.  The private key is stored outside the
project directory (in ``~/.agentry/``) so it is never accidentally committed,
while the public key lives in the project's ``.agentry/`` directory so it
can be shared via version control.

Usage::

    from agentry.security.signing import generate_keypair, DEFAULT_PRIVATE_KEY_PATH, DEFAULT_PUBLIC_KEY_PATH

    priv_path, pub_path = generate_keypair()
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

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
