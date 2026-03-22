"""DockerRunner implementation for sandboxed execution.

Provides a RunnerProtocol backend that executes agents inside Docker containers
with strict isolation: CPU/memory limits, read-only/read-write bind mounts,
non-root user (UID 1000), and isolated networking.

The ``execute()`` method starts the provisioned container, mounts a JSON
config file with agent runtime configuration and resolved inputs, then runs the
runtime shim (``agentry.runners.shim``) inside the container. The shim
launches the configured agent runtime (e.g. ClaudeCodeAgent, which invokes
``claude -p``). Timeout enforcement kills the container (SIGKILL) if execution
exceeds ``resources.timeout`` seconds. The ``ANTHROPIC_API_KEY`` environment
variable is forwarded from the host into the container.

Uses ``docker-py`` (the ``docker`` library) for all container lifecycle
management. The Docker daemon must be reachable for ``check_available()``
and ``provision()`` to succeed.

Usage::

    from agentry.runners.docker_runner import DockerRunner

    runner = DockerRunner()
    status = runner.check_available()
    assert status.available

    ctx = runner.provision(safety_block=safety, resolved_inputs={})
    result = runner.execute(runner_context=ctx, agent_config=config)
    runner.teardown(ctx)
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import uuid
from pathlib import Path
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

        # Forward ANTHROPIC_API_KEY from the host environment into the container
        # so the agent runtime (claude CLI) can authenticate.
        container_env: list[str] = []
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key:
            container_env.append(f"ANTHROPIC_API_KEY={api_key}")

        try:
            container = self._client.containers.create(
                image=image,
                name=container_name,
                command=[
                    "python", "-m", "agentry.runners.shim",
                    "/config/agent_config.json",
                    "/output/result.json",
                ],
                user=self._CONTAINER_USER,
                volumes=binds,
                cpu_period=cpu_period,
                cpu_quota=cpu_quota,
                mem_limit=mem_limit,
                network_disabled=False,
                detach=True,
                environment=container_env,
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
    # RunnerProtocol: execute
    # ------------------------------------------------------------------

    def execute(
        self,
        runner_context: RunnerContext,
        agent_config: AgentConfig,
    ) -> ExecutionResult:
        """Execute the agent inside the provisioned container.

        Starts the container, mounts a JSON config file with agent runtime
        configuration, tool bindings, and resolved inputs, then runs the
        runtime shim (``agentry.runners.shim``) inside the container.

        The shim reads the config, launches the configured agent runtime
        (e.g. ClaudeCodeAgent which invokes ``claude -p``), and writes the
        :class:`~agentry.agents.models.AgentResult` to
        ``/output/result.json``. After the container exits (or is killed on
        timeout), this method reads the result file and returns an
        :class:`ExecutionResult`.

        Args:
            runner_context: The provisioned environment context returned by
                :meth:`provision`.
            agent_config: Bundled agent execution parameters (system prompt,
                inputs, tool names, LLM config, retry config, timeout).

        Returns:
            An :class:`ExecutionResult` wrapping the execution output.

        Raises:
            RuntimeError: If the container cannot be started or the result
                cannot be read.
        """
        container_id = runner_context.container_id
        if not container_id:
            return ExecutionResult(
                exit_code=1,
                error="No container ID in runner context; cannot execute.",
            )

        short_id = container_id[:12]

        # Build the agent config JSON that the shim will read.
        config_payload = self._build_config_payload(agent_config)

        # Write the config to a temp file on the host.
        config_dir = tempfile.mkdtemp(prefix="agentry-config-")
        config_path = os.path.join(config_dir, "agent_config.json")
        with open(config_path, "w") as fh:
            json.dump(config_payload, fh, indent=2, default=str)

        logger.info(
            "Wrote agent config to %s for container id=%s",
            config_path,
            short_id,
        )

        try:
            container = self._client.containers.get(container_id)
        except Exception as exc:
            return ExecutionResult(
                exit_code=1,
                error=f"Cannot find container id={short_id}: {exc}",
            )

        # Copy the config file into the container at /config/.
        self._copy_to_container(container, config_path, "/config/agent_config.json")

        # Start the container with the shim command.
        shim_command = [
            "python", "-m", "agentry.runners.shim",
            "/config/agent_config.json",
            "/output/result.json",
        ]

        logger.info(
            "Starting container id=%s with command: %s",
            short_id,
            shim_command,
        )

        try:
            container.start()
        except Exception as exc:
            return ExecutionResult(
                exit_code=1,
                error=f"Failed to start container id={short_id}: {exc}",
            )

        # Wait for completion with timeout enforcement.
        timeout = agent_config.timeout
        timed_out = False
        start_time = time.monotonic()

        try:
            wait_result = container.wait(timeout=timeout)
            exit_code = wait_result.get("StatusCode", -1)
        except Exception as exc:
            elapsed = time.monotonic() - start_time
            exc_str = str(exc).lower()

            # Detect timeout -- docker-py raises on timeout or the container
            # may still be running.
            if timeout is not None and (
                elapsed >= timeout
                or "timed out" in exc_str
                or "read timeout" in exc_str
                or "timeout" in exc_str
            ):
                timed_out = True
                logger.warning(
                    "Container id=%s exceeded timeout of %.1fs; killing.",
                    short_id,
                    timeout,
                )
                self._kill_container(container, short_id)
                exit_code = 137  # SIGKILL
            else:
                return ExecutionResult(
                    exit_code=1,
                    error=f"Error waiting for container id={short_id}: {exc}",
                )

        # Collect stdout/stderr from the container.
        stdout = self._collect_logs(container, "stdout")
        stderr = self._collect_logs(container, "stderr")

        # Read the result file from the output directory.
        result_data = self._read_result_file()

        # Parse agent-level result fields from the shim's output.
        agent_fields = self._parse_agent_result(result_data)

        # Build the execution result.
        error_msg = ""
        if timed_out:
            error_msg = (
                f"Execution timed out after {timeout}s; "
                f"container id={short_id} was killed."
            )
        elif exit_code != 0:
            # Prefer the agent's error message from result.json if available.
            error_msg = agent_fields.get("error") or result_data.get(
                "error",
                f"Container id={short_id} exited with code {exit_code}.",
            )

        logger.info(
            "Container id=%s finished: exit_code=%d timed_out=%s",
            short_id,
            exit_code,
            timed_out,
        )

        return ExecutionResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            runner_metadata={
                "runner_type": "docker",
                "container_id": container_id,
                "timed_out": timed_out,
                "result_data": result_data,
            },
            timed_out=timed_out,
            error=error_msg,
            output=agent_fields.get("output"),
            token_usage=agent_fields.get("token_usage", {}),
            tool_invocations=agent_fields.get("tool_invocations", []),
        )

    # ------------------------------------------------------------------
    # Private helpers for execute
    # ------------------------------------------------------------------

    @staticmethod
    def _build_config_payload(agent_config: AgentConfig) -> dict[str, Any]:
        """Serialize an AgentConfig into a JSON-compatible dictionary.

        The resulting payload is written to a file mounted inside the container
        at ``/config/agent_config.json`` and read by the runtime shim.

        Args:
            agent_config: The agent configuration to serialize.

        Returns:
            A dictionary suitable for ``json.dump()``.
        """
        payload: dict[str, Any] = {
            "system_prompt": agent_config.system_prompt,
            "resolved_inputs": agent_config.resolved_inputs,
            "tool_names": agent_config.tool_names,
            "agent_name": agent_config.agent_name,
            "agent_config": agent_config.agent_config,
        }
        if agent_config.timeout is not None:
            payload["timeout"] = agent_config.timeout
        return payload

    @staticmethod
    def _parse_agent_result(result_data: dict[str, Any]) -> dict[str, Any]:
        """Extract agent result fields from the shim's result JSON.

        Reads the structured AgentResult written by the shim and maps its
        fields into the ``runner_metadata`` dict returned by :meth:`execute`.

        Args:
            result_data: Parsed result dictionary from ``result.json``.

        Returns:
            A dictionary with agent-level result fields (output, token_usage,
            timed_out, raw_output, error).
        """
        return {
            "output": result_data.get("output"),
            "raw_output": result_data.get("raw_output", ""),
            "token_usage": result_data.get("token_usage", {}),
            "tool_invocations": result_data.get("tool_invocations", []),
            "timed_out": result_data.get("timed_out", False),
            "error": result_data.get("error", ""),
        }

    @staticmethod
    def _copy_to_container(
        container: Any,
        host_path: str,
        container_path: str,
    ) -> None:
        """Copy a file from the host into the container using ``put_archive``.

        Args:
            container: Docker container object.
            host_path: Absolute path on the host.
            container_path: Absolute path inside the container.
        """
        import io
        import tarfile

        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            tar.add(host_path, arcname=os.path.basename(container_path))
        tar_stream.seek(0)

        container_dir = str(Path(container_path).parent)
        container.put_archive(container_dir, tar_stream)

    def _kill_container(self, container: Any, short_id: str) -> None:
        """Send SIGKILL to a running container.

        Args:
            container: Docker container object.
            short_id: Short container ID for logging.
        """
        try:
            container.kill(signal="SIGKILL")
            logger.info("Killed container id=%s", short_id)
        except Exception as kill_exc:
            logger.warning(
                "Failed to kill container id=%s: %s",
                short_id,
                kill_exc,
            )

    @staticmethod
    def _collect_logs(container: Any, stream: str) -> str:
        """Collect logs from a container.

        Args:
            container: Docker container object.
            stream: Either ``"stdout"`` or ``"stderr"``.

        Returns:
            Decoded log output, or empty string on failure.
        """
        try:
            raw = container.logs(
                stdout=(stream == "stdout"),
                stderr=(stream == "stderr"),
            )
            if isinstance(raw, bytes):
                return raw.decode("utf-8", errors="replace")
            return str(raw)
        except Exception:
            return ""

    def _read_result_file(self) -> dict[str, Any]:
        """Read the result JSON from the output directory.

        Returns:
            Parsed result dictionary, or a dict with an error key on failure.
        """
        result_path = os.path.join(self._output_path, "result.json")
        try:
            with open(result_path) as fh:
                return json.load(fh)
        except FileNotFoundError:
            return {"error": f"Result file not found: {result_path}"}
        except json.JSONDecodeError as exc:
            return {"error": f"Invalid JSON in result file: {exc}"}

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
