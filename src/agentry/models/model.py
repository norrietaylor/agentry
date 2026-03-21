"""Model block model.

LLM provider configuration: provider, model ID, temperature, max_tokens,
system_prompt path, and retry config.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RetryConfig(BaseModel):
    """Retry configuration for LLM calls."""

    model_config = ConfigDict(strict=True, extra="forbid")

    max_attempts: int = Field(default=3, ge=1)
    backoff: str = "exponential"


class ModelBlock(BaseModel):
    """LLM provider and model configuration.

    Attributes:
        provider: LLM provider name (e.g. ``anthropic``).
        model_id: The specific model identifier (e.g. ``claude-sonnet-4-20250514``).
        temperature: Sampling temperature.
        max_tokens: Maximum number of tokens in the response.
        system_prompt: Path to the system prompt file, relative to the workflow file.
        retry: Retry configuration for transient failures.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    provider: str = "anthropic"
    model_id: str = "claude-sonnet-4-20250514"
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=1)
    system_prompt: str = ""
    retry: RetryConfig = RetryConfig()
