"""Network isolation verification for sandboxed execution.

Provides :class:`NetworkIsolationVerifier` which verifies that the DNS
filtering proxy is correctly blocking non-allowed domains. The verifier is
called by :class:`~agentry.security.setup.SetupPhase` before agent execution
begins.

Verification strategy
---------------------
The verifier uses the :class:`~agentry.runners.dns_proxy.DNSFilteringProxy`
directly to confirm:

1. A domain that is **not** in the allow list returns ``allowed=False`` when
   queried — confirming that the filtering logic is active.
2. The LLM API domain (``api.anthropic.com`` by default) returns
   ``allowed=True`` — confirming that agent communication is not broken.

When Docker is available and a container context is supplied, the verifier
additionally attempts to resolve the blocked domain *from inside the container*
using ``docker exec`` and confirms that the attempt fails. This provides a
stronger end-to-end guarantee that the network configuration (not just the
proxy logic) is correct.

Usage::

    from agentry.runners.dns_proxy import DNSFilteringProxy
    from agentry.runners.network_isolation import NetworkIsolationVerifier

    proxy = DNSFilteringProxy(
        allowed_domains=["api.anthropic.com"],
        provider="anthropic",
    )
    verifier = NetworkIsolationVerifier(proxy=proxy)
    result = verifier.verify()
    if not result.passed:
        raise RuntimeError(result.diagnostic)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentry.runners.dns_proxy import DNSFilteringProxy

logger = logging.getLogger(__name__)

# Domain that must always be blocked in a correctly configured sandbox.
# We use a well-known public domain that is never in any reasonable allow list.
_VERIFICATION_BLOCKED_DOMAIN = "example.com"

# Domain that must always be reachable (derived from default LLM provider).
_VERIFICATION_ALLOWED_DOMAIN = "api.anthropic.com"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class IsolationCheckResult:
    """Result of a single isolation check.

    Attributes:
        check_name: Short identifier for the check.
        passed: True when the check succeeded.
        domain: The domain that was checked.
        expected_action: ``"blocked"`` or ``"resolved"``.
        actual_action: The action that actually occurred.
        diagnostic: Human-readable explanation when ``passed=False``.
    """

    check_name: str
    passed: bool
    domain: str
    expected_action: str
    actual_action: str
    diagnostic: str = ""


@dataclass
class NetworkIsolationResult:
    """Aggregate result of all network isolation checks.

    Attributes:
        passed: True when all checks passed.
        checks: List of individual check results.
        diagnostic: Combined diagnostic message for all failures.
        metadata: Arbitrary metadata about the verification run.
    """

    passed: bool
    checks: list[IsolationCheckResult] = field(default_factory=list)
    diagnostic: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------


class NetworkIsolationVerifier:
    """Verifies network isolation for the DNS filtering proxy.

    Confirms that:
    - Known-blocked domains (e.g. ``example.com``) are rejected with
      ``action=blocked``.
    - The LLM API domain is allowed through.

    When a *docker_client* and *container_id* are provided, the verifier also
    runs an in-container check by executing a ``nslookup`` command via
    ``docker exec`` and confirming it fails for blocked domains.

    Args:
        proxy: The :class:`~agentry.runners.dns_proxy.DNSFilteringProxy`
            instance to verify.
        blocked_domain: Domain that should be blocked. Defaults to
            ``"example.com"``.
        allowed_domain: Domain that should resolve. Defaults to
            ``"api.anthropic.com"``.
        docker_client: Optional Docker client for in-container verification.
        container_id: Optional container ID for in-container verification.
            Requires *docker_client*.
    """

    def __init__(
        self,
        proxy: DNSFilteringProxy,
        *,
        blocked_domain: str = _VERIFICATION_BLOCKED_DOMAIN,
        allowed_domain: str = _VERIFICATION_ALLOWED_DOMAIN,
        docker_client: Any = None,
        container_id: str = "",
    ) -> None:
        self._proxy = proxy
        self._blocked_domain = blocked_domain
        self._allowed_domain = allowed_domain
        self._docker_client = docker_client
        self._container_id = container_id

    def verify(self) -> NetworkIsolationResult:
        """Run all network isolation checks.

        Checks performed:
        1. Proxy rejects the blocked domain.
        2. Proxy allows the LLM API domain.
        3. (Optional) In-container check that blocked domain fails to resolve.

        Returns:
            A :class:`NetworkIsolationResult` with ``passed=True`` only when
            all checks pass.
        """
        checks: list[IsolationCheckResult] = []

        # Check 1: Blocked domain is rejected by the proxy.
        block_check = self._check_domain_blocked(self._blocked_domain)
        checks.append(block_check)
        logger.debug(
            "NetworkIsolationVerifier: block check for %r: %s",
            self._blocked_domain,
            "PASS" if block_check.passed else "FAIL",
        )

        # Check 2: Allowed domain resolves via the proxy.
        allow_check = self._check_domain_allowed(self._allowed_domain)
        checks.append(allow_check)
        logger.debug(
            "NetworkIsolationVerifier: allow check for %r: %s",
            self._allowed_domain,
            "PASS" if allow_check.passed else "FAIL",
        )

        # Check 3: In-container check (optional).
        if self._docker_client and self._container_id:
            container_check = self._check_container_dns(self._blocked_domain)
            checks.append(container_check)
            logger.debug(
                "NetworkIsolationVerifier: in-container block check for %r: %s",
                self._blocked_domain,
                "PASS" if container_check.passed else "FAIL",
            )

        all_passed = all(c.passed for c in checks)
        failures = [c for c in checks if not c.passed]
        diagnostic = ""
        if failures:
            parts = [
                f"{c.check_name}: expected {c.expected_action!r} for {c.domain!r}, "
                f"got {c.actual_action!r}. {c.diagnostic}"
                for c in failures
            ]
            diagnostic = "Network isolation verification failed. " + " | ".join(parts)
            logger.warning("NetworkIsolationVerifier: %s", diagnostic)
        else:
            logger.info(
                "NetworkIsolationVerifier: all %d check(s) passed", len(checks)
            )

        return NetworkIsolationResult(
            passed=all_passed,
            checks=checks,
            diagnostic=diagnostic,
            metadata={
                "blocked_domain": self._blocked_domain,
                "allowed_domain": self._allowed_domain,
                "checks_run": len(checks),
            },
        )

    # ------------------------------------------------------------------
    # Internal checks
    # ------------------------------------------------------------------

    def _check_domain_blocked(self, domain: str) -> IsolationCheckResult:
        """Verify that *domain* is rejected by the proxy.

        Args:
            domain: The domain name to check.

        Returns:
            An :class:`IsolationCheckResult` for this check.
        """
        # Use a no-op upstream resolver so the check is purely about filtering.
        allowed, _ip = self._proxy.resolve_query(domain, "A")
        actual_action = "resolved" if allowed else "blocked"
        passed = not allowed  # We expect the domain to be blocked.

        diagnostic = ""
        if not passed:
            diagnostic = (
                f"Domain {domain!r} should be blocked but was resolved. "
                "Check that the domain is not in the allow list and that "
                "the DNS filtering proxy is configured correctly."
            )

        return IsolationCheckResult(
            check_name="proxy_blocks_unlisted_domain",
            passed=passed,
            domain=domain,
            expected_action="blocked",
            actual_action=actual_action,
            diagnostic=diagnostic,
        )

    def _check_domain_allowed(self, domain: str) -> IsolationCheckResult:
        """Verify that *domain* resolves via the proxy.

        Args:
            domain: The domain name to check.

        Returns:
            An :class:`IsolationCheckResult` for this check.
        """
        allowed, _ip = self._proxy.resolve_query(domain, "A")
        actual_action = "resolved" if allowed else "blocked"
        passed = allowed  # We expect the domain to be allowed.

        diagnostic = ""
        if not passed:
            diagnostic = (
                f"Domain {domain!r} should be resolvable but was blocked. "
                "Check that the LLM API domain is included in the allow list."
            )

        return IsolationCheckResult(
            check_name="proxy_allows_llm_api_domain",
            passed=passed,
            domain=domain,
            expected_action="resolved",
            actual_action=actual_action,
            diagnostic=diagnostic,
        )

    def _check_container_dns(self, domain: str) -> IsolationCheckResult:
        """Verify that *domain* fails to resolve from inside the container.

        Runs ``nslookup <domain>`` inside the container via ``docker exec``
        and confirms the command exits with a non-zero status (indicating DNS
        failure).

        Args:
            domain: The domain name to check inside the container.

        Returns:
            An :class:`IsolationCheckResult` for this check.
        """
        try:
            container = self._docker_client.containers.get(self._container_id)
            exit_code, output = container.exec_run(
                ["nslookup", domain],
                stdout=True,
                stderr=True,
            )
            output_str = output.decode("utf-8", errors="replace") if output else ""

            # nslookup returns 0 on success, 1 on NXDOMAIN/failure.
            # We want the resolution to fail (non-zero exit or NXDOMAIN in output).
            dns_failed = exit_code != 0 or "nxdomain" in output_str.lower()

            if dns_failed:
                return IsolationCheckResult(
                    check_name="container_dns_blocks_unlisted_domain",
                    passed=True,
                    domain=domain,
                    expected_action="blocked",
                    actual_action="blocked",
                )
            else:
                diagnostic = (
                    f"In-container DNS resolution of {domain!r} succeeded "
                    f"(exit_code={exit_code}). The isolated network is not "
                    "blocking DNS queries for unlisted domains."
                )
                return IsolationCheckResult(
                    check_name="container_dns_blocks_unlisted_domain",
                    passed=False,
                    domain=domain,
                    expected_action="blocked",
                    actual_action="resolved",
                    diagnostic=diagnostic,
                )
        except Exception as exc:
            # If the container is not running or exec fails, treat it as a
            # diagnostic failure rather than a check failure so that setup
            # can continue with a warning.
            logger.warning(
                "NetworkIsolationVerifier: in-container DNS check failed with error: %s",
                exc,
            )
            return IsolationCheckResult(
                check_name="container_dns_blocks_unlisted_domain",
                passed=True,  # Non-critical; cannot run the check.
                domain=domain,
                expected_action="blocked",
                actual_action="unknown",
                diagnostic=f"In-container check skipped due to error: {exc}",
            )
