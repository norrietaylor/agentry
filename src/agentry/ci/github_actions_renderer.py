"""Render GitHub Actions YAML from a workflow definition and CLI parameters.

Produces a well-structured GitHub Actions workflow YAML file from a parsed
:class:`~agentry.models.workflow.WorkflowDefinition`, a list of trigger names,
an optional cron schedule, and the original workflow file path.

Uses :mod:`yaml` (PyYAML) for structured serialization so that quoting and
escaping are handled correctly.
"""

from __future__ import annotations

from typing import Any

import yaml

from agentry.models.workflow import WorkflowDefinition

# ---------------------------------------------------------------------------
# Tool capability -> GitHub Actions permission mapping
# ---------------------------------------------------------------------------

# Maps tool capability prefixes to the GitHub Actions permissions they require.
# Only the minimal set of permissions is declared.
_TOOL_PERMISSION_MAP: dict[str, dict[str, str]] = {
    "pr:comment": {"pull-requests": "write"},
    "pr:review": {"pull-requests": "write"},
    "pr:": {"pull-requests": "write"},
    "issue:": {"issues": "write"},
    "repository:read": {"contents": "read"},
    "repository:write": {"contents": "write"},
    "repository:": {"contents": "read"},
}


def _derive_permissions(tool_capabilities: list[str]) -> dict[str, str]:
    """Derive minimal GitHub Actions permissions from tool capabilities.

    Always includes ``contents: read``. Elevates to ``write`` only when
    the tool manifest explicitly requires it.

    Args:
        tool_capabilities: List of tool capability strings from the workflow.

    Returns:
        A dict mapping GitHub Actions permission scope to access level.
    """
    permissions: dict[str, str] = {"contents": "read"}

    for capability in tool_capabilities:
        for prefix, perm in _TOOL_PERMISSION_MAP.items():
            if capability == prefix or capability.startswith(prefix):
                for scope, level in perm.items():
                    existing = permissions.get(scope)
                    # Only upgrade; never downgrade.
                    if existing is None or (existing == "read" and level == "write"):
                        permissions[scope] = level

    return permissions


def _build_triggers(
    trigger_list: list[str],
    schedule: str | None,
) -> dict[str, Any]:
    """Build the ``on:`` section of a GitHub Actions workflow.

    Args:
        trigger_list: List of trigger names (e.g. ``["pull_request", "push"]``).
        schedule: Cron expression string, required when ``"schedule"`` is in
            *trigger_list*.

    Returns:
        A dict suitable for the ``on`` key in a GitHub Actions workflow.
    """
    triggers: dict[str, Any] = {}

    for trigger in trigger_list:
        if trigger == "schedule":
            triggers["schedule"] = [{"cron": schedule}]
        elif trigger == "pull_request":
            triggers["pull_request"] = {}
        elif trigger == "push":
            triggers["push"] = {}
        elif trigger == "issues":
            triggers["issues"] = {"types": ["opened", "edited"]}
        else:
            # Unknown triggers are passed through as empty dicts.
            triggers[trigger] = {}

    return triggers


def _build_steps(workflow_path: str) -> list[dict[str, Any]]:
    """Build the ``steps`` list for the agentry job.

    Args:
        workflow_path: Path to the workflow YAML file, used in the ``agentry run``
            command.

    Returns:
        A list of step dicts for the GitHub Actions job.
    """
    return [
        {
            "name": "Checkout repository",
            "uses": "actions/checkout@v4",
        },
        {
            "name": "Set up Python",
            "uses": "actions/setup-python@v5",
            "with": {"python-version": "3.12"},
        },
        {
            "name": "Install agentry",
            "run": "pip install agentry",
        },
        {
            "name": "Run agentry",
            "run": f"agentry run {workflow_path}",
            "env": {
                "ANTHROPIC_API_KEY": "${{ secrets.ANTHROPIC_API_KEY }}",
                "GITHUB_TOKEN": "${{ secrets.GITHUB_TOKEN }}",
            },
        },
    ]


def render_pipeline_yaml(
    workflow: WorkflowDefinition,
    workflow_path: str,
    trigger_list: list[str],
    schedule: str | None = None,
) -> str:
    """Render a complete GitHub Actions workflow YAML string.

    Builds the full pipeline configuration from the workflow definition and
    generation parameters, then serializes it via :func:`yaml.dump`.

    Args:
        workflow: The parsed workflow definition.
        workflow_path: Path to the workflow YAML file.
        trigger_list: List of trigger names.
        schedule: Optional cron expression (required when ``"schedule"`` is in
            *trigger_list*).

    Returns:
        A YAML string representing the GitHub Actions workflow.
    """
    workflow_name = f"Agentry: {workflow.identity.name}"
    permissions = _derive_permissions(workflow.tools.capabilities)
    triggers = _build_triggers(trigger_list, schedule)
    steps = _build_steps(workflow_path)

    pipeline: dict[str, Any] = {
        "name": workflow_name,
        "on": triggers,
        "permissions": permissions,
        "jobs": {
            "agentry": {
                "runs-on": "ubuntu-latest",
                "steps": steps,
            },
        },
    }

    # Use default_flow_style=False for block-style YAML output, and
    # sort_keys=False to preserve the logical ordering of top-level keys.
    return yaml.dump(pipeline, default_flow_style=False, sort_keys=False)
