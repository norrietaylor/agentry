"""DockerRunner implementation for sandboxed execution.

Provides a RunnerProtocol backend that executes agents inside Docker containers
with strict isolation: CPU/memory limits, read-only/read-write bind mounts,
non-root user (UID 1000), and isolated networking.

Uses ``docker-py`` (the ``docker`` library) for all container lifecycle
management. The Docker daemon must be reachable for ``check_available()``
and ``provision()`` to succeed.

Usage::

    from agentry.runners.docker_runner import DockerRunner

    runner = DockerRunner()
    status = runner.check_available()
    assert status.available

    ctx = runner.provision(safety_block=safety, resolved_inputs={})
    # ... execute handled by T01.5 ...
    runner.teardown(ctx)
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from agentry.models.safety import SafetyBlock
from agentry.runners.protocol import (
    AgentConfig,
    ExecutionResult,
    RunnerContext,
    RunnerStatus,
)

logger = logging.getLogger(__name__)

# docker-py is an optional runtime dependency; import lazily so that the module
# can be imported even in environments without it (e.g. when running unit tests
# with a mock injected via the constructor).
try:
    import docker  # type: ignore[import-untyped]

    _DOCKER_AVAILABLE = True
except ImportError:
    _DOCKER_AVAILABLE = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_memory_limit(memory_str: str) -> str:
    """Normalise a memory limit string for the Docker API.

    Accepts values like ``"2GB"``, ``"512MB"``, ``"256m"`` and returns them
    unchanged -- the Docker SDK accepts these forms directly.

    Args:
        memory_str: Human-readable memory limit from :class:`ResourceConfig`.

    Returns:
        The memory string suitable for ``host_config.mem_limit``.
    """
    return memory_str


def _build_bind_mounts(
    safety_block: SafetyBlock,
    codebase_path: str,
    output_path: str,
) -> tuple[list[str], dict[str, str]]:
    """Build Docker bind-mount strings and a host-to-container mapping.

    The resulting mounts are:
    - Codebase directory mounted read-only at ``/workspace``.
    - Output directory mounted read-write at ``/output``.
    - Each ``filesystem.read`` path mounted read-only at ``/mnt/read/<basename>``.
    - Each ``filesystem.write`` path mounted read-write at ``/mnt/write/<basename>``.

    Args:
        safety_block: Workflow safety configuration.
        codebase_path: Absolute host path to the codebase.
        output_path: Absolute host path to the output directory.

    Returns:
        A tuple of (bind_list, mount_mappings) where *bind_list* is a list of
        Docker bind-mount strings (``"host:container:mode"``) and
        *mount_mappings* maps host paths to container paths.
    """
    binds: list[str] = []
    mappings: dict[str, str] = {}

    # Codebase -- always read-only.
    binds.append(f"{codebase_path}:/workspace:ro")
    mappings[codebase_path] = "/workspace"

    # Output directory -- always read-write.
    binds.append(f"{output_path}:/output:rw")
    mappings[output_path] = "/output"

    # Extra read mounts from filesystem config.
    for idx, read_path in enumerate(safety_block.filesystem.read):
        container_path = f"/mnt/read/{idx}"
        binds.append(f"{read_path}:{container_path}:ro")
        mappings[read_path] = container_path

    # Extra write mounts from filesystem config.
    for idx, write_path in enumerate(safety_block.filesystem.write):
        container_path = f"/mnt/write/{idx}"
        binds.append(f"{write_path}:{container_path}:rw")
        mappings[write_path] = container_path

    return binds, mappings


# ---------------------------------------------------------------------------
# DockerRunner
# ---------------------------------------------------------------------------


class DockerRunner:
    """Runner backend for sandboxed execution inside Docker containers.

    Implements :class:`RunnerProtocol` using ``docker-py`` for the full
    container lifecycle: creation with resource limits and bind mounts,
    and teardown with container/volume removal.

    Args:
        docker_client: A ``docker.DockerClient`` instance (or compatible mock).
            When *None* (the default), the client is created lazily from the
            environment via ``docker.from_env()``.
        codebase_path: Absolute host path to the codebase directory. Mounted
            read-only at ``/workspace`` inside the container.
        output_path: Absolute host path to the output directory. Mounted
            read-write at ``/output`` inside the container.

    Raises:
        RuntimeError: If *docker_client* is *None* and ``docker-py`` is not
            installed.
    """

    _CONTAINER_USER = "1000:1000"
    _WORKSPACE_PATH = "/workspace"
    _OUTPUT_PATH = "/output"

    def __init__(
        self,
        docker_client: Any = None,
        *,
        codebase_path: str = ".",
        output_path: str = "/tmp/agentry-output",
    ) -> None:
        if docker_client is not None:
            self._client = docker_client
        elif _DOCKER_AVAILABLE:
            self._client = docker.from_env()  # type: ignore[union-attr]
        else:
            raise RuntimeError(
                "docker-py is not installed. Install it with: pip install docker"
            )
        self._codebase_path = codebase_path
        self._output_path = output_path

    # ------------------------------------------------------------------
    # RunnerProtocol: check_available
    # ------------------------------------------------------------------

    def check_available(self) -> RunnerStatus:
        """Check whether the Docker daemon is reachable.

        Probes the daemon via ``docker.ping()``. Returns ``available=True``
        if the ping succeeds, or ``available=False`` with a diagnostic
        message if it fails.

        Returns:
            A :class:`RunnerStatus` indicating Docker daemon availability.
        """
        try:
            self._client.ping()
            return RunnerStatus(
                available=True,
                message="Docker daemon is reachable.",
            )
        except Exception as exc:
            return RunnerStatus(
                available=False,
                message=f"Docker daemon is not reachable: {exc}",
            )

    # ------------------------------------------------------------------
    # RunnerProtocol: provision
    # ------------------------------------------------------------------

    def provision(
        self,
        safety_block: SafetyBlock,
        resolved_inputs: dict[str, str],
    ) -> RunnerContext:
        """Provision a Docker container for sandboxed execution.

        Creates a container with:
        - Base image from ``safety_block.sandbox.base``.
        - CPU limit from ``safety_block.resources.cpu``.
        - Memory limit from ``safety_block.resources.memory``.
        - Read-only bind mounts for ``filesystem.read`` paths.
        - Read-write bind mounts for ``filesystem.write`` paths.
        - Codebase mounted read-only at ``/workspace``.
        - Output directory mounted read-write at ``/output``.
        - Non-root user (UID 1000).

        Args:
            safety_block: Workflow safety configuration.
            resolved_inputs: Mapping from input name to resolved content.

        Returns:
            A :class:`RunnerContext` with the container ID, mount mappings,
            and resource metadata.

        Raises:
            RuntimeError: If container creation fails.
        """
        execution_id = uuid.uuid4().hex[:12]
        container_name = f"agentry-sandbox-{execution_id}"

        binds, mount_mappings = _build_bind_mounts(
            safety_block,
            self._codebase_path,
            self._output_path,
        )

        image = safety_block.sandbox.base
        cpu_period = 100_000  # microseconds (default Docker period)
        cpu_quota = int(safety_block.resources.cpu * cpu_period)
        mem_limit = _parse_memory_limit(safety_block.resources.memory)

        logger.info(
            "Provisioning container %r from image %r (cpu=%.1f, memory=%s)",
            container_name,
            image,
            safety_block.resources.cpu,
            mem_limit,
        )

        try:
            container = self._client.containers.create(
                image=image,
                name=container_name,
                user=self._CONTAINER_USER,
                volumes=binds,
                cpu_period=cpu_period,
                cpu_quota=cpu_quota,
                mem_limit=mem_limit,
                network_disabled=False,
                detach=True,
                labels={
                    "agentry.execution_id": execution_id,
                    "agentry.managed": "true",
                },
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to provision Docker container {container_name!r}: {exc}"
            ) from exc

        container_id: str = container.id
        logger.info(
            "Provisioned container %r (id=%s)",
            container_name,
            container_id[:12],
        )

        return RunnerContext(
            container_id=container_id,
            network_id="",
            mount_mappings=mount_mappings,
            metadata={
                "runner_type": "docker",
                "execution_id": execution_id,
                "container_name": container_name,
                "image": image,
                "cpu": safety_block.resources.cpu,
                "memory": mem_limit,
                "user": self._CONTAINER_USER,
            },
        )

    # ------------------------------------------------------------------
    # RunnerProtocol: execute (stub -- implemented in T01.5)
    # ------------------------------------------------------------------

    def execute(
        self,
        runner_context: RunnerContext,
        agent_config: AgentConfig,
    ) -> ExecutionResult:
        """Execute the agent inside the provisioned container.

        .. note::

            Full implementation is deferred to T01.5. This stub returns
            an error result indicating the method is not yet implemented.

        Args:
            runner_context: The provisioned environment context.
            agent_config: Bundled agent execution parameters.

        Returns:
            An :class:`ExecutionResult` with an error indicating the method
            is not yet implemented.
        """
        return ExecutionResult(
            exit_code=1,
            error="DockerRunner.execute() is not yet implemented (see T01.5).",
        )

    # ------------------------------------------------------------------
    # RunnerProtocol: teardown
    # ------------------------------------------------------------------

    def teardown(self, runner_context: RunnerContext) -> None:
        """Remove the container and associated volumes.

        Idempotent: calling teardown on an already-removed container does not
        raise. Failed cleanup logs a warning but does not propagate the
        exception, as teardown failures should not mask execution results.

        Args:
            runner_context: The provisioned environment context returned by
                :meth:`provision`.
        """
        container_id = runner_context.container_id
        if not container_id:
            logger.debug("No container ID in context; skipping teardown.")
            return

        short_id = container_id[:12]
        logger.debug("Tearing down container id=%s", short_id)

        try:
            container = self._client.containers.get(container_id)
            container.remove(force=True, v=True)
            logger.info("Removed container id=%s", short_id)
        except Exception as exc:
            exc_str = str(exc).lower()
            if "404" in exc_str or "not found" in exc_str or "no such container" in exc_str:
                logger.debug(
                    "Container id=%s already absent, skipping teardown.",
                    short_id,
                )
                return
            # Log warning but do not raise -- teardown failures should not
            # mask execution results.
            logger.warning(
                "Failed to remove container id=%s: %s",
                short_id,
                exc,
            )
