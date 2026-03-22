"""DNS filtering proxy for sandboxed execution.

Implements a lightweight DNS proxy that resolves queries only for domains
explicitly listed in the workflow's ``network.allow`` list. All other queries
receive an NXDOMAIN response. The LLM API domain (e.g. ``api.anthropic.com``)
is always included in the allow list, derived from the model configuration
provider.

The proxy is designed to run as a sidecar container on the isolated Docker
network created by :class:`~agentry.runners.network.NetworkManager`. It must
start before the agent container so that DNS is available when the agent boots.

The sandbox container is configured to use the proxy's IP as its sole DNS
resolver (``--dns`` flag in Docker).

Usage::

    from agentry.runners.dns_proxy import DNSFilteringProxy

    proxy = DNSFilteringProxy(
        allowed_domains=["api.anthropic.com", "pypi.org"],
    )
    # In production this runs inside a sidecar container; for unit testing
    # the filtering logic is exercised directly via resolve_query().
"""

from __future__ import annotations

import logging
import socket
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# dnslib is an optional dependency; import lazily so the module can be loaded
# even when dnslib is not installed (e.g. during unit tests that mock the
# DNS layer).
try:
    from dnslib import QTYPE, RR, A, DNSHeader, DNSRecord

    _DNSLIB_AVAILABLE = True
except ImportError:
    _DNSLIB_AVAILABLE = False


# ---------------------------------------------------------------------------
# Provider-to-domain mapping
# ---------------------------------------------------------------------------

# Maps LLM provider identifiers to the DNS domains they require for API calls.
# Used to auto-include the appropriate API domain regardless of what the user
# specifies in the workflow's network allow list.
PROVIDER_DOMAINS: dict[str, list[str]] = {
    "anthropic": ["api.anthropic.com"],
    "openai": ["api.openai.com"],
    "google": ["generativelanguage.googleapis.com"],
    "azure": ["openai.azure.com"],
}

# Default domains to include when provider cannot be determined.
DEFAULT_LLM_DOMAINS: list[str] = ["api.anthropic.com"]


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class DNSQueryLog:
    """Record of a single DNS query processed by the filtering proxy.

    Attributes:
        domain: The queried domain name (without trailing dot).
        query_type: DNS query type string (e.g. ``"A"``, ``"AAAA"``).
        allowed: Whether the domain was in the allow list.
        timestamp: ISO 8601 timestamp of the query.
    """

    domain: str
    query_type: str
    allowed: bool
    timestamp: str


@dataclass
class DNSProxyConfig:
    """Configuration for the DNS filtering proxy.

    Attributes:
        allowed_domains: Domains that are permitted to resolve. Subdomains
            are matched if the parent domain is in the list (e.g. allowing
            ``example.com`` also allows ``sub.example.com``).
        upstream_dns: Upstream DNS server for resolving allowed domains.
            Defaults to Google's public DNS.
        listen_address: Address the proxy listens on. Defaults to all
            interfaces (``"0.0.0.0"``).
        listen_port: UDP port the proxy listens on. Defaults to ``53``.
        provider: LLM provider identifier used to auto-include API domains.
            When ``None``, the default LLM domains are included.
    """

    allowed_domains: list[str] = field(default_factory=list)
    upstream_dns: str = "8.8.8.8"
    listen_address: str = "0.0.0.0"
    listen_port: int = 53
    provider: str | None = None


# ---------------------------------------------------------------------------
# Core filtering logic
# ---------------------------------------------------------------------------


def build_allow_set(
    allowed_domains: list[str],
    provider: str | None = None,
) -> set[str]:
    """Build the normalised set of allowed domains.

    Merges the explicit allow list with the LLM provider's required domains.
    All domain names are lowercased and stripped of trailing dots.

    Args:
        allowed_domains: Explicit domain allow list from the workflow.
        provider: LLM provider identifier (e.g. ``"anthropic"``). When
            *None*, :data:`DEFAULT_LLM_DOMAINS` are included.

    Returns:
        A set of normalised domain name strings.
    """
    result: set[str] = set()

    # Add explicit domains.
    for domain in allowed_domains:
        result.add(domain.lower().rstrip("."))

    # Add LLM provider domains.
    if provider is not None:
        provider_key = provider.lower()
        for domain in PROVIDER_DOMAINS.get(provider_key, DEFAULT_LLM_DOMAINS):
            result.add(domain.lower().rstrip("."))
    else:
        for domain in DEFAULT_LLM_DOMAINS:
            result.add(domain.lower().rstrip("."))

    return result


def is_domain_allowed(domain: str, allow_set: set[str]) -> bool:
    """Check whether *domain* matches any entry in *allow_set*.

    Matching rules:
    - Exact match (case-insensitive).
    - Subdomain match: ``sub.example.com`` matches if ``example.com`` is in
      the allow set.

    Args:
        domain: The domain name to check (may include trailing dot).
        allow_set: Set of normalised allowed domain names.

    Returns:
        ``True`` if the domain is allowed, ``False`` otherwise.
    """
    normalised = domain.lower().rstrip(".")
    if not normalised:
        return False

    # Exact match.
    if normalised in allow_set:
        return True

    # Subdomain match: walk up the domain hierarchy.
    parts = normalised.split(".")
    for i in range(1, len(parts)):
        parent = ".".join(parts[i:])
        if parent in allow_set:
            return True

    return False


# ---------------------------------------------------------------------------
# DNS Filtering Proxy
# ---------------------------------------------------------------------------


class DNSFilteringProxy:
    """Lightweight DNS filtering proxy for sandboxed agent execution.

    Resolves DNS queries only for domains in the allow list. Blocked domains
    receive NXDOMAIN responses. Maintains a log of all queries for inclusion
    in the execution record.

    The proxy does **not** cache responses -- this simplifies the
    implementation and avoids stale-cache issues during short-lived sandbox
    executions.

    Args:
        config: Proxy configuration. When *None*, a default configuration
            with only the default LLM domains is used.
        upstream_resolver: A callable that performs the actual upstream DNS
            resolution for allowed domains. When *None*, the built-in UDP
            resolver is used. Providing a mock here enables unit testing
            without network access.

    Raises:
        RuntimeError: If ``dnslib`` is not installed and no
            *upstream_resolver* is provided.
    """

    def __init__(
        self,
        config: DNSProxyConfig | None = None,
        *,
        upstream_resolver: Any = None,
        allowed_domains: list[str] | None = None,
        provider: str | None = None,
    ) -> None:
        if config is not None:
            self._config = config
        else:
            self._config = DNSProxyConfig(
                allowed_domains=allowed_domains or [],
                provider=provider,
            )

        self._allow_set = build_allow_set(
            self._config.allowed_domains,
            self._config.provider,
        )
        self._upstream_resolver = upstream_resolver
        self._query_log: list[DNSQueryLog] = []
        self._running = False

        logger.debug(
            "DNSFilteringProxy initialised with allow set: %s",
            sorted(self._allow_set),
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def allow_set(self) -> set[str]:
        """The normalised set of allowed domain names."""
        return set(self._allow_set)

    @property
    def query_log(self) -> list[DNSQueryLog]:
        """List of DNS query log entries (read-only copy)."""
        return list(self._query_log)

    # ------------------------------------------------------------------
    # Core resolution
    # ------------------------------------------------------------------

    def resolve_query(self, domain: str, query_type: str = "A") -> tuple[bool, str | None]:
        """Resolve a DNS query, enforcing the domain allow list.

        If the domain is allowed, the query is forwarded to the upstream
        resolver and the result is returned. If the domain is blocked,
        ``(False, None)`` is returned and the caller should respond with
        NXDOMAIN.

        Every call is recorded in :attr:`query_log`.

        Args:
            domain: The domain name being queried.
            query_type: DNS query type (e.g. ``"A"``, ``"AAAA"``).

        Returns:
            A tuple ``(allowed, ip_address)``. When *allowed* is ``False``,
            *ip_address* is ``None``. When *allowed* is ``True``,
            *ip_address* is the resolved address string (or ``None`` if
            upstream resolution failed).
        """
        allowed = is_domain_allowed(domain, self._allow_set)
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        ip_address: str | None = None
        if allowed:
            ip_address = self._resolve_upstream(domain, query_type)
            logger.debug("ALLOW %s (%s) -> %s", domain, query_type, ip_address)
        else:
            logger.debug("BLOCK %s (%s) -> NXDOMAIN", domain, query_type)

        self._query_log.append(
            DNSQueryLog(
                domain=domain.lower().rstrip("."),
                query_type=query_type,
                allowed=allowed,
                timestamp=timestamp,
            )
        )

        return allowed, ip_address

    def _resolve_upstream(self, domain: str, query_type: str) -> str | None:
        """Forward a DNS query to the upstream resolver.

        Args:
            domain: The domain name to resolve.
            query_type: DNS query type string.

        Returns:
            The resolved IP address string, or ``None`` on failure.
        """
        if self._upstream_resolver is not None:
            result: str | None = self._upstream_resolver(domain, query_type)
            return result

        # Fall back to socket-based resolution for A records.
        if query_type.upper() == "A":
            try:
                result = socket.gethostbyname(domain)
                return result
            except socket.gaierror:
                logger.warning("Upstream resolution failed for %s", domain)
                return None

        # For non-A records without a custom resolver, return None.
        logger.debug("No upstream resolver for query type %s", query_type)
        return None

    # ------------------------------------------------------------------
    # DNS packet handling (requires dnslib)
    # ------------------------------------------------------------------

    def handle_dns_packet(self, data: bytes) -> bytes:
        """Process a raw DNS query packet and return a response packet.

        Parses the incoming DNS query using ``dnslib``, checks the domain
        against the allow list, and returns either a resolved response or
        an NXDOMAIN response.

        Args:
            data: Raw DNS query packet bytes.

        Returns:
            Raw DNS response packet bytes.

        Raises:
            RuntimeError: If ``dnslib`` is not installed.
        """
        if not _DNSLIB_AVAILABLE:
            raise RuntimeError(
                "dnslib is not installed. Install it with: pip install dnslib"
            )

        request = DNSRecord.parse(data)
        qname = str(request.q.qname)
        qtype = QTYPE[request.q.qtype]

        allowed, ip_address = self.resolve_query(qname, qtype)

        if allowed and ip_address is not None:
            # Build a successful response.
            response = DNSRecord(
                DNSHeader(id=request.header.id, qr=1, aa=1, ra=1),
                q=request.q,
            )
            response.add_answer(
                RR(
                    rname=request.q.qname,
                    rtype=request.q.qtype,
                    rdata=A(ip_address),
                    ttl=60,
                )
            )
        else:
            # Build an NXDOMAIN response.
            response = DNSRecord(
                DNSHeader(
                    id=request.header.id,
                    qr=1,
                    aa=1,
                    ra=1,
                    rcode=3,  # NXDOMAIN
                ),
                q=request.q,
            )

        packed: bytes = response.pack()
        return packed

    # ------------------------------------------------------------------
    # Server lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the DNS proxy server (blocking).

        Binds a UDP socket to the configured address and port and enters a
        receive loop. The loop runs until :meth:`stop` sets the running flag
        to ``False``.

        In production the proxy runs inside a sidecar Docker container, so
        this blocking call is the container's main process.

        Raises:
            RuntimeError: If ``dnslib`` is not installed.
        """
        if not _DNSLIB_AVAILABLE:
            raise RuntimeError(
                "dnslib is not installed. Install it with: pip install dnslib"
            )

        self._running = True
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(1.0)  # Allow periodic check of self._running.
        sock.bind((self._config.listen_address, self._config.listen_port))
        logger.info(
            "DNS filtering proxy listening on %s:%d",
            self._config.listen_address,
            self._config.listen_port,
        )

        try:
            while self._running:
                try:
                    data, addr = sock.recvfrom(4096)
                    response = self.handle_dns_packet(data)
                    sock.sendto(response, addr)
                except TimeoutError:
                    continue
                except Exception:
                    logger.exception("Error processing DNS query")
        finally:
            sock.close()
            logger.info("DNS filtering proxy stopped.")

    def stop(self) -> None:
        """Signal the proxy server to stop.

        Sets the running flag to ``False``. The server loop will exit after
        the current socket timeout (at most 1 second).
        """
        self._running = False

    # ------------------------------------------------------------------
    # Execution record helpers
    # ------------------------------------------------------------------

    def get_execution_record_entries(self) -> list[dict[str, Any]]:
        """Return DNS query log entries formatted for the execution record.

        Each entry maps directly to an element of the ``dns_queries`` array
        in the execution record JSON. The ``allowed`` boolean is translated to
        an ``action`` string: ``"resolved"`` when the domain was allowed,
        ``"blocked"`` when it was rejected with NXDOMAIN.

        Returns:
            A list of dicts, each containing ``domain``, ``action``,
            ``query_type``, and ``timestamp`` keys.
        """
        return [
            {
                "domain": log.domain,
                "action": "resolved" if log.allowed else "blocked",
                "query_type": log.query_type,
                "timestamp": log.timestamp,
            }
            for log in self._query_log
        ]

    # ------------------------------------------------------------------
    # Docker sidecar helpers
    # ------------------------------------------------------------------

    @staticmethod
    def get_container_dns_config(proxy_ip: str) -> dict[str, Any]:
        """Return Docker container creation kwargs for DNS configuration.

        The returned dictionary should be merged into the ``containers.create``
        call so that the sandbox container uses the DNS proxy as its sole
        resolver.

        Args:
            proxy_ip: The IP address of the DNS proxy sidecar on the
                isolated network.

        Returns:
            A dictionary with ``dns`` and ``dns_search`` keys suitable for
            passing to ``docker.containers.create()``.
        """
        return {
            "dns": [proxy_ip],
            "dns_search": [],
        }

    @staticmethod
    def get_sidecar_container_config(
        image: str = "python:3.12-slim",
        network_id: str = "",
        execution_id: str = "",
    ) -> dict[str, Any]:
        """Return Docker container creation kwargs for the DNS proxy sidecar.

        The sidecar runs the DNS filtering proxy as the container's main
        process. It must be started *before* the agent container so that
        DNS is available when the agent boots.

        Args:
            image: Docker image for the sidecar. Should have Python and
                ``dnslib`` installed.
            network_id: Docker network ID to attach the sidecar to.
            execution_id: Execution identifier for labelling.

        Returns:
            A dictionary of container creation kwargs.
        """
        return {
            "image": image,
            "name": f"agentry-dns-{execution_id}" if execution_id else "agentry-dns",
            "network": network_id,
            "detach": True,
            "labels": {
                "agentry.execution_id": execution_id,
                "agentry.managed": "true",
                "agentry.role": "dns-proxy",
            },
        }
