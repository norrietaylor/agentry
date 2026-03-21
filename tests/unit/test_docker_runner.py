"""Unit tests for T01.4: DockerRunner core (provision, teardown, check_available).

All Docker calls are mocked -- no Docker daemon is required.

Tests cover:
- check_available() returns available=True when daemon ping succeeds.
- check_available() returns available=False when daemon ping fails.
- provision() creates a container with correct image, CPU, memory, user, mounts.
- provision() raises RuntimeError when container creation fails.
- provision() returns RunnerContext with container_id, mount_mappings, metadata.
- teardown() calls container.remove(force=True, v=True).
- teardown() is idempotent: 404 / "not found" does not raise.
- teardown() logs warning on unexpected failure but does not raise.
- teardown() is a no-op when container_id is empty.
- Constructor raises RuntimeError when docker-py is absent and no client given.
- DockerRunner satisfies RunnerProtocol.
- execute() returns a stub error result (pending T01.5).
- Integration test with @pytest.mark.docker marker.
"""

from __future__ import annotations

import logging
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


class TestExecuteStub:
    """Tests for the execute() stub (pending T01.5)."""

    def test_returns_error_result(self) -> None:
        client = _make_docker_client()
        runner = DockerRunner(docker_client=client)
        ctx = RunnerContext(container_id="some-container")
        config = AgentConfig(
            system_prompt="test",
            resolved_inputs={},
            tool_names=[],
            llm_config=None,
        )
        result = runner.execute(runner_context=ctx, agent_config=config)

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == 1
        assert "not yet implemented" in result.error.lower()


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
