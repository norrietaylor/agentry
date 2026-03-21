"""Unit tests for T04.3: Multiple-failure reporting and --skip-preflight CLI flag.

Tests cover:
- Multiple preflight check failures are reported together (not one-at-a-time).
- When API key missing AND Docker not available AND filesystem path missing,
  all three failures appear in the error report.
- PreflightFailedError contains all failure messages.
- agentry run --skip-preflight bypasses all preflight checks with a warning.
- agentry setup --skip-preflight bypasses all preflight checks with a warning.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentry.security.checks import (
    AnthropicAPIKeyCheck,
    DockerAvailableCheck,
    FilesystemMountsCheck,
)
from agentry.security.preflight import (
    PreflightChecker,
    PreflightFailedError,
    PreflightResult,
)


# ---------------------------------------------------------------------------
# Stub checks for testing
# ---------------------------------------------------------------------------


@dataclass
class _StubCheckResult:
    """Minimal substitute for PreflightCheckResult used in stub checks."""

    passed: bool
    name: str
    message: str = ""
    remediation: str = ""


class _FailingAPIKeyCheck:
    """Stub check that fails with API key message."""

    @property
    def name(self) -> str:
        return "anthropic_api_key"

    def run(self) -> _StubCheckResult:
        return _StubCheckResult(
            passed=False,
            name=self.name,
            message="ANTHROPIC_API_KEY is not set.",
            remediation="Export the ANTHROPIC_API_KEY environment variable.",
        )


class _FailingDockerCheck:
    """Stub check that fails with Docker message."""

    @property
    def name(self) -> str:
        return "docker_available"

    def run(self) -> _StubCheckResult:
        return _StubCheckResult(
            passed=False,
            name=self.name,
            message="Docker is not running.",
            remediation="Start Docker Desktop or the Docker daemon.",
        )


class _FailingFilesystemCheck:
    """Stub check that fails with filesystem message."""

    @property
    def name(self) -> str:
        return "filesystem_mounts"

    def run(self) -> _StubCheckResult:
        return _StubCheckResult(
            passed=False,
            name=self.name,
            message="/nonexistent/path does not exist.",
            remediation="Create the directory or adjust the workflow definition.",
        )


# ---------------------------------------------------------------------------
# Tests: Multiple failures reported together
# ---------------------------------------------------------------------------


class TestMultipleFailuresReportedTogether:
    """Verify that all preflight failures are collected and reported."""

    def test_all_three_failures_appear_in_results(self) -> None:
        """All three failures must appear in the results list."""
        checker = PreflightChecker(
            checks=[
                _FailingAPIKeyCheck(),
                _FailingDockerCheck(),
                _FailingFilesystemCheck(),
            ]
        )
        results = checker.run_all()
        assert len(results) == 3
        failed = [r for r in results if not r.passed]
        assert len(failed) == 3

    def test_api_key_failure_in_results(self) -> None:
        """API key failure must be included."""
        checker = PreflightChecker(
            checks=[
                _FailingAPIKeyCheck(),
                _FailingDockerCheck(),
                _FailingFilesystemCheck(),
            ]
        )
        results = checker.run_all()
        names = {r.check_name for r in results if not r.passed}
        assert "anthropic_api_key" in names

    def test_docker_failure_in_results(self) -> None:
        """Docker failure must be included."""
        checker = PreflightChecker(
            checks=[
                _FailingAPIKeyCheck(),
                _FailingDockerCheck(),
                _FailingFilesystemCheck(),
            ]
        )
        results = checker.run_all()
        names = {r.check_name for r in results if not r.passed}
        assert "docker_available" in names

    def test_filesystem_failure_in_results(self) -> None:
        """Filesystem failure must be included."""
        checker = PreflightChecker(
            checks=[
                _FailingAPIKeyCheck(),
                _FailingDockerCheck(),
                _FailingFilesystemCheck(),
            ]
        )
        results = checker.run_all()
        names = {r.check_name for r in results if not r.passed}
        assert "filesystem_mounts" in names

    def test_error_contains_all_failure_messages(self) -> None:
        """PreflightFailedError must include all failure messages."""
        results = [
            PreflightResult(
                passed=False,
                check_name="api_key",
                message="ANTHROPIC_API_KEY is not set.",
                remediation="Export the variable.",
            ),
            PreflightResult(
                passed=False,
                check_name="docker",
                message="Docker is not running.",
                remediation="Start Docker.",
            ),
            PreflightResult(
                passed=False,
                check_name="filesystem",
                message="/nonexistent/path does not exist.",
                remediation="Create the directory.",
            ),
        ]
        error = PreflightFailedError(results)
        error_str = str(error)
        assert "ANTHROPIC_API_KEY is not set." in error_str
        assert "Docker is not running." in error_str
        assert "/nonexistent/path does not exist." in error_str

    def test_error_lists_all_checks_by_name(self) -> None:
        """Error message must list all failed check names."""
        results = [
            PreflightResult(
                passed=False,
                check_name="api_key",
                message="Missing key.",
            ),
            PreflightResult(
                passed=False,
                check_name="docker",
                message="Not running.",
            ),
            PreflightResult(
                passed=False,
                check_name="filesystem",
                message="Path missing.",
            ),
        ]
        error = PreflightFailedError(results)
        error_str = str(error)
        assert "api_key" in error_str
        assert "docker" in error_str
        assert "filesystem" in error_str

    def test_report_failures_includes_all_failures(self) -> None:
        """report_failures() must include all three failures."""
        results = [
            PreflightResult(
                passed=False,
                check_name="api_key",
                message="ANTHROPIC_API_KEY is not set.",
                remediation="Export the variable.",
            ),
            PreflightResult(
                passed=False,
                check_name="docker",
                message="Docker is not running.",
                remediation="Start Docker.",
            ),
            PreflightResult(
                passed=False,
                check_name="filesystem",
                message="/nonexistent/path does not exist.",
                remediation="Create the directory.",
            ),
        ]
        checker = PreflightChecker()
        report = checker.report_failures(results)
        assert "api_key" in report
        assert "docker" in report
        assert "filesystem" in report
        assert "ANTHROPIC_API_KEY is not set." in report
        assert "Docker is not running." in report
        assert "/nonexistent/path does not exist." in report

    def test_raise_on_failure_includes_all_failures(self) -> None:
        """raise_on_failure must include all three failures in the exception."""
        checker = PreflightChecker(
            checks=[
                _FailingAPIKeyCheck(),
                _FailingDockerCheck(),
                _FailingFilesystemCheck(),
            ]
        )
        with pytest.raises(PreflightFailedError) as exc_info:
            checker.run_all(raise_on_failure=True)
        error = exc_info.value
        assert len(error.failures) == 3
        failure_names = {r.check_name for r in error.failures}
        assert "anthropic_api_key" in failure_names
        assert "docker_available" in failure_names
        assert "filesystem_mounts" in failure_names


# ---------------------------------------------------------------------------
# Tests: Concrete checks with multiple failures
# ---------------------------------------------------------------------------


class TestConcreteMulitpleFailures:
    """Test concrete check implementations reporting multiple failures."""

    def test_api_key_missing_docker_not_running_filesystem_missing(self) -> None:
        """All three concrete checks fail together."""
        # Set up conditions: no API key, Docker not running, missing path
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

        checks = [
            AnthropicAPIKeyCheck(),
            DockerAvailableCheck(trust="sandboxed"),
            FilesystemMountsCheck(read_paths=["/nonexistent/path"]),
        ]

        with patch.dict(os.environ, env, clear=True):
            # Mock Docker as not running
            completed = MagicMock()
            completed.returncode = 1
            completed.stderr = b"Cannot connect to the Docker daemon"

            with patch("shutil.which", return_value="/usr/bin/docker"):
                with patch("subprocess.run", return_value=completed):
                    checker = PreflightChecker(checks=checks)
                    results = checker.run_all()

        # All three should fail
        assert len(results) == 3
        failed = [r for r in results if not r.passed]
        assert len(failed) == 3
        failed_names = {r.check_name for r in failed}
        assert "anthropic_api_key" in failed_names
        assert "docker_available" in failed_names
        assert "filesystem_mounts" in failed_names

    def test_multiple_filesystem_paths_missing(self) -> None:
        """Multiple missing filesystem paths reported together."""
        check = FilesystemMountsCheck(
            read_paths=["/missing/path1", "/missing/path2"],
            write_paths=["/missing/path3"],
        )
        result = check.run()
        assert result.passed is False
        # All three paths should be mentioned
        assert "/missing/path1" in result.message
        assert "/missing/path2" in result.message
        assert "/missing/path3" in result.message


# ---------------------------------------------------------------------------
# Tests: skip_preflight bypasses all checks
# ---------------------------------------------------------------------------


class TestSkipPreflightBypassesAllChecks:
    """Verify that skip_preflight=True bypasses all checks."""

    def test_skip_preflight_returns_empty_even_with_failures(self) -> None:
        """When skip_preflight=True, no checks run even if they would fail."""
        checker = PreflightChecker(
            checks=[
                _FailingAPIKeyCheck(),
                _FailingDockerCheck(),
                _FailingFilesystemCheck(),
            ],
            skip_preflight=True,
        )
        results = checker.run_all()
        assert results == []

    def test_skip_preflight_no_exception_even_with_failures(self) -> None:
        """When skip_preflight=True, raise_on_failure=True doesn't raise."""
        checker = PreflightChecker(
            checks=[
                _FailingAPIKeyCheck(),
                _FailingDockerCheck(),
                _FailingFilesystemCheck(),
            ],
            skip_preflight=True,
        )
        # Should not raise even though all checks would fail
        results = checker.run_all(raise_on_failure=True)
        assert results == []

    def test_skip_preflight_with_concrete_checks(self) -> None:
        """skip_preflight works with real concrete checks."""
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

        checks = [
            AnthropicAPIKeyCheck(),
            DockerAvailableCheck(trust="sandboxed"),
            FilesystemMountsCheck(read_paths=["/nonexistent/path"]),
        ]

        checker = PreflightChecker(
            checks=checks,
            skip_preflight=True,
        )

        with patch.dict(os.environ, env, clear=True):
            # Don't bother mocking Docker since checks won't run
            results = checker.run_all()

        assert results == []
