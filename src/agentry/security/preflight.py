"""PreflightChecker framework and check interface.

Implements the PreflightChecker class with a pluggable check interface.
Each preflight check receives the workflow definition and environment
context, and returns a PreflightResult dataclass.

The PreflightChecker.run_all() method executes all registered checks and
collects all failures together (not one-at-a-time), enabling developers
to fix multiple issues in a single iteration.

Usage::

    from agentry.security.preflight import (
        PreflightChecker,
        PreflightResult,
    )
    from agentry.security.envelope import PreflightCheck

    checker = PreflightChecker(checks=[api_key_check, docker_check])
    results = checker.run_all()
    if checker.any_failed(results):
        checker.report_failures(results)

The PreflightChecker also supports the ``--skip-preflight`` flag via its
``skip_preflight`` constructor parameter, which bypasses all checks with a
warning log.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentry.security.envelope import PreflightCheck

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class PreflightResult:
    """Result of a single preflight check.

    This is the canonical result type returned by the PreflightChecker
    framework.  It maps closely to
    :class:`~agentry.security.envelope.PreflightCheckResult` but is defined
    here as the authoritative type for the preflight subsystem.

    Attributes:
        passed: True when the check succeeded.
        check_name: Name of the check.
        message: Description of the result (especially on failure).
        remediation: Suggested remediation when the check fails.
    """

    passed: bool
    check_name: str
    message: str = ""
    remediation: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict suitable for JSON encoding or manifests."""
        return {
            "name": self.check_name,
            "passed": self.passed,
            "message": self.message,
            "remediation": self.remediation,
        }


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class PreflightFailedError(Exception):
    """Raised by PreflightChecker.run_all() when one or more checks fail.

    Unlike the legacy :class:`~agentry.security.envelope.PreflightError` (which
    reports only the *first* failure), this exception collects *all* failures
    so that the developer can fix them in one iteration.

    Attributes:
        results: Complete list of :class:`PreflightResult` instances — both
            passed and failed.
        failures: Subset of *results* where ``passed`` is False.
    """

    def __init__(self, results: list[PreflightResult]) -> None:
        self.results = results
        self.failures = [r for r in results if not r.passed]
        lines = ["Preflight checks failed:"]
        for r in self.failures:
            line = f"  - {r.check_name}: {r.message}"
            if r.remediation:
                line += f" ({r.remediation})"
            lines.append(line)
        super().__init__("\n".join(lines))


# ---------------------------------------------------------------------------
# PreflightChecker
# ---------------------------------------------------------------------------


class PreflightChecker:
    """Pluggable framework for running preflight checks before agent execution.

    The checker holds a list of :class:`~agentry.security.envelope.PreflightCheck`
    objects and runs them all via :meth:`run_all`.  All failures are collected
    rather than stopping on the first failure, enabling the developer to see
    and fix all problems in one iteration.

    Args:
        checks: List of preflight check objects.  Each must satisfy the
            :class:`~agentry.security.envelope.PreflightCheck` protocol
            (i.e. have a ``name`` property and a ``run()`` method returning
            a :class:`~agentry.security.envelope.PreflightCheckResult`).
        skip_preflight: When True, :meth:`run_all` skips all checks and
            returns an empty list with a warning logged.  This corresponds
            to the ``--skip-preflight`` CLI flag.
    """

    def __init__(
        self,
        checks: list[PreflightCheck] | None = None,
        skip_preflight: bool = False,
    ) -> None:
        self._checks: list[PreflightCheck] = checks or []
        self._skip_preflight = skip_preflight

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def skip_preflight(self) -> bool:
        """Whether preflight checks are currently disabled."""
        return self._skip_preflight

    def add_check(self, check: PreflightCheck) -> None:
        """Register an additional preflight check.

        Args:
            check: A check object satisfying the
                :class:`~agentry.security.envelope.PreflightCheck` protocol.
        """
        self._checks.append(check)

    def run_all(
        self,
        *,
        raise_on_failure: bool = False,
    ) -> list[PreflightResult]:
        """Execute all registered checks and collect results.

        When ``skip_preflight`` is True the method returns an empty list
        immediately and logs a warning — no checks are executed.

        All checks are always executed regardless of individual pass/fail
        status so that the developer sees *all* failures at once.

        Args:
            raise_on_failure: If True, raise :class:`PreflightFailedError`
                when one or more checks fail.  Defaults to False so that
                callers can inspect results themselves.

        Returns:
            A list of :class:`PreflightResult` instances, one per registered
            check.  Empty when ``skip_preflight`` is True.

        Raises:
            PreflightFailedError: When *raise_on_failure* is True and at
                least one check did not pass.
        """
        if self._skip_preflight:
            logger.warning(
                "PreflightChecker: preflight checks skipped (--skip-preflight). "
                "This flag is intended for development and debugging only."
            )
            return []

        results: list[PreflightResult] = []

        for check in self._checks:
            try:
                raw = check.run()
                result = PreflightResult(
                    passed=raw.passed,
                    check_name=raw.name,
                    message=raw.message,
                    remediation=getattr(raw, "remediation", ""),
                )
            except Exception as exc:  # noqa: BLE001
                # If a check raises an exception treat it as a failure.
                result = PreflightResult(
                    passed=False,
                    check_name=getattr(check, "name", repr(check)),
                    message=f"Check raised an unexpected exception: {exc}",
                    remediation="Investigate and fix the check implementation.",
                )
                logger.exception(
                    "PreflightChecker: check %r raised an exception",
                    result.check_name,
                )

            results.append(result)
            if result.passed:
                logger.debug(
                    "PreflightChecker: [PASS] %s — %s",
                    result.check_name,
                    result.message,
                )
            else:
                logger.warning(
                    "PreflightChecker: [FAIL] %s — %s",
                    result.check_name,
                    result.message,
                )

        if raise_on_failure and any(not r.passed for r in results):
            raise PreflightFailedError(results)

        return results

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    @staticmethod
    def any_failed(results: list[PreflightResult]) -> bool:
        """Return True if any result in *results* did not pass.

        Args:
            results: A list of :class:`PreflightResult` instances as
                returned by :meth:`run_all`.

        Returns:
            True when at least one check failed.
        """
        return any(not r.passed for r in results)

    @staticmethod
    def failures(results: list[PreflightResult]) -> list[PreflightResult]:
        """Return only the failed results from *results*.

        Args:
            results: A list of :class:`PreflightResult` instances.

        Returns:
            Subset of *results* where ``passed`` is False.
        """
        return [r for r in results if not r.passed]

    @staticmethod
    def to_manifest_entries(
        results: list[PreflightResult],
    ) -> list[dict[str, Any]]:
        """Serialise *results* for inclusion in the setup manifest.

        Each entry is a dict with keys ``name``, ``passed``, ``message``,
        and ``remediation``.

        Args:
            results: A list of :class:`PreflightResult` instances.

        Returns:
            List of plain dicts suitable for JSON encoding.
        """
        return [r.to_dict() for r in results]

    def report_failures(self, results: list[PreflightResult]) -> str:
        """Build a human-readable failure report string.

        Returns an empty string when there are no failures.

        Args:
            results: A list of :class:`PreflightResult` instances.

        Returns:
            A formatted string listing every failed check or an empty
            string if all checks passed.
        """
        failed = self.failures(results)
        if not failed:
            return ""

        lines = ["Preflight checks failed:"]
        for r in failed:
            line = f"  - {r.check_name}: {r.message}"
            if r.remediation:
                line += f"\n    Remediation: {r.remediation}"
            lines.append(line)
        return "\n".join(lines)
