"""Unit tests for T02.1: NetworkManager isolated Docker network creation and teardown.

All Docker calls are mocked — no Docker daemon is required.

Tests cover:
- create_network() calls docker networks.create() with correct parameters
  (bridge driver, internal=True, labelled with execution_id).
- create_network() returns the network ID from the Docker response.
- create_network() raises NetworkCreationError on API failure.
- teardown_network() calls network.remove() via networks.get().
- teardown_network() is idempotent: a 404 / "not found" response does not
  raise NetworkTeardownError.
- teardown_network() raises NetworkTeardownError on non-404 API failures.
- Network name is prefixed with the configurable prefix and the execution_id.
- Providing a docker_client in the constructor avoids calling docker.from_env().
- Labels include agentry.execution_id and agentry.managed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentry.runners.network import (
    NetworkCreationError,
    NetworkManager,
    NetworkTeardownError,
)

# ---------------------------------------------------------------------------
# Helpers / Factories
# ---------------------------------------------------------------------------


def _make_docker_client() -> MagicMock:
    """Return a MagicMock that mimics a docker.DockerClient."""
    client = MagicMock(name="DockerClient")
    client.networks = MagicMock(name="NetworkCollection")
    return client


def _make_network_mock(network_id: str = "abc123" * 10 + "ab") -> MagicMock:
    """Return a MagicMock that mimics a docker.models.networks.Network."""
    net = MagicMock(name="Network")
    net.id = network_id
    return net


# ---------------------------------------------------------------------------
# create_network
# ---------------------------------------------------------------------------


class TestCreateNetwork:
    def test_returns_network_id(self) -> None:
        client = _make_docker_client()
        network = _make_network_mock("deadbeef" * 8)
        client.networks.create.return_value = network

        manager = NetworkManager(docker_client=client)
        result = manager.create_network("exec-001")

        assert result == "deadbeef" * 8

    def test_calls_networks_create_with_bridge_driver(self) -> None:
        client = _make_docker_client()
        client.networks.create.return_value = _make_network_mock()

        manager = NetworkManager(docker_client=client)
        manager.create_network("exec-002")

        _, kwargs = client.networks.create.call_args
        assert kwargs["driver"] == "bridge"

    def test_calls_networks_create_with_internal_true(self) -> None:
        client = _make_docker_client()
        client.networks.create.return_value = _make_network_mock()

        manager = NetworkManager(docker_client=client)
        manager.create_network("exec-003")

        _, kwargs = client.networks.create.call_args
        assert kwargs["internal"] is True

    def test_network_name_contains_prefix_and_execution_id(self) -> None:
        client = _make_docker_client()
        client.networks.create.return_value = _make_network_mock()

        manager = NetworkManager(docker_client=client)
        manager.create_network("myexec-999")

        _, kwargs = client.networks.create.call_args
        assert kwargs["name"] == "agentry-net-myexec-999"

    def test_custom_prefix_used_in_network_name(self) -> None:
        client = _make_docker_client()
        client.networks.create.return_value = _make_network_mock()

        manager = NetworkManager(docker_client=client, network_name_prefix="custom-prefix")
        manager.create_network("exec-004")

        _, kwargs = client.networks.create.call_args
        assert kwargs["name"] == "custom-prefix-exec-004"

    def test_labels_contain_execution_id(self) -> None:
        client = _make_docker_client()
        client.networks.create.return_value = _make_network_mock()

        manager = NetworkManager(docker_client=client)
        manager.create_network("exec-005")

        _, kwargs = client.networks.create.call_args
        labels = kwargs["labels"]
        assert labels["agentry.execution_id"] == "exec-005"

    def test_labels_contain_managed_marker(self) -> None:
        client = _make_docker_client()
        client.networks.create.return_value = _make_network_mock()

        manager = NetworkManager(docker_client=client)
        manager.create_network("exec-006")

        _, kwargs = client.networks.create.call_args
        labels = kwargs["labels"]
        assert labels["agentry.managed"] == "true"

    def test_raises_network_creation_error_on_api_failure(self) -> None:
        client = _make_docker_client()
        client.networks.create.side_effect = RuntimeError("Docker API error")

        manager = NetworkManager(docker_client=client)
        with pytest.raises(NetworkCreationError, match="Docker API error"):
            manager.create_network("exec-007")

    def test_network_creation_error_message_contains_network_name(self) -> None:
        client = _make_docker_client()
        client.networks.create.side_effect = RuntimeError("boom")

        manager = NetworkManager(docker_client=client)
        with pytest.raises(NetworkCreationError, match="agentry-net-exec-fail"):
            manager.create_network("exec-fail")


# ---------------------------------------------------------------------------
# teardown_network
# ---------------------------------------------------------------------------


class TestTeardownNetwork:
    def test_calls_network_remove(self) -> None:
        client = _make_docker_client()
        network = _make_network_mock("net123" * 10 + "nn")
        client.networks.get.return_value = network

        manager = NetworkManager(docker_client=client)
        manager.teardown_network("net123" * 10 + "nn")

        network.remove.assert_called_once()

    def test_fetches_network_by_id(self) -> None:
        client = _make_docker_client()
        network = _make_network_mock()
        client.networks.get.return_value = network

        manager = NetworkManager(docker_client=client)
        manager.teardown_network("some-network-id")

        client.networks.get.assert_called_once_with("some-network-id")

    def test_idempotent_on_404_string(self) -> None:
        """teardown_network does not raise when network is already gone (404)."""
        client = _make_docker_client()
        client.networks.get.side_effect = RuntimeError("404 Not Found")

        manager = NetworkManager(docker_client=client)
        # Should not raise
        manager.teardown_network("already-gone-id")

    def test_idempotent_on_no_such_network(self) -> None:
        client = _make_docker_client()
        client.networks.get.side_effect = RuntimeError("No such network: abc")

        manager = NetworkManager(docker_client=client)
        # Should not raise
        manager.teardown_network("already-gone-id")

    def test_idempotent_on_not_found_string(self) -> None:
        client = _make_docker_client()
        client.networks.get.side_effect = RuntimeError("network not found")

        manager = NetworkManager(docker_client=client)
        # Should not raise
        manager.teardown_network("already-gone-id")

    def test_raises_teardown_error_on_unexpected_failure(self) -> None:
        client = _make_docker_client()
        client.networks.get.side_effect = RuntimeError("unexpected internal error")

        manager = NetworkManager(docker_client=client)
        with pytest.raises(NetworkTeardownError, match="unexpected internal error"):
            manager.teardown_network("problem-network-id")

    def test_raises_teardown_error_when_remove_fails(self) -> None:
        client = _make_docker_client()
        network = _make_network_mock()
        network.remove.side_effect = RuntimeError("container still attached")
        client.networks.get.return_value = network

        manager = NetworkManager(docker_client=client)
        with pytest.raises(NetworkTeardownError, match="container still attached"):
            manager.teardown_network("busy-network-id")

    def test_teardown_error_contains_network_id(self) -> None:
        client = _make_docker_client()
        client.networks.get.side_effect = RuntimeError("some failure")

        manager = NetworkManager(docker_client=client)
        with pytest.raises(NetworkTeardownError, match="problem-net-99"):
            manager.teardown_network("problem-net-99")


# ---------------------------------------------------------------------------
# Constructor behaviour
# ---------------------------------------------------------------------------


class TestConstructor:
    def test_raises_runtime_error_without_docker_installed(self) -> None:
        """When docker-py is absent and no client is provided, raise RuntimeError."""
        with patch("agentry.runners.network._DOCKER_AVAILABLE", False), pytest.raises(
            RuntimeError, match="docker-py is not installed"
        ):
            NetworkManager()

    def test_accepts_explicit_client(self) -> None:
        client = _make_docker_client()
        client.networks.create.return_value = _make_network_mock()
        # Should not call docker.from_env()
        manager = NetworkManager(docker_client=client)
        assert manager is not None

    def test_default_prefix(self) -> None:
        client = _make_docker_client()
        client.networks.create.return_value = _make_network_mock()
        manager = NetworkManager(docker_client=client)
        manager.create_network("x")
        _, kwargs = client.networks.create.call_args
        assert kwargs["name"].startswith("agentry-net-")
