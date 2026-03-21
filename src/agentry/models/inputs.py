"""Input block models.

Discriminated union input types: git-diff, repository-ref, document-ref.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class GitDiffInput(BaseModel):
    """Input that resolves to a git diff in the target repository.

    Requires a ``ref`` field indicating the git reference to diff against
    (e.g. ``HEAD~1``, ``main``).
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    type: Literal["git-diff"]
    required: bool = True
    description: str = ""
    ref: str = "HEAD~1"


class RepositoryRefInput(BaseModel):
    """Input that resolves to an absolute path of a git repository."""

    model_config = ConfigDict(strict=True, extra="forbid")

    type: Literal["repository-ref"]
    required: bool = True
    description: str = ""


class DocumentRefInput(BaseModel):
    """Input that resolves to the contents of a document file."""

    model_config = ConfigDict(strict=True, extra="forbid")

    type: Literal["document-ref"]
    required: bool = False
    description: str = ""
    path: str = ""


class StringInput(BaseModel):
    """Plain string input passed directly via CLI."""

    model_config = ConfigDict(strict=True, extra="forbid")

    type: Literal["string"]
    required: bool = True
    description: str = ""
    default: str | None = None


# Discriminated union of all input types, keyed on the ``type`` field.
InputType = Annotated[
    GitDiffInput | RepositoryRefInput | DocumentRefInput | StringInput,
    Field(discriminator="type"),
]
