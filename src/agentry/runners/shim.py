"""Runtime shim for sandboxed Docker execution.

This lightweight script runs inside the Docker container. It reads a JSON
configuration file mounted at a known path, launches the configured agent
runtime (e.g. ClaudeCodeAgent), and writes the result to
``/output/result.json``.

The shim is the bridge between the host-side DockerRunner (which provisions
the container) and the in-container agent execution. The host mounts a
``config.json`` file containing agent runtime configuration, tool bindings,
resolved inputs, and the system prompt. The shim parses this file, constructs
the appropriate agent runtime via :class:`~agentry.agents.registry.AgentRegistry`,
runs it, and persists the :class:`~agentry.agents.models.AgentResult` as JSON.

Usage (inside the container)::

    python -m agentry.runners.shim /config/agent_config.json

Exit codes:
    0 -- Agent executed successfully.
    1 -- Agent execution failed (error recorded in result.json).
    2 -- Shim startup error (bad config, missing file, etc.).
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_PATH = "/config/agent_config.json"
DEFAULT_OUTPUT_PATH = "/output/result.json"


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def load_config(config_path: str) -> dict[str, Any]:
    """Load and validate the agent configuration JSON.

    Args:
        config_path: Filesystem path to the JSON config file.

    Returns:
        Parsed configuration dictionary.

    Raises:
        FileNotFoundError: If the config file does not exist.
        json.JSONDecodeError: If the config file is not valid JSON.
        ValueError: If required keys are missing.
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with path.open() as fh:
        config = json.load(fh)

    required_keys = {"system_prompt", "resolved_inputs", "tool_names"}
    missing = required_keys - set(config.keys())
    if missing:
        raise ValueError(f"Missing required config keys: {sorted(missing)}")

    return config


# ---------------------------------------------------------------------------
# Result writing
# ---------------------------------------------------------------------------


def write_result(output_path: str, result: dict[str, Any]) -> None:
    """Write the execution result to a JSON file.

    Creates parent directories if they do not exist.

    Args:
        output_path: Filesystem path for the output JSON.
        result: Serialisable result dictionary.
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as fh:
        json.dump(result, fh, indent=2, default=str)


# ---------------------------------------------------------------------------
# Shim execution
# ---------------------------------------------------------------------------


def run_shim(
    config_path: str = DEFAULT_CONFIG_PATH,
    output_path: str = DEFAULT_OUTPUT_PATH,
) -> int:
    """Execute the agent runtime from configuration and write the result.

    This is the main entry point for the shim. It loads configuration,
    constructs the configured agent runtime via
    :class:`~agentry.agents.registry.AgentRegistry`, runs it, and writes the
    :class:`~agentry.agents.models.AgentResult` to the output path.

    Args:
        config_path: Path to the agent configuration JSON file.
        output_path: Path to write the result JSON file.

    Returns:
        Exit code: 0 for success, 1 for agent error, 2 for shim error.
    """
    try:
        config = load_config(config_path)
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        error_result = {
            "error": f"Shim startup error: {exc}",
            "exit_code": 2,
        }
        write_result(output_path, error_result)
        return 2

    try:
        # Import here to allow the module to be importable without the full
        # agentry stack (useful for testing the shim's config parsing).
        from agentry.agents.models import AgentTask  # noqa: PLC0415
        from agentry.agents.registry import AgentRegistry  # noqa: PLC0415

        agent_name: str = config.get("agent_name", "claude-code")
        agent_config: dict[str, Any] = config.get("agent_config", {})

        registry = AgentRegistry.default()
        agent = registry.get(agent_name, **agent_config)

        # Build an AgentTask from the resolved config.
        task_parts: list[str] = []
        resolved_inputs: dict[str, str] = config.get("resolved_inputs", {})
        for key, value in resolved_inputs.items():
            task_parts.append(f"{key}:\n{value}")
        task_description = "\n\n".join(task_parts) if task_parts else ""

        task = AgentTask(
            system_prompt=config["system_prompt"],
            task_description=task_description,
            tool_names=config.get("tool_names", []),
            timeout=config.get("timeout"),
            working_directory="/workspace",
        )

        agent_result = agent.execute(task)

        result: dict[str, Any] = {
            "exit_code": 1 if agent_result.error else 0,
            "raw_output": agent_result.raw_output,
            "error": agent_result.error,
            "timed_out": agent_result.timed_out,
            "token_usage": agent_result.token_usage.model_dump(),
            "tool_invocations": agent_result.tool_invocations,
        }
        if agent_result.output is not None:
            result["output"] = agent_result.output

        write_result(output_path, result)
        return 1 if agent_result.error else 0

    except Exception as exc:
        error_result = {
            "error": f"Shim execution error: {exc}",
            "traceback": traceback.format_exc(),
            "exit_code": 1,
        }
        write_result(output_path, error_result)
        return 1


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point for ``python -m agentry.runners.shim``."""
    config_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CONFIG_PATH
    output_path = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUTPUT_PATH
    sys.exit(run_shim(config_path, output_path))


if __name__ == "__main__":
    main()
