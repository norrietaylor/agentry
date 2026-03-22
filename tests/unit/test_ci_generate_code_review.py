"""Unit tests for GitHubActionsBinder.generate_pipeline_config() with code-review workflow (T03.2).

Tests verify that the GitHubActionsBinder.generate_pipeline_config() method produces
correct GitHub Actions workflow configuration for the code-review workflow, matching
the structure and expectations of the committed .github/workflows/agentry-code-review.yml file.

This test suite documents the structure of the generated pipeline config and serves as
a specification for the code-review workflow's CI configuration requirements.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agentry.binders.github_actions import GitHubActionsBinder


@pytest.fixture
def mock_env(tmp_path: Path) -> dict[str, str]:
    """Create mock GitHub Actions environment variables for testing."""
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps({"pull_request": {"number": 1}}))

    return {
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_EVENT_PATH": str(event_file),
        "GITHUB_WORKSPACE": str(tmp_path / "workspace"),
        "GITHUB_REPOSITORY": "test/repo",
        "GITHUB_TOKEN": "test-token",
    }


class TestGitHubActionsBinder:
    """Unit tests for GitHubActionsBinder.generate_pipeline_config()."""

    def test_generate_pipeline_config_basic_structure(self, mock_env: dict[str, str]) -> None:
        """generate_pipeline_config() returns a dict with required top-level keys."""
        binder = GitHubActionsBinder(env=mock_env)
        config = binder.generate_pipeline_config(
            workflow_name="code-review",
            triggers=["pull_request"],
            tool_declarations=[],
        )

        assert isinstance(config, dict)
        assert "name" in config
        assert "on" in config
        assert "permissions" in config
        assert "jobs" in config

    def test_generate_pipeline_config_workflow_name_in_name_field(self, mock_env: dict[str, str]) -> None:
        """Generated config name includes 'Agentry:' prefix and workflow name."""
        binder = GitHubActionsBinder(env=mock_env)
        config = binder.generate_pipeline_config(
            workflow_name="code-review",
            triggers=["pull_request"],
        )

        assert config["name"] == "Agentry: code-review"

    def test_generate_pipeline_config_pull_request_trigger(self, mock_env: dict[str, str]) -> None:
        """Generated config with pull_request trigger includes on.pull_request."""
        binder = GitHubActionsBinder(env=mock_env)
        config = binder.generate_pipeline_config(
            workflow_name="code-review",
            triggers=["pull_request"],
        )

        assert "pull_request" in config["on"]

    def test_generate_pipeline_config_default_permissions_includes_contents_read(
        self, mock_env: dict[str, str]
    ) -> None:
        """Generated config always includes contents:read permission."""
        binder = GitHubActionsBinder(env=mock_env)
        config = binder.generate_pipeline_config(
            workflow_name="code-review",
            triggers=["pull_request"],
        )

        assert config["permissions"].get("contents") == "read"

    def test_generate_pipeline_config_pr_comment_tool_adds_pull_requests_write(
        self, mock_env: dict[str, str]
    ) -> None:
        """Workflow with pr:comment tool gets pull-requests:write permission."""
        binder = GitHubActionsBinder(env=mock_env)
        config = binder.generate_pipeline_config(
            workflow_name="code-review",
            triggers=["pull_request"],
            tool_declarations=["pr:comment"],
        )

        assert config["permissions"].get("pull-requests") == "write"

    def test_generate_pipeline_config_pr_review_tool_adds_pull_requests_write(
        self, mock_env: dict[str, str]
    ) -> None:
        """Workflow with pr:review tool gets pull-requests:write permission."""
        binder = GitHubActionsBinder(env=mock_env)
        config = binder.generate_pipeline_config(
            workflow_name="code-review",
            triggers=["pull_request"],
            tool_declarations=["pr:review"],
        )

        assert config["permissions"].get("pull-requests") == "write"

    def test_generate_pipeline_config_jobs_has_agentry_job(self, mock_env: dict[str, str]) -> None:
        """Generated config jobs section includes an 'agentry' job."""
        binder = GitHubActionsBinder(env=mock_env)
        config = binder.generate_pipeline_config(
            workflow_name="code-review",
            triggers=["pull_request"],
        )

        assert "agentry" in config["jobs"]

    def test_generate_pipeline_config_agentry_job_runs_on_ubuntu_latest(
        self, mock_env: dict[str, str]
    ) -> None:
        """The agentry job runs on ubuntu-latest."""
        binder = GitHubActionsBinder(env=mock_env)
        config = binder.generate_pipeline_config(
            workflow_name="code-review",
            triggers=["pull_request"],
        )

        assert config["jobs"]["agentry"]["runs-on"] == "ubuntu-latest"

    def test_generate_pipeline_config_agentry_job_has_steps(self, mock_env: dict[str, str]) -> None:
        """The agentry job has a non-empty steps list."""
        binder = GitHubActionsBinder(env=mock_env)
        config = binder.generate_pipeline_config(
            workflow_name="code-review",
            triggers=["pull_request"],
        )

        assert "steps" in config["jobs"]["agentry"]
        assert isinstance(config["jobs"]["agentry"]["steps"], list)
        assert len(config["jobs"]["agentry"]["steps"]) > 0

    def test_generate_pipeline_config_steps_include_checkout(self, mock_env: dict[str, str]) -> None:
        """Steps include a checkout action step."""
        binder = GitHubActionsBinder(env=mock_env)
        config = binder.generate_pipeline_config(
            workflow_name="code-review",
            triggers=["pull_request"],
        )

        steps = config["jobs"]["agentry"]["steps"]
        checkout = next(
            (s for s in steps if s.get("uses", "").startswith("actions/checkout")),
            None,
        )
        assert checkout is not None

    def test_generate_pipeline_config_steps_include_setup_python(self, mock_env: dict[str, str]) -> None:
        """Steps include a setup-python action step."""
        binder = GitHubActionsBinder(env=mock_env)
        config = binder.generate_pipeline_config(
            workflow_name="code-review",
            triggers=["pull_request"],
        )

        steps = config["jobs"]["agentry"]["steps"]
        setup = next(
            (s for s in steps if s.get("uses", "").startswith("actions/setup-python")),
            None,
        )
        assert setup is not None

    def test_generate_pipeline_config_setup_python_uses_version_5(self, mock_env: dict[str, str]) -> None:
        """setup-python step uses version 5."""
        binder = GitHubActionsBinder(env=mock_env)
        config = binder.generate_pipeline_config(
            workflow_name="code-review",
            triggers=["pull_request"],
        )

        steps = config["jobs"]["agentry"]["steps"]
        setup = next(
            (s for s in steps if s.get("uses", "").startswith("actions/setup-python")),
            None,
        )
        assert setup is not None
        assert "setup-python@v5" in setup["uses"]

    def test_generate_pipeline_config_setup_python_specifies_python_3_12(
        self, mock_env: dict[str, str]
    ) -> None:
        """setup-python step configures Python 3.12."""
        binder = GitHubActionsBinder(env=mock_env)
        config = binder.generate_pipeline_config(
            workflow_name="code-review",
            triggers=["pull_request"],
        )

        steps = config["jobs"]["agentry"]["steps"]
        setup = next(
            (s for s in steps if s.get("uses", "").startswith("actions/setup-python")),
            None,
        )
        assert setup is not None
        assert setup["with"]["python-version"] == "3.12"

    def test_generate_pipeline_config_steps_include_install(self, mock_env: dict[str, str]) -> None:
        """Steps include an install agentry step."""
        binder = GitHubActionsBinder(env=mock_env)
        config = binder.generate_pipeline_config(
            workflow_name="code-review",
            triggers=["pull_request"],
        )

        steps = config["jobs"]["agentry"]["steps"]
        install = next(
            (s for s in steps if "run" in s and "pip install" in s["run"]),
            None,
        )
        assert install is not None

    def test_generate_pipeline_config_steps_include_run_agentry(self, mock_env: dict[str, str]) -> None:
        """Steps include a run agentry step."""
        binder = GitHubActionsBinder(env=mock_env)
        config = binder.generate_pipeline_config(
            workflow_name="code-review",
            triggers=["pull_request"],
        )

        steps = config["jobs"]["agentry"]["steps"]
        run = next(
            (s for s in steps if "run" in s and "agentry run" in s["run"]),
            None,
        )
        assert run is not None

    def test_generate_pipeline_config_run_step_includes_workflow_path(
        self, mock_env: dict[str, str]
    ) -> None:
        """Run agentry step includes the specified workflow path."""
        binder = GitHubActionsBinder(env=mock_env)
        workflow_path = "workflows/code-review.yaml"
        config = binder.generate_pipeline_config(
            workflow_name="code-review",
            triggers=["pull_request"],
            workflow_path=workflow_path,
        )

        steps = config["jobs"]["agentry"]["steps"]
        run = next(
            (s for s in steps if "run" in s and "agentry run" in s["run"]),
            None,
        )
        assert run is not None
        assert workflow_path in run["run"]

    def test_generate_pipeline_config_run_step_env_includes_anthropic_api_key(
        self, mock_env: dict[str, str]
    ) -> None:
        """Run agentry step env includes ANTHROPIC_API_KEY secret."""
        binder = GitHubActionsBinder(env=mock_env)
        config = binder.generate_pipeline_config(
            workflow_name="code-review",
            triggers=["pull_request"],
        )

        steps = config["jobs"]["agentry"]["steps"]
        run = next(
            (s for s in steps if "run" in s and "agentry run" in s["run"]),
            None,
        )
        assert run is not None
        assert "env" in run
        assert "ANTHROPIC_API_KEY" in run["env"]
        assert "secrets.ANTHROPIC_API_KEY" in run["env"]["ANTHROPIC_API_KEY"]

    def test_generate_pipeline_config_run_step_env_includes_github_token(
        self, mock_env: dict[str, str]
    ) -> None:
        """Run agentry step env includes GITHUB_TOKEN secret."""
        binder = GitHubActionsBinder(env=mock_env)
        config = binder.generate_pipeline_config(
            workflow_name="code-review",
            triggers=["pull_request"],
        )

        steps = config["jobs"]["agentry"]["steps"]
        run = next(
            (s for s in steps if "run" in s and "agentry run" in s["run"]),
            None,
        )
        assert run is not None
        assert "env" in run
        assert "GITHUB_TOKEN" in run["env"]
        assert "secrets.GITHUB_TOKEN" in run["env"]["GITHUB_TOKEN"]

    def test_generate_pipeline_config_with_multiple_triggers(self, mock_env: dict[str, str]) -> None:
        """Config with multiple triggers includes all triggers in on block."""
        binder = GitHubActionsBinder(env=mock_env)
        config = binder.generate_pipeline_config(
            workflow_name="code-review",
            triggers=["pull_request", "push"],
        )

        assert "pull_request" in config["on"]
        assert "push" in config["on"]

    def test_generate_pipeline_config_with_schedule_trigger(self, mock_env: dict[str, str]) -> None:
        """Config with schedule trigger includes cron expression."""
        binder = GitHubActionsBinder(env=mock_env)
        cron = "0 2 * * 1"
        config = binder.generate_pipeline_config(
            workflow_name="code-review",
            triggers=["schedule"],
            schedule=cron,
        )

        assert "schedule" in config["on"]
        assert config["on"]["schedule"] == [{"cron": cron}]

    def test_generate_pipeline_config_structure_serializable_to_yaml(
        self, mock_env: dict[str, str]
    ) -> None:
        """Generated config can be serialized to YAML without error."""
        binder = GitHubActionsBinder(env=mock_env)
        config = binder.generate_pipeline_config(
            workflow_name="code-review",
            triggers=["pull_request"],
            tool_declarations=["pr:comment", "repository:read"],
        )

        # Should not raise an exception
        yaml_output = yaml.dump(config, default_flow_style=False)
        assert isinstance(yaml_output, str)
        assert "Agentry: code-review" in yaml_output

    def test_generate_pipeline_config_with_pr_create_tool(self, mock_env: dict[str, str]) -> None:
        """Config with pr:create tool maintains correct permissions."""
        binder = GitHubActionsBinder(env=mock_env)
        config = binder.generate_pipeline_config(
            workflow_name="code-review",
            triggers=["pull_request"],
            tool_declarations=["pr:create"],
        )

        # pr:create is a "pr:" prefix tool, should grant pull-requests:write
        assert config["permissions"].get("pull-requests") == "write"
        assert config["permissions"].get("contents") == "read"

    def test_generate_pipeline_config_default_workflow_path(self, mock_env: dict[str, str]) -> None:
        """generate_pipeline_config() defaults to workflow.yaml if not specified."""
        binder = GitHubActionsBinder(env=mock_env)
        config = binder.generate_pipeline_config(
            workflow_name="code-review",
            triggers=["pull_request"],
        )

        steps = config["jobs"]["agentry"]["steps"]
        run = next(
            (s for s in steps if "run" in s and "agentry run" in s["run"]),
            None,
        )
        assert run is not None
        # Default should be workflow.yaml
        assert "workflow.yaml" in run["run"]

    def test_generate_pipeline_config_env_section_exists(self, mock_env: dict[str, str]) -> None:
        """Generated config has env section with required secrets."""
        binder = GitHubActionsBinder(env=mock_env)
        config = binder.generate_pipeline_config(
            workflow_name="code-review",
            triggers=["pull_request"],
        )

        assert "env" in config
        assert "ANTHROPIC_API_KEY" in config["env"]
        assert "GITHUB_TOKEN" in config["env"]
