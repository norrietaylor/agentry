"""Unit tests for DockerRunner (T01.4 core + T01.5 execute with timeout and shim).

All Docker calls are mocked -- no Docker daemon is required.

Tests cover:
- check_available() returns available=True when daemon ping succeeds.
- check_available() returns available=False when daemon ping fails.
- provision() creates a container with correct image, CPU, memory, user, mounts.
- provision() raises RuntimeError when container creation fails.
- provision() returns RunnerContext with container_id, mount_mappings, metadata.
- provision() sets the shim command on the container.
- teardown() calls container.remove(force=True, v=True).
- teardown() is idempotent: 404 / "not found" does not raise.
- teardown() logs warning on unexpected failure but does not raise.
- teardown() is a no-op when container_id is empty.
- Constructor raises RuntimeError when docker-py is absent and no client given.
- DockerRunner satisfies RunnerProtocol.
- execute() starts container, copies config, and returns result.
- execute() enforces timeout and kills container on expiry.
- execute() returns error when container_id is empty.
- execute() handles container not found gracefully.
- execute() collects stdout/stderr from the container.
- _build_config_payload() serialises AgentConfig correctly.
- Integration test with @pytest.mark.docker marker.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from agentry.models.safety import (
    FilesystemConfig,
    ResourceConfig,
    SafetyBlock,
    SandboxConfig,
)
from agentry.runners.docker_runner import DockerRunner, _build_bind_mounts
from agentry.runners.protocol import (
    AgentConfig,
    ExecutionResult,
    RunnerContext,
    RunnerProtocol,
    RunnerStatus,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_docker_client() -> MagicMock:
    """Return a MagicMock that mimics a docker.DockerClient."""
    client = MagicMock(name="DockerClient")
    client.containers = MagicMock(name="ContainerCollection")
    client.ping = MagicMock(return_value=True)
    return client


def _make_container_mock(container_id: str = "abc123def456") -> MagicMock:
    """Return a MagicMock that mimics a docker.models.containers.Container."""
    container = MagicMock(name="Container")
    container.id = container_id
    return container


def _make_safety_block(**overrides: object) -> SafetyBlock:
    """Build a SafetyBlock with optional overrides."""
    defaults: dict[str, object] = {
        "sandbox": SandboxConfig(base="agentry/sandbox:1.0"),
        "resources": ResourceConfig(cpu=2.0, memory="4GB", timeout=300),
        "filesystem": FilesystemConfig(
            read=["/host/data"],
            write=["/host/results"],
        ),
    }
    defaults.update(overrides)
    return SafetyBlock(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# check_available
# ---------------------------------------------------------------------------


class TestCheckAvailable:
    """Tests for DockerRunner.check_available()."""

    def test_returns_available_when_ping_succeeds(self) -> None:
        client = _make_docker_client()
        runner = DockerRunner(docker_client=client)
        status = runner.check_available()

        assert isinstance(status, RunnerStatus)
        assert status.available is True
        assert "reachable" in status.message.lower()

    def test_returns_unavailable_when_ping_fails(self) -> None:
        client = _make_docker_client()
        client.ping.side_effect = ConnectionError("Cannot connect to Docker daemon")
        runner = DockerRunner(docker_client=client)
        status = runner.check_available()

        assert status.available is False
        assert "not reachable" in status.message.lower()
        assert "Cannot connect" in status.message

    def test_calls_client_ping(self) -> None:
        client = _make_docker_client()
        runner = DockerRunner(docker_client=client)
        runner.check_available()

        client.ping.assert_called_once()


# ---------------------------------------------------------------------------
# provision
# ---------------------------------------------------------------------------


class TestProvision:
    """Tests for DockerRunner.provision()."""

    def test_returns_runner_context_with_container_id(self) -> None:
        client = _make_docker_client()
        container = _make_container_mock("deadbeef" * 8)
        client.containers.create.return_value = container
        safety = _make_safety_block()

        runner = DockerRunner(docker_client=client, codebase_path="/home/user/repo")
        ctx = runner.provision(safety_block=safety, resolved_inputs={})

        assert isinstance(ctx, RunnerContext)
        assert ctx.container_id == "deadbeef" * 8

    def test_uses_correct_image(self) -> None:
        client = _make_docker_client()
        client.containers.create.return_value = _make_container_mock()
        safety = _make_safety_block(sandbox=SandboxConfig(base="custom/image:2.0"))

        runner = DockerRunner(docker_client=client)
        runner.provision(safety_block=safety, resolved_inputs={})

        _, kwargs = client.containers.create.call_args
        assert kwargs["image"] == "custom/image:2.0"

    def test_sets_cpu_limits(self) -> None:
        client = _make_docker_client()
        client.containers.create.return_value = _make_container_mock()
        safety = _make_safety_block(
            resources=ResourceConfig(cpu=2.0, memory="4GB", timeout=300)
        )

        runner = DockerRunner(docker_client=client)
        runner.provision(safety_block=safety, resolved_inputs={})

        _, kwargs = client.containers.create.call_args
        assert kwargs["cpu_period"] == 100_000
        assert kwargs["cpu_quota"] == 200_000  # 2.0 * 100000

    def test_sets_memory_limit(self) -> None:
        client = _make_docker_client()
        client.containers.create.return_value = _make_container_mock()
        safety = _make_safety_block(
            resources=ResourceConfig(cpu=1.0, memory="512MB", timeout=300)
        )

        runner = DockerRunner(docker_client=client)
        runner.provision(safety_block=safety, resolved_inputs={})

        _, kwargs = client.containers.create.call_args
        assert kwargs["mem_limit"] == "512MB"

    def test_sets_non_root_user(self) -> None:
        client = _make_docker_client()
        client.containers.create.return_value = _make_container_mock()
        safety = _make_safety_block()

        runner = DockerRunner(docker_client=client)
        runner.provision(safety_block=safety, resolved_inputs={})

        _, kwargs = client.containers.create.call_args
        assert kwargs["user"] == "1000:1000"

    def test_mounts_codebase_read_only(self) -> None:
        client = _make_docker_client()
        client.containers.create.return_value = _make_container_mock()
        safety = _make_safety_block(filesystem=FilesystemConfig())

        runner = DockerRunner(docker_client=client, codebase_path="/home/user/repo")
        ctx = runner.provision(safety_block=safety, resolved_inputs={})

        _, kwargs = client.containers.create.call_args
        volumes = kwargs["volumes"]
        assert "/home/user/repo:/workspace:ro" in volumes
        assert ctx.mount_mappings["/home/user/repo"] == "/workspace"

    def test_mounts_output_read_write(self) -> None:
        client = _make_docker_client()
        client.containers.create.return_value = _make_container_mock()
        safety = _make_safety_block(filesystem=FilesystemConfig())

        runner = DockerRunner(
            docker_client=client, output_path="/tmp/agentry-out"
        )
        ctx = runner.provision(safety_block=safety, resolved_inputs={})

        _, kwargs = client.containers.create.call_args
        volumes = kwargs["volumes"]
        assert "/tmp/agentry-out:/output:rw" in volumes
        assert ctx.mount_mappings["/tmp/agentry-out"] == "/output"

    def test_mounts_filesystem_read_paths(self) -> None:
        client = _make_docker_client()
        client.containers.create.return_value = _make_container_mock()
        safety = _make_safety_block(
            filesystem=FilesystemConfig(read=["/data/shared", "/data/config"])
        )

        runner = DockerRunner(docker_client=client)
        ctx = runner.provision(safety_block=safety, resolved_inputs={})

        _, kwargs = client.containers.create.call_args
        volumes = kwargs["volumes"]
        assert "/data/shared:/mnt/read/0:ro" in volumes
        assert "/data/config:/mnt/read/1:ro" in volumes
        assert ctx.mount_mappings["/data/shared"] == "/mnt/read/0"
        assert ctx.mount_mappings["/data/config"] == "/mnt/read/1"

    def test_mounts_filesystem_write_paths(self) -> None:
        client = _make_docker_client()
        client.containers.create.return_value = _make_container_mock()
        safety = _make_safety_block(
            filesystem=FilesystemConfig(write=["/tmp/logs"])
        )

        runner = DockerRunner(docker_client=client)
        ctx = runner.provision(safety_block=safety, resolved_inputs={})

        _, kwargs = client.containers.create.call_args
        volumes = kwargs["volumes"]
        assert "/tmp/logs:/mnt/write/0:rw" in volumes
        assert ctx.mount_mappings["/tmp/logs"] == "/mnt/write/0"

    def test_metadata_contains_runner_type(self) -> None:
        client = _make_docker_client()
        client.containers.create.return_value = _make_container_mock()
        safety = _make_safety_block()

        runner = DockerRunner(docker_client=client)
        ctx = runner.provision(safety_block=safety, resolved_inputs={})

        assert ctx.metadata["runner_type"] == "docker"

    def test_metadata_contains_image_and_resources(self) -> None:
        client = _make_docker_client()
        client.containers.create.return_value = _make_container_mock()
        safety = _make_safety_block(
            sandbox=SandboxConfig(base="my/image:3.0"),
            resources=ResourceConfig(cpu=4.0, memory="8GB", timeout=600),
        )

        runner = DockerRunner(docker_client=client)
        ctx = runner.provision(safety_block=safety, resolved_inputs={})

        assert ctx.metadata["image"] == "my/image:3.0"
        assert ctx.metadata["cpu"] == 4.0
        assert ctx.metadata["memory"] == "8GB"
        assert ctx.metadata["user"] == "1000:1000"

    def test_container_has_agentry_labels(self) -> None:
        client = _make_docker_client()
        client.containers.create.return_value = _make_container_mock()
        safety = _make_safety_block()

        runner = DockerRunner(docker_client=client)
        runner.provision(safety_block=safety, resolved_inputs={})

        _, kwargs = client.containers.create.call_args
        labels = kwargs["labels"]
        assert labels["agentry.managed"] == "true"
        assert "agentry.execution_id" in labels

    def test_raises_runtime_error_on_create_failure(self) -> None:
        client = _make_docker_client()
        client.containers.create.side_effect = RuntimeError("Image not found")
        safety = _make_safety_block()

        runner = DockerRunner(docker_client=client)
        with pytest.raises(RuntimeError, match="Failed to provision"):
            runner.provision(safety_block=safety, resolved_inputs={})

    def test_provision_logs_info(self, caplog) -> None:
        client = _make_docker_client()
        client.containers.create.return_value = _make_container_mock()
        safety = _make_safety_block()

        runner = DockerRunner(docker_client=client)
        with caplog.at_level(logging.INFO):
            runner.provision(safety_block=safety, resolved_inputs={})

        assert any("provisioning" in r.message.lower() or "provisioned" in r.message.lower() for r in caplog.records)

    def test_container_name_has_prefix(self) -> None:
        client = _make_docker_client()
        client.containers.create.return_value = _make_container_mock()
        safety = _make_safety_block()

        runner = DockerRunner(docker_client=client)
        runner.provision(safety_block=safety, resolved_inputs={})

        _, kwargs = client.containers.create.call_args
        assert kwargs["name"].startswith("agentry-sandbox-")


# ---------------------------------------------------------------------------
# teardown
# ---------------------------------------------------------------------------


class TestTeardown:
    """Tests for DockerRunner.teardown()."""

    def test_removes_container_with_force_and_volumes(self) -> None:
        client = _make_docker_client()
        container = _make_container_mock("ctr-to-remove")
        client.containers.get.return_value = container

        runner = DockerRunner(docker_client=client)
        ctx = RunnerContext(container_id="ctr-to-remove")
        runner.teardown(ctx)

        client.containers.get.assert_called_once_with("ctr-to-remove")
        container.remove.assert_called_once_with(force=True, v=True)

    def test_idempotent_on_404(self) -> None:
        client = _make_docker_client()
        client.containers.get.side_effect = RuntimeError("404 Not Found")

        runner = DockerRunner(docker_client=client)
        ctx = RunnerContext(container_id="gone-container")
        # Should not raise.
        runner.teardown(ctx)

    def test_idempotent_on_no_such_container(self) -> None:
        client = _make_docker_client()
        client.containers.get.side_effect = RuntimeError("No such container: abc")

        runner = DockerRunner(docker_client=client)
        ctx = RunnerContext(container_id="gone-container")
        # Should not raise.
        runner.teardown(ctx)

    def test_idempotent_on_not_found_string(self) -> None:
        client = _make_docker_client()
        client.containers.get.side_effect = RuntimeError("container not found")

        runner = DockerRunner(docker_client=client)
        ctx = RunnerContext(container_id="gone-container")
        # Should not raise.
        runner.teardown(ctx)

    def test_logs_warning_on_unexpected_failure(self, caplog) -> None:
        client = _make_docker_client()
        client.containers.get.side_effect = RuntimeError("unexpected internal error")

        runner = DockerRunner(docker_client=client)
        ctx = RunnerContext(container_id="problem-container")

        with caplog.at_level(logging.WARNING):
            runner.teardown(ctx)

        assert any("failed to remove" in r.message.lower() for r in caplog.records)

    def test_does_not_raise_on_unexpected_failure(self) -> None:
        client = _make_docker_client()
        client.containers.get.side_effect = RuntimeError("disk I/O error")

        runner = DockerRunner(docker_client=client)
        ctx = RunnerContext(container_id="problem-container")
        # Should not raise -- teardown swallows unexpected errors.
        runner.teardown(ctx)

    def test_no_op_when_container_id_is_empty(self) -> None:
        client = _make_docker_client()
        runner = DockerRunner(docker_client=client)
        ctx = RunnerContext(container_id="")
        runner.teardown(ctx)
        client.containers.get.assert_not_called()

    def test_teardown_when_remove_fails(self, caplog) -> None:
        client = _make_docker_client()
        container = _make_container_mock("remove-fail")
        container.remove.side_effect = RuntimeError("container is paused")
        client.containers.get.return_value = container

        runner = DockerRunner(docker_client=client)
        ctx = RunnerContext(container_id="remove-fail")

        with caplog.at_level(logging.WARNING):
            runner.teardown(ctx)

        assert any("failed to remove" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


class TestConstructor:
    """Tests for DockerRunner constructor."""

    def test_raises_runtime_error_without_docker_installed(self) -> None:
        with patch("agentry.runners.docker_runner._DOCKER_AVAILABLE", False), pytest.raises(
            RuntimeError, match="docker-py is not installed"
        ):
            DockerRunner()

    def test_accepts_explicit_client(self) -> None:
        client = _make_docker_client()
        runner = DockerRunner(docker_client=client)
        assert runner is not None

    def test_stores_codebase_path(self) -> None:
        client = _make_docker_client()
        runner = DockerRunner(docker_client=client, codebase_path="/my/code")
        assert runner._codebase_path == "/my/code"

    def test_stores_output_path(self) -> None:
        client = _make_docker_client()
        runner = DockerRunner(docker_client=client, output_path="/my/output")
        assert runner._output_path == "/my/output"


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


class TestProtocolCompliance:
    """Verify DockerRunner satisfies RunnerProtocol."""

    def test_satisfies_runner_protocol(self) -> None:
        client = _make_docker_client()
        runner = DockerRunner(docker_client=client)
        assert isinstance(runner, RunnerProtocol)


# ---------------------------------------------------------------------------
# execute stub
# ---------------------------------------------------------------------------


def _make_agent_config(**overrides: object) -> AgentConfig:
    """Build an AgentConfig with sensible defaults."""
    defaults: dict[str, object] = {
        "system_prompt": "You are a helpful assistant.",
        "resolved_inputs": {"diff": "test diff content"},
        "tool_names": ["repository:read"],
        "llm_config": {"model": "claude-3-sonnet", "temperature": 0.0},
        "timeout": 60.0,
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)  # type: ignore[arg-type]


def _setup_execute_mocks(
    client: MagicMock,
    container: MagicMock,
    exit_code: int = 0,
    stdout: bytes = b"agent output",
    stderr: bytes = b"",
) -> None:
    """Configure container mock for a successful execute() flow."""
    client.containers.get.return_value = container
    container.wait.return_value = {"StatusCode": exit_code}
    container.logs.side_effect = lambda stdout=False, stderr=False: (
        stdout if stdout else stderr if stderr else b""
    )
    # Provide distinct stdout/stderr
    def _logs_side_effect(stdout=False, stderr=False):  # noqa: FBT002
        if stdout:
            return stdout
        if stderr:
            return stderr
        return b""

    # Override with actual bytes
    container.logs.side_effect = None
    container.logs.return_value = stdout


class TestExecute:
    """Tests for DockerRunner.execute() (T01.5)."""

    def test_returns_error_when_no_container_id(self) -> None:
        client = _make_docker_client()
        runner = DockerRunner(docker_client=client)
        ctx = RunnerContext(container_id="")
        config = _make_agent_config()

        result = runner.execute(runner_context=ctx, agent_config=config)

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == 1
        assert "no container id" in result.error.lower()

    def test_returns_error_when_container_not_found(self) -> None:
        client = _make_docker_client()
        client.containers.get.side_effect = RuntimeError("No such container")
        runner = DockerRunner(docker_client=client)
        ctx = RunnerContext(container_id="missing-container")
        config = _make_agent_config()

        result = runner.execute(runner_context=ctx, agent_config=config)

        assert result.exit_code == 1
        assert "cannot find container" in result.error.lower()

    def test_starts_container(self) -> None:
        client = _make_docker_client()
        container = _make_container_mock("exec-container-id")
        _setup_execute_mocks(client, container, exit_code=0)

        output_dir = tempfile.mkdtemp(prefix="agentry-test-output-")
        runner = DockerRunner(docker_client=client, output_path=output_dir)
        ctx = RunnerContext(container_id="exec-container-id")
        config = _make_agent_config()

        runner.execute(runner_context=ctx, agent_config=config)

        container.start.assert_called_once()

    def test_copies_config_to_container(self) -> None:
        client = _make_docker_client()
        container = _make_container_mock("exec-container-id")
        _setup_execute_mocks(client, container, exit_code=0)

        output_dir = tempfile.mkdtemp(prefix="agentry-test-output-")
        runner = DockerRunner(docker_client=client, output_path=output_dir)
        ctx = RunnerContext(container_id="exec-container-id")
        config = _make_agent_config()

        runner.execute(runner_context=ctx, agent_config=config)

        container.put_archive.assert_called_once()
        call_args = container.put_archive.call_args
        assert call_args[0][0] == "/config"

    def test_waits_for_container_with_timeout(self) -> None:
        client = _make_docker_client()
        container = _make_container_mock("exec-container-id")
        _setup_execute_mocks(client, container, exit_code=0)

        output_dir = tempfile.mkdtemp(prefix="agentry-test-output-")
        runner = DockerRunner(docker_client=client, output_path=output_dir)
        ctx = RunnerContext(container_id="exec-container-id")
        config = _make_agent_config(timeout=120.0)

        runner.execute(runner_context=ctx, agent_config=config)

        container.wait.assert_called_once_with(timeout=120.0)

    def test_successful_execution_returns_zero_exit_code(self) -> None:
        client = _make_docker_client()
        container = _make_container_mock("exec-container-id")
        _setup_execute_mocks(client, container, exit_code=0)

        output_dir = tempfile.mkdtemp(prefix="agentry-test-output-")
        runner = DockerRunner(docker_client=client, output_path=output_dir)
        ctx = RunnerContext(container_id="exec-container-id")
        config = _make_agent_config()

        result = runner.execute(runner_context=ctx, agent_config=config)

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == 0
        assert result.timed_out is False
        assert result.error == ""

    def test_nonzero_exit_code_produces_error(self) -> None:
        client = _make_docker_client()
        container = _make_container_mock("exec-container-id")
        _setup_execute_mocks(client, container, exit_code=1)

        output_dir = tempfile.mkdtemp(prefix="agentry-test-output-")
        runner = DockerRunner(docker_client=client, output_path=output_dir)
        ctx = RunnerContext(container_id="exec-container-id")
        config = _make_agent_config()

        result = runner.execute(runner_context=ctx, agent_config=config)

        assert result.exit_code == 1
        assert result.error != ""

    def test_returns_error_when_start_fails(self) -> None:
        client = _make_docker_client()
        container = _make_container_mock("exec-container-id")
        client.containers.get.return_value = container
        container.start.side_effect = RuntimeError("Cannot start container")

        output_dir = tempfile.mkdtemp(prefix="agentry-test-output-")
        runner = DockerRunner(docker_client=client, output_path=output_dir)
        ctx = RunnerContext(container_id="exec-container-id")
        config = _make_agent_config()

        result = runner.execute(runner_context=ctx, agent_config=config)

        assert result.exit_code == 1
        assert "failed to start" in result.error.lower()

    def test_runner_metadata_includes_container_info(self) -> None:
        client = _make_docker_client()
        container = _make_container_mock("exec-container-id")
        _setup_execute_mocks(client, container, exit_code=0)

        output_dir = tempfile.mkdtemp(prefix="agentry-test-output-")
        runner = DockerRunner(docker_client=client, output_path=output_dir)
        ctx = RunnerContext(container_id="exec-container-id")
        config = _make_agent_config()

        result = runner.execute(runner_context=ctx, agent_config=config)

        assert result.runner_metadata["runner_type"] == "docker"
        assert result.runner_metadata["container_id"] == "exec-container-id"


class TestExecuteTimeout:
    """Tests for timeout enforcement in DockerRunner.execute()."""

    def test_kills_container_on_timeout(self) -> None:
        client = _make_docker_client()
        container = _make_container_mock("timeout-container")
        client.containers.get.return_value = container
        container.wait.side_effect = RuntimeError("Read timed out")
        container.logs.return_value = b""

        output_dir = tempfile.mkdtemp(prefix="agentry-test-output-")
        runner = DockerRunner(docker_client=client, output_path=output_dir)
        ctx = RunnerContext(container_id="timeout-container")
        config = _make_agent_config(timeout=5.0)

        result = runner.execute(runner_context=ctx, agent_config=config)

        assert result.timed_out is True
        assert result.exit_code == 137  # SIGKILL
        container.kill.assert_called_once_with(signal="SIGKILL")

    def test_timeout_error_message_includes_duration(self) -> None:
        client = _make_docker_client()
        container = _make_container_mock("timeout-container")
        client.containers.get.return_value = container
        container.wait.side_effect = RuntimeError("timeout")
        container.logs.return_value = b""

        output_dir = tempfile.mkdtemp(prefix="agentry-test-output-")
        runner = DockerRunner(docker_client=client, output_path=output_dir)
        ctx = RunnerContext(container_id="timeout-container")
        config = _make_agent_config(timeout=10.0)

        result = runner.execute(runner_context=ctx, agent_config=config)

        assert result.timed_out is True
        assert "timed out" in result.error.lower()
        assert "10.0s" in result.error

    def test_non_timeout_wait_error_returns_error_result(self) -> None:
        client = _make_docker_client()
        container = _make_container_mock("error-container")
        client.containers.get.return_value = container
        container.wait.side_effect = RuntimeError("Docker daemon crashed")

        output_dir = tempfile.mkdtemp(prefix="agentry-test-output-")
        runner = DockerRunner(docker_client=client, output_path=output_dir)
        ctx = RunnerContext(container_id="error-container")
        # No timeout set so this should not be treated as a timeout.
        config = _make_agent_config(timeout=None)

        result = runner.execute(runner_context=ctx, agent_config=config)

        assert result.exit_code == 1
        assert result.timed_out is False
        assert "error waiting for container" in result.error.lower()

    def test_kill_failure_is_handled_gracefully(self, caplog) -> None:
        client = _make_docker_client()
        container = _make_container_mock("unkillable-container")
        client.containers.get.return_value = container
        container.wait.side_effect = RuntimeError("timed out")
        container.kill.side_effect = RuntimeError("Cannot kill")
        container.logs.return_value = b""

        output_dir = tempfile.mkdtemp(prefix="agentry-test-output-")
        runner = DockerRunner(docker_client=client, output_path=output_dir)
        ctx = RunnerContext(container_id="unkillable-container")
        config = _make_agent_config(timeout=5.0)

        with caplog.at_level(logging.WARNING):
            result = runner.execute(runner_context=ctx, agent_config=config)

        assert result.timed_out is True
        assert any("failed to kill" in r.message.lower() for r in caplog.records)


class TestExecuteConfigPayload:
    """Tests for _build_config_payload static method."""

    def test_serialises_basic_config(self) -> None:
        config = _make_agent_config(
            system_prompt="Review code",
            resolved_inputs={"diff": "abc"},
            tool_names=["shell:execute"],
            llm_config={"model": "claude-3"},
            timeout=30.0,
        )

        payload = DockerRunner._build_config_payload(config)

        assert payload["system_prompt"] == "Review code"
        assert payload["resolved_inputs"] == {"diff": "abc"}
        assert payload["tool_names"] == ["shell:execute"]
        assert payload["llm_config"] == {"model": "claude-3"}
        assert payload["timeout"] == 30.0

    def test_omits_timeout_when_none(self) -> None:
        config = _make_agent_config(timeout=None)

        payload = DockerRunner._build_config_payload(config)

        assert "timeout" not in payload

    def test_serialises_object_llm_config(self) -> None:
        class FakeLLMConfig:
            def __init__(self):
                self.model = "claude-3-opus"
                self.temperature = 0.5

        config = _make_agent_config(llm_config=FakeLLMConfig())

        payload = DockerRunner._build_config_payload(config)

        assert payload["llm_config"]["model"] == "claude-3-opus"
        assert payload["llm_config"]["temperature"] == 0.5


class TestProvisionCommand:
    """Tests that provision() sets the shim command on the container."""

    def test_provision_sets_shim_command(self) -> None:
        client = _make_docker_client()
        client.containers.create.return_value = _make_container_mock()
        safety = _make_safety_block()

        runner = DockerRunner(docker_client=client)
        runner.provision(safety_block=safety, resolved_inputs={})

        _, kwargs = client.containers.create.call_args
        assert kwargs["command"] == [
            "python", "-m", "agentry.runners.shim",
            "/config/agent_config.json",
            "/output/result.json",
        ]


class TestReadResultFile:
    """Tests for DockerRunner._read_result_file."""

    def test_reads_valid_result(self) -> None:
        output_dir = tempfile.mkdtemp(prefix="agentry-test-output-")
        result_path = os.path.join(output_dir, "result.json")
        with open(result_path, "w") as fh:
            json.dump({"exit_code": 0, "final_content": "ok"}, fh)

        client = _make_docker_client()
        runner = DockerRunner(docker_client=client, output_path=output_dir)
        result = runner._read_result_file()

        assert result["exit_code"] == 0
        assert result["final_content"] == "ok"

    def test_returns_error_when_file_missing(self) -> None:
        output_dir = tempfile.mkdtemp(prefix="agentry-test-output-")

        client = _make_docker_client()
        runner = DockerRunner(docker_client=client, output_path=output_dir)
        result = runner._read_result_file()

        assert "error" in result
        assert "not found" in result["error"].lower()

    def test_returns_error_on_invalid_json(self) -> None:
        output_dir = tempfile.mkdtemp(prefix="agentry-test-output-")
        result_path = os.path.join(output_dir, "result.json")
        with open(result_path, "w") as fh:
            fh.write("not valid json {{{")

        client = _make_docker_client()
        runner = DockerRunner(docker_client=client, output_path=output_dir)
        result = runner._read_result_file()

        assert "error" in result
        assert "invalid json" in result["error"].lower()


# ---------------------------------------------------------------------------
# _build_bind_mounts helper
# ---------------------------------------------------------------------------


class TestBuildBindMounts:
    """Tests for the _build_bind_mounts helper function."""

    def test_basic_mounts(self) -> None:
        safety = SafetyBlock(filesystem=FilesystemConfig())
        binds, mappings = _build_bind_mounts(safety, "/code", "/out")

        assert "/code:/workspace:ro" in binds
        assert "/out:/output:rw" in binds
        assert mappings["/code"] == "/workspace"
        assert mappings["/out"] == "/output"

    def test_read_mounts(self) -> None:
        safety = SafetyBlock(
            filesystem=FilesystemConfig(read=["/data/a", "/data/b"])
        )
        binds, mappings = _build_bind_mounts(safety, "/code", "/out")

        assert "/data/a:/mnt/read/0:ro" in binds
        assert "/data/b:/mnt/read/1:ro" in binds
        assert mappings["/data/a"] == "/mnt/read/0"
        assert mappings["/data/b"] == "/mnt/read/1"

    def test_write_mounts(self) -> None:
        safety = SafetyBlock(
            filesystem=FilesystemConfig(write=["/tmp/results"])
        )
        binds, mappings = _build_bind_mounts(safety, "/code", "/out")

        assert "/tmp/results:/mnt/write/0:rw" in binds
        assert mappings["/tmp/results"] == "/mnt/write/0"


# ---------------------------------------------------------------------------
# Full lifecycle (unit, mocked)
# ---------------------------------------------------------------------------


class TestFullLifecycle:
    """Full provision -> teardown lifecycle with mocked Docker client."""

    def test_provision_then_teardown(self) -> None:
        client = _make_docker_client()
        container = _make_container_mock("lifecycle-container-id")
        client.containers.create.return_value = container
        client.containers.get.return_value = container

        safety = _make_safety_block()
        runner = DockerRunner(
            docker_client=client,
            codebase_path="/home/user/repo",
            output_path="/tmp/output",
        )

        # 1. Check availability.
        status = runner.check_available()
        assert status.available

        # 2. Provision.
        ctx = runner.provision(safety_block=safety, resolved_inputs={})
        assert ctx.container_id == "lifecycle-container-id"
        assert ctx.metadata["runner_type"] == "docker"
        assert ctx.mount_mappings["/home/user/repo"] == "/workspace"

        # 3. Teardown.
        runner.teardown(ctx)
        container.remove.assert_called_once_with(force=True, v=True)


# ---------------------------------------------------------------------------
# Integration test marker
# ---------------------------------------------------------------------------


@pytest.mark.docker
class TestDockerRunnerIntegration:
    """Integration tests that require a running Docker daemon.

    These tests are marked with @pytest.mark.docker and will be skipped by
    default. Run with ``pytest -m docker`` to execute them.
    """

    def test_check_available_with_real_daemon(self) -> None:
        """check_available() returns True with a real Docker daemon."""
        try:
            import docker as _docker  # type: ignore[import-untyped]
        except ImportError:
            pytest.skip("docker-py not installed")

        try:
            client = _docker.from_env()
            client.ping()
        except Exception:
            pytest.skip("Docker daemon not available")

        runner = DockerRunner(docker_client=client)
        status = runner.check_available()
        assert status.available is True
