"""Isolated Docker network management for sandboxed execution.

Creates a dedicated Docker bridge network per sandbox execution with no
default internet connectivity (``internal=True``). The network is torn down
after execution completes, regardless of whether execution succeeded or failed.

Usage::

    from agentry.runners.network import NetworkManager

    manager = NetworkManager()
    network_id = manager.create_network("exec-abc123")
    try:
        # ... provision container attached to network_id ...
        pass
    finally:
        manager.teardown_network(network_id)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# docker-py is an optional runtime dependency; import lazily so that the module
# can be imported even in environments without it (e.g. when running unit tests
# with a mock injected via the constructor).
try:
    import docker

    _DOCKER_AVAILABLE = True
except ImportError:
    _DOCKER_AVAILABLE = False


class NetworkCreationError(Exception):
    """Raised when Docker network creation fails."""


class NetworkTeardownError(Exception):
    """Raised when Docker network teardown fails.

    Note: In most usage the caller should log this and continue rather than
    re-raise — a teardown failure should not mask the underlying execution
    result.
    """


class NetworkManager:
    """Manages isolated Docker networks for sandboxed agent execution.

    Each ``create_network`` call produces an ``internal=True`` bridge network
    that has no default internet connectivity. The sandbox container is
    attached to this network so that egress is limited to the DNS filtering
    proxy and any explicitly permitted endpoints.

    Args:
        docker_client: A ``docker.DockerClient`` instance (or compatible mock).
            When *None* (the default), the client is created lazily from the
            environment via ``docker.from_env()``. Providing a client
            explicitly is useful in tests.
        network_name_prefix: Prefix for generated network names. The full name
            is ``{prefix}-{execution_id}``.

    Raises:
        RuntimeError: If *docker_client* is *None* and ``docker-py`` is not
            installed.
    """

    _DRIVER = "bridge"
    _DEFAULT_PREFIX = "agentry-net"

    def __init__(
        self,
        docker_client: Any = None,
        *,
        network_name_prefix: str = _DEFAULT_PREFIX,
    ) -> None:
        if docker_client is not None:
            self._client = docker_client
        elif _DOCKER_AVAILABLE:
            self._client = docker.from_env()
        else:
            raise RuntimeError(
                "docker-py is not installed. Install it with: pip install docker"
            )
        self._prefix = network_name_prefix

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_network(self, execution_id: str) -> str:
        """Create an isolated Docker bridge network for *execution_id*.

        The network is configured with ``internal=True`` which disables the
        default route, preventing containers on this network from initiating
        outbound internet connections directly.

        Args:
            execution_id: A unique identifier for the current execution (e.g.
                a UUID or timestamp-based string). Used to construct the
                network name and as a label value for later discovery.

        Returns:
            The Docker network ID (a hex string) that can be passed to
            ``teardown_network`` or used when attaching containers.

        Raises:
            NetworkCreationError: If the Docker API call fails.
        """
        network_name = f"{self._prefix}-{execution_id}"
        labels = {
            "agentry.execution_id": execution_id,
            "agentry.managed": "true",
        }
        logger.debug("Creating isolated network %r for execution %r", network_name, execution_id)
        try:
            network = self._client.networks.create(
                name=network_name,
                driver=self._DRIVER,
                internal=True,
                labels=labels,
            )
        except Exception as exc:
            raise NetworkCreationError(
                f"Failed to create Docker network {network_name!r}: {exc}"
            ) from exc

        network_id: str = network.id
        logger.info(
            "Created isolated network %r (id=%s) for execution %r",
            network_name,
            network_id[:12],
            execution_id,
        )
        return network_id

    def teardown_network(self, network_id: str) -> None:
        """Remove the Docker network identified by *network_id*.

        This method is designed to be called in a ``finally`` block so that
        the network is always removed even when execution fails. Teardown
        errors are logged as warnings rather than raised by default; callers
        that need strict error propagation should catch and inspect
        ``NetworkTeardownError``.

        Args:
            network_id: The Docker network ID returned by ``create_network``.

        Raises:
            NetworkTeardownError: If the Docker API call fails for a reason
                other than the network already being absent (idempotent
                deletion of a missing network is not an error).
        """
        logger.debug("Tearing down network id=%s", network_id[:12] if len(network_id) >= 12 else network_id)
        try:
            network = self._client.networks.get(network_id)
            network.remove()
            logger.info("Removed network id=%s", network_id[:12] if len(network_id) >= 12 else network_id)
        except Exception as exc:
            # Check if it's a "not found" scenario — that is idempotent and
            # not an error.
            exc_str = str(exc).lower()
            if "404" in exc_str or "not found" in exc_str or "no such network" in exc_str:
                logger.debug(
                    "Network id=%s already absent, skipping teardown",
                    network_id[:12] if len(network_id) >= 12 else network_id,
                )
                return
            raise NetworkTeardownError(
                f"Failed to remove Docker network id={network_id!r}: {exc}"
            ) from exc
