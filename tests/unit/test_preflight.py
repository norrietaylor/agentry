"""Unit tests for T04.1: PreflightChecker framework and check interface.

Tests cover:
- PreflightResult dataclass fields (passed, check_name, message, remediation).
- PreflightResult.to_dict() serialises all fields correctly.
- PreflightChecker.run_all() executes all registered checks.
- PreflightChecker.run_all() collects all failures (not just the first).
- PreflightChecker.run_all() returns empty list when skip_preflight=True.
- PreflightChecker.run_all(raise_on_failure=True) raises PreflightFailedError.
- PreflightChecker.any_failed() returns correct bool.
- PreflightChecker.failures() returns only failed results.
- PreflightChecker.to_manifest_entries() serialises for the setup manifest.
- PreflightChecker.report_failures() returns formatted report string.
- PreflightChecker.add_check() registers additional checks.
- Checks that raise exceptions are captured as failures.
- PreflightFailedError contains all failures (not just the first).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from agentry.security.preflight import (
    PreflightChecker,
    PreflightFailedError,
    PreflightResult,
)

# ---------------------------------------------------------------------------
# Helpers / stub checks
# ---------------------------------------------------------------------------


@dataclass
class _StubCheckResult:
    """Minimal substitute for PreflightCheckResult used in stub checks."""

    passed: bool
    name: str
    message: str = ""
    remediation: str = ""


class _PassingCheck:
    """A stub preflight check that always passes."""

    @property
    def name(self) -> str:
        return "always_passes"

    def run(self) -> _StubCheckResult:
        return _StubCheckResult(passed=True, name=self.name, message="All good.")


class _FailingCheck:
    """A stub preflight check that always fails."""

    def __init__(
        self,
        check_name: str = "always_fails",
        message: str = "Something is wrong.",
        remediation: str = "Fix it.",
    ) -> None:
        self._name = check_name
        self._message = message
        self._remediation = remediation

    @property
    def name(self) -> str:
        return self._name

    def run(self) -> _StubCheckResult:
        return _StubCheckResult(
            passed=False,
            name=self._name,
            message=self._message,
            remediation=self._remediation,
        )


class _RaisingCheck:
    """A stub preflight check that raises an exception when run."""

    @property
    def name(self) -> str:
        return "raises_exception"

    def run(self) -> _StubCheckResult:
        raise RuntimeError("unexpected error in check")


# ---------------------------------------------------------------------------
# PreflightResult tests
# ---------------------------------------------------------------------------


class TestPreflightResult:
    def test_passing_result_fields(self) -> None:
        result = PreflightResult(
            passed=True,
            check_name="api_key",
            message="Key is valid.",
            remediation="",
        )
        assert result.passed is True
        assert result.check_name == "api_key"
        assert result.message == "Key is valid."
        assert result.remediation == ""

    def test_failing_result_fields(self) -> None:
        result = PreflightResult(
            passed=False,
            check_name="docker",
            message="Docker is not running.",
            remediation="Start Docker Desktop.",
        )
        assert result.passed is False
        assert result.remediation == "Start Docker Desktop."

    def test_to_dict_all_fields(self) -> None:
        result = PreflightResult(
            passed=False,
            check_name="api_key",
            message="Key missing.",
            remediation="Set ANTHROPIC_API_KEY.",
        )
        d = result.to_dict()
        assert d == {
            "name": "api_key",
            "passed": False,
            "message": "Key missing.",
            "remediation": "Set ANTHROPIC_API_KEY.",
        }

    def test_to_dict_defaults(self) -> None:
        result = PreflightResult(passed=True, check_name="simple")
        d = result.to_dict()
        assert d["name"] == "simple"
        assert d["passed"] is True
        assert d["message"] == ""
        assert d["remediation"] == ""


# ---------------------------------------------------------------------------
# PreflightChecker.run_all() tests
# ---------------------------------------------------------------------------


class TestPreflightCheckerRunAll:
    def test_run_all_no_checks_returns_empty(self) -> None:
        checker = PreflightChecker()
        results = checker.run_all()
        assert results == []

    def test_run_all_single_passing_check(self) -> None:
        checker = PreflightChecker(checks=[_PassingCheck()])
        results = checker.run_all()
        assert len(results) == 1
        assert results[0].passed is True
        assert results[0].check_name == "always_passes"

    def test_run_all_single_failing_check(self) -> None:
        checker = PreflightChecker(checks=[_FailingCheck()])
        results = checker.run_all()
        assert len(results) == 1
        assert results[0].passed is False

    def test_run_all_collects_all_failures(self) -> None:
        """All failing checks must be reported, not just the first one."""
        checker = PreflightChecker(
            checks=[
                _FailingCheck("check_a", "A is broken."),
                _FailingCheck("check_b", "B is broken."),
                _PassingCheck(),
            ]
        )
        results = checker.run_all()
        assert len(results) == 3
        failed = [r for r in results if not r.passed]
        assert len(failed) == 2
        failed_names = {r.check_name for r in failed}
        assert "check_a" in failed_names
        assert "check_b" in failed_names

    def test_run_all_does_not_stop_after_first_failure(self) -> None:
        """Verifies that all checks run even when an early one fails."""
        execution_order: list[str] = []

        class _TrackingFail:
            @property
            def name(self) -> str:
                return "fail_first"

            def run(self) -> _StubCheckResult:
                execution_order.append("fail_first")
                return _StubCheckResult(passed=False, name=self.name, message="fail")

        class _TrackingPass:
            @property
            def name(self) -> str:
                return "pass_second"

            def run(self) -> _StubCheckResult:
                execution_order.append("pass_second")
                return _StubCheckResult(passed=True, name=self.name, message="ok")

        checker = PreflightChecker(checks=[_TrackingFail(), _TrackingPass()])
        checker.run_all()
        assert execution_order == ["fail_first", "pass_second"]

    def test_run_all_check_raises_exception_captured_as_failure(self) -> None:
        checker = PreflightChecker(checks=[_RaisingCheck()])
        results = checker.run_all()
        assert len(results) == 1
        assert results[0].passed is False
        assert "unexpected error in check" in results[0].message

    def test_run_all_raise_on_failure_raises_preflight_failed_error(self) -> None:
        checker = PreflightChecker(checks=[_FailingCheck()])
        with pytest.raises(PreflightFailedError) as exc_info:
            checker.run_all(raise_on_failure=True)
        assert "always_fails" in str(exc_info.value)

    def test_run_all_raise_on_failure_no_raise_when_all_pass(self) -> None:
        checker = PreflightChecker(checks=[_PassingCheck()])
        # Should not raise.
        results = checker.run_all(raise_on_failure=True)
        assert len(results) == 1
        assert results[0].passed is True

    def test_run_all_preserves_result_order(self) -> None:
        checker = PreflightChecker(
            checks=[
                _FailingCheck("first"),
                _PassingCheck(),
                _FailingCheck("third"),
            ]
        )
        results = checker.run_all()
        assert results[0].check_name == "first"
        assert results[1].check_name == "always_passes"
        assert results[2].check_name == "third"


# ---------------------------------------------------------------------------
# skip_preflight tests
# ---------------------------------------------------------------------------


class TestSkipPreflight:
    def test_skip_preflight_returns_empty_list(self) -> None:
        checker = PreflightChecker(
            checks=[_FailingCheck()],
            skip_preflight=True,
        )
        results = checker.run_all()
        assert results == []

    def test_skip_preflight_property(self) -> None:
        checker = PreflightChecker(skip_preflight=True)
        assert checker.skip_preflight is True

    def test_skip_preflight_false_by_default(self) -> None:
        checker = PreflightChecker()
        assert checker.skip_preflight is False

    def test_skip_preflight_does_not_raise_when_raise_on_failure_set(self) -> None:
        checker = PreflightChecker(
            checks=[_FailingCheck()],
            skip_preflight=True,
        )
        # Should not raise even though checks would fail.
        results = checker.run_all(raise_on_failure=True)
        assert results == []


# ---------------------------------------------------------------------------
# Convenience helper tests
# ---------------------------------------------------------------------------


class TestPreflightCheckerHelpers:
    def _make_results(self) -> list[PreflightResult]:
        return [
            PreflightResult(passed=True, check_name="a", message="ok"),
            PreflightResult(passed=False, check_name="b", message="fail b"),
            PreflightResult(passed=False, check_name="c", message="fail c"),
        ]

    def test_any_failed_true_when_failures_present(self) -> None:
        results = self._make_results()
        assert PreflightChecker.any_failed(results) is True

    def test_any_failed_false_when_all_pass(self) -> None:
        results = [PreflightResult(passed=True, check_name="x")]
        assert PreflightChecker.any_failed(results) is False

    def test_any_failed_false_on_empty_list(self) -> None:
        assert PreflightChecker.any_failed([]) is False

    def test_failures_returns_only_failed(self) -> None:
        results = self._make_results()
        failed = PreflightChecker.failures(results)
        assert len(failed) == 2
        names = {r.check_name for r in failed}
        assert names == {"b", "c"}

    def test_failures_empty_when_all_pass(self) -> None:
        results = [PreflightResult(passed=True, check_name="x")]
        assert PreflightChecker.failures(results) == []

    def test_to_manifest_entries_serialises_all(self) -> None:
        results = self._make_results()
        entries = PreflightChecker.to_manifest_entries(results)
        assert len(entries) == 3
        names = [e["name"] for e in entries]
        assert names == ["a", "b", "c"]
        for entry in entries:
            assert "passed" in entry
            assert "message" in entry
            assert "remediation" in entry

    def test_report_failures_empty_when_all_pass(self) -> None:
        checker = PreflightChecker()
        results = [PreflightResult(passed=True, check_name="x")]
        assert checker.report_failures(results) == ""

    def test_report_failures_lists_all_failed_checks(self) -> None:
        checker = PreflightChecker()
        results = self._make_results()
        report = checker.report_failures(results)
        assert "fail b" in report
        assert "fail c" in report
        assert "Preflight checks failed" in report

    def test_report_failures_includes_remediation(self) -> None:
        checker = PreflightChecker()
        results = [
            PreflightResult(
                passed=False,
                check_name="api",
                message="Key missing.",
                remediation="Set ANTHROPIC_API_KEY.",
            )
        ]
        report = checker.report_failures(results)
        assert "Set ANTHROPIC_API_KEY." in report

    def test_add_check_registers_new_check(self) -> None:
        checker = PreflightChecker()
        assert checker.run_all() == []
        checker.add_check(_PassingCheck())
        results = checker.run_all()
        assert len(results) == 1


# ---------------------------------------------------------------------------
# PreflightFailedError tests
# ---------------------------------------------------------------------------


class TestPreflightFailedError:
    def test_error_contains_all_failures(self) -> None:
        results = [
            PreflightResult(passed=False, check_name="api_key", message="Key missing."),
            PreflightResult(passed=True, check_name="docker", message="ok"),
            PreflightResult(passed=False, check_name="fs", message="Path not found."),
        ]
        error = PreflightFailedError(results)
        assert len(error.failures) == 2
        failed_names = {r.check_name for r in error.failures}
        assert "api_key" in failed_names
        assert "fs" in failed_names

    def test_error_results_contains_all_results(self) -> None:
        results = [
            PreflightResult(passed=True, check_name="a"),
            PreflightResult(passed=False, check_name="b"),
        ]
        error = PreflightFailedError(results)
        assert len(error.results) == 2

    def test_error_message_contains_failed_check_names(self) -> None:
        results = [
            PreflightResult(
                passed=False,
                check_name="api_key",
                message="ANTHROPIC_API_KEY is not set.",
                remediation="Export the variable.",
            )
        ]
        error = PreflightFailedError(results)
        assert "api_key" in str(error)
        assert "ANTHROPIC_API_KEY is not set." in str(error)

    def test_error_is_exception(self) -> None:
        error = PreflightFailedError([])
        assert isinstance(error, Exception)
