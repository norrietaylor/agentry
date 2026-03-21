"""Identity block model.

Represents the workflow's name, version, and description.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, field_validator

_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)


class IdentityBlock(BaseModel):
    """Workflow identity: name, version, and description.

    The version field must follow semantic versioning (https://semver.org).
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    name: str
    version: str
    description: str

    @field_validator("version")
    @classmethod
    def validate_semver(cls, v: str) -> str:
        """Ensure the version string follows semantic versioning format."""
        if not _SEMVER_RE.match(v):
            msg = (
                f"Invalid semantic version: {v!r}. "
                "Expected format: MAJOR.MINOR.PATCH (e.g. '1.0.0', '0.2.1-beta')"
            )
            raise ValueError(msg)
        return v
