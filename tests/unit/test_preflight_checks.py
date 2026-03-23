"""Unit tests for T04.2: Concrete preflight check implementations.

Tests cover:
- AnthropicAPIKeyCheck: missing key, valid key (mocked HTTP 200), invalid key
  (mocked HTTP 401), network error, unexpected HTTP status.
- DockerAvailableCheck: trust=elevated skips check, docker not on PATH,
  docker not running (non-zero exit), docker running (exit 0), timeout.
- FilesystemMountsCheck: no paths (trivial pass), all paths exist, one read
  path missing, one write path missing, multiple paths missing.
"""

from __future__ import annotations

import os
import subprocess
import urllib.error
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from agentry.security.checks import (
    AnthropicAPIKeyCheck,
    ClaudeCodeAuthCheck,
    DockerAvailableCheck,
    FilesystemMountsCheck,
)

# ---------------------------------------------------------------------------
# AnthropicAPIKeyCheck tests
# ---------------------------------------------------------------------------


class TestAnthropicAPIKeyCheck:
    """Tests for AnthropicAPIKeyCheck."""

    def _make_check(self) -> AnthropicAPIKeyCheck:
        return AnthropicAPIKeyCheck(
            env_var="ANTHROPIC_API_KEY",
            api_base="https://api.anthropic.com",
            timeout=5,
        )

    # ------------------------------------------------------------------
    # name property
    # ------------------------------------------------------------------

    def test_name_is_anthropic_api_key(self) -> None:
        check = self._make_check()
        assert check.name == "anthropic_api_key"

    # ------------------------------------------------------------------
    # Missing / empty key
    # ------------------------------------------------------------------

    def test_missing_key_fails(self) -> None:
        check = self._make_check()
        with patch.dict(os.environ, {}, clear=True):
            # Ensure the variable is absent.
            os.environ.pop("ANTHROPIC_API_KEY", None)
            result = check.run()
        assert result.passed is False

    def test_missing_key_message_mentions_api_key(self) -> None:
        check = self._make_check()
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            result = check.run()
        assert "ANTHROPIC_API_KEY" in result.message

    def test_missing_key_includes_remediation(self) -> None:
        check = self._make_check()
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            result = check.run()
        assert result.remediation != ""

    def test_empty_string_key_fails(self) -> None:
        check = self._make_check()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False):
            result = check.run()
        assert result.passed is False

    # ------------------------------------------------------------------
    # Valid key (HTTP 200)
    # ------------------------------------------------------------------

    def _make_http200_response(self) -> Any:
        resp = MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        resp.status = 200
        resp.read = MagicMock(return_value=b'{"data": []}')
        return resp

    def test_valid_key_passes(self) -> None:
        check = self._make_check()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-valid-key"}), patch(
            "urllib.request.urlopen",
            return_value=self._make_http200_response(),
        ):
            result = check.run()
        assert result.passed is True

    def test_valid_key_message_confirms_accepted(self) -> None:
        check = self._make_check()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-valid-key"}), patch(
            "urllib.request.urlopen",
            return_value=self._make_http200_response(),
        ):
            result = check.run()
        assert "accepted" in result.message.lower() or "valid" in result.message.lower()

    # ------------------------------------------------------------------
    # Invalid / revoked key (HTTP 401)
    # ------------------------------------------------------------------

    def _make_http401_error(self) -> urllib.error.HTTPError:
        return urllib.error.HTTPError(
            url="https://api.anthropic.com/v1/models",
            code=401,
            msg="Unauthorized",
            hdrs=MagicMock(),  # type: ignore[arg-type]
            fp=BytesIO(b""),
        )

    def test_invalid_key_401_fails(self) -> None:
        check = self._make_check()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-revoked"}), patch(
            "urllib.request.urlopen",
            side_effect=self._make_http401_error(),
        ):
            result = check.run()
        assert result.passed is False

    def test_invalid_key_401_message_mentions_invalid_or_revoked(self) -> None:
        check = self._make_check()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-revoked"}), patch(
            "urllib.request.urlopen",
            side_effect=self._make_http401_error(),
        ):
            result = check.run()
        msg = result.message.lower()
        assert "invalid" in msg or "revoked" in msg or "401" in msg

    def test_invalid_key_includes_remediation(self) -> None:
        check = self._make_check()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-revoked"}), patch(
            "urllib.request.urlopen",
            side_effect=self._make_http401_error(),
        ):
            result = check.run()
        assert result.remediation != ""

    # ------------------------------------------------------------------
    # Other HTTP error (e.g. 500)
    # ------------------------------------------------------------------

    def test_http_500_fails(self) -> None:
        check = self._make_check()
        err = urllib.error.HTTPError(
            url="https://api.anthropic.com/v1/models",
            code=500,
            msg="Internal Server Error",
            hdrs=MagicMock(),  # type: ignore[arg-type]
            fp=BytesIO(b""),
        )
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-any"}):
            with patch("urllib.request.urlopen", side_effect=err):
                result = check.run()
        assert result.passed is False
        assert "500" in result.message

    # ------------------------------------------------------------------
    # Network / URL error
    # ------------------------------------------------------------------

    def test_network_error_fails(self) -> None:
        check = self._make_check()
        err = urllib.error.URLError(reason="Name or service not known")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-any"}):
            with patch("urllib.request.urlopen", side_effect=err):
                result = check.run()
        assert result.passed is False
        assert result.remediation != ""

    # ------------------------------------------------------------------
    # Custom env_var
    # ------------------------------------------------------------------

    def test_custom_env_var(self) -> None:
        check = AnthropicAPIKeyCheck(env_var="MY_ANTHROPIC_KEY")
        env = {k: v for k, v in os.environ.items() if k != "MY_ANTHROPIC_KEY"}
        with patch.dict(os.environ, env, clear=True):
            result = check.run()
        assert result.passed is False
        assert "MY_ANTHROPIC_KEY" in result.message

    # ------------------------------------------------------------------
    # Result protocol fields
    # ------------------------------------------------------------------

    def test_result_has_name_field(self) -> None:
        check = self._make_check()
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            result = check.run()
        assert result.name == "anthropic_api_key"

    def test_result_has_passed_field(self) -> None:
        check = self._make_check()
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            result = check.run()
        assert isinstance(result.passed, bool)


# ---------------------------------------------------------------------------
# ClaudeCodeAuthCheck tests
# ---------------------------------------------------------------------------


class TestClaudeCodeAuthCheck:
    """Tests for ClaudeCodeAuthCheck."""

    def test_passes_when_api_key_set(self) -> None:
        """Should pass when ANTHROPIC_API_KEY is set, even if claude not on PATH."""
        check = ClaudeCodeAuthCheck()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}), \
             patch("shutil.which", return_value=None):
            result = check.run()
        assert result.passed is True
        assert "ANTHROPIC_API_KEY is set" in result.message

    def test_passes_when_oauth_token_set(self) -> None:
        """Should pass when CLAUDE_CODE_OAUTH_TOKEN is set, even without API key or CLI."""
        check = ClaudeCodeAuthCheck()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "CLAUDE_CODE_OAUTH_TOKEN": "tok-123"}, clear=False), \
             patch("shutil.which", return_value=None):
            result = check.run()
        assert result.passed is True
        assert "CLAUDE_CODE_OAUTH_TOKEN is set" in result.message

    def test_passes_when_claude_on_path(self) -> None:
        """Should pass when claude CLI is available, even without API key."""
        check = ClaudeCodeAuthCheck()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False), \
             patch("shutil.which", return_value="/usr/local/bin/claude"):
            result = check.run()
        assert result.passed is True
        assert "claude CLI is available" in result.message

    def test_fails_when_no_auth(self) -> None:
        """Should fail when neither API key nor claude CLI is available."""
        check = ClaudeCodeAuthCheck()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False), \
             patch("shutil.which", return_value=None):
            result = check.run()
        assert result.passed is False
        assert "No Claude Code authentication found" in result.message
        assert result.remediation != ""

    def test_api_key_takes_precedence(self) -> None:
        """When API key is set, should report that — not check for claude CLI."""
        check = ClaudeCodeAuthCheck()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}), \
             patch("shutil.which") as mock_which:
            result = check.run()
        # shutil.which should not be called when API key is present
        mock_which.assert_not_called()
        assert result.passed is True

    def test_custom_env_var(self) -> None:
        """Should respect custom env var name."""
        check = ClaudeCodeAuthCheck(env_var="MY_KEY")
        with patch.dict(os.environ, {"MY_KEY": "custom-key"}):
            result = check.run()
        assert result.passed is True

    def test_name(self) -> None:
        """Check name is claude_code_auth."""
        check = ClaudeCodeAuthCheck()
        assert check.name == "claude_code_auth"


# ---------------------------------------------------------------------------
# DockerAvailableCheck tests
# ---------------------------------------------------------------------------


class TestDockerAvailableCheck:
    """Tests for DockerAvailableCheck."""

    def _make_sandboxed_check(self) -> DockerAvailableCheck:
        return DockerAvailableCheck(trust="sandboxed")

    # ------------------------------------------------------------------
    # name property
    # ------------------------------------------------------------------

    def test_name_is_docker_available(self) -> None:
        check = self._make_sandboxed_check()
        assert check.name == "docker_available"

    # ------------------------------------------------------------------
    # Non-sandboxed trust levels skip check
    # ------------------------------------------------------------------

    def test_elevated_trust_skips_check_passes(self) -> None:
        check = DockerAvailableCheck(trust="elevated")
        result = check.run()
        assert result.passed is True

    def test_elevated_trust_message_mentions_skip(self) -> None:
        check = DockerAvailableCheck(trust="elevated")
        result = check.run()
        assert "skip" in result.message.lower() or "elevated" in result.message.lower()

    def test_unknown_trust_level_skips_check(self) -> None:
        check = DockerAvailableCheck(trust="custom_level")
        result = check.run()
        assert result.passed is True

    # ------------------------------------------------------------------
    # Docker not on PATH
    # ------------------------------------------------------------------

    def test_docker_not_on_path_fails(self) -> None:
        check = DockerAvailableCheck(trust="sandboxed", ping_command=["docker", "info"])
        with patch("shutil.which", return_value=None):
            result = check.run()
        assert result.passed is False

    def test_docker_not_on_path_message_mentions_executable(self) -> None:
        check = DockerAvailableCheck(trust="sandboxed", ping_command=["docker", "info"])
        with patch("shutil.which", return_value=None):
            result = check.run()
        assert "docker" in result.message.lower()

    def test_docker_not_on_path_includes_remediation(self) -> None:
        check = DockerAvailableCheck(trust="sandboxed", ping_command=["docker", "info"])
        with patch("shutil.which", return_value=None):
            result = check.run()
        assert result.remediation != ""

    # ------------------------------------------------------------------
    # Docker running (exit code 0)
    # ------------------------------------------------------------------

    def test_docker_running_passes(self) -> None:
        check = DockerAvailableCheck(
            trust="sandboxed",
            ping_command=["docker", "info"],
        )
        completed = MagicMock()
        completed.returncode = 0
        completed.stderr = b""
        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run", return_value=completed):
                result = check.run()
        assert result.passed is True

    def test_docker_running_message_mentions_daemon(self) -> None:
        check = DockerAvailableCheck(trust="sandboxed")
        completed = MagicMock()
        completed.returncode = 0
        completed.stderr = b""
        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run", return_value=completed):
                result = check.run()
        assert "docker" in result.message.lower()

    # ------------------------------------------------------------------
    # Docker not running (non-zero exit)
    # ------------------------------------------------------------------

    def test_docker_not_running_fails(self) -> None:
        check = DockerAvailableCheck(trust="sandboxed")
        completed = MagicMock()
        completed.returncode = 1
        completed.stderr = b"Cannot connect to the Docker daemon"
        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run", return_value=completed):
                result = check.run()
        assert result.passed is False

    def test_docker_not_running_message_mentions_not_running(self) -> None:
        check = DockerAvailableCheck(trust="sandboxed")
        completed = MagicMock()
        completed.returncode = 1
        completed.stderr = b"Cannot connect to the Docker daemon"
        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run", return_value=completed):
                result = check.run()
        assert "docker" in result.message.lower()

    def test_docker_not_running_includes_remediation(self) -> None:
        check = DockerAvailableCheck(trust="sandboxed")
        completed = MagicMock()
        completed.returncode = 1
        completed.stderr = b""
        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run", return_value=completed):
                result = check.run()
        assert result.remediation != ""

    # ------------------------------------------------------------------
    # Docker check timeout
    # ------------------------------------------------------------------

    def test_docker_timeout_fails(self) -> None:
        check = DockerAvailableCheck(trust="sandboxed", timeout=5)
        with patch("shutil.which", return_value="/usr/bin/docker"), patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="docker info", timeout=5),
        ):
            result = check.run()
        assert result.passed is False
        assert "timeout" in result.message.lower() or "timed out" in result.message.lower()

    # ------------------------------------------------------------------
    # Custom ping command
    # ------------------------------------------------------------------

    def test_custom_ping_command(self) -> None:
        check = DockerAvailableCheck(
            trust="sandboxed",
            ping_command=["docker", "version"],
        )
        completed = MagicMock()
        completed.returncode = 0
        completed.stderr = b""
        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run", return_value=completed) as mock_run:
                check.run()
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args == ["docker", "version"]

    # ------------------------------------------------------------------
    # Result protocol fields
    # ------------------------------------------------------------------

    def test_result_has_name_field(self) -> None:
        check = DockerAvailableCheck(trust="elevated")
        result = check.run()
        assert result.name == "docker_available"

    def test_result_has_passed_field(self) -> None:
        check = DockerAvailableCheck(trust="elevated")
        result = check.run()
        assert isinstance(result.passed, bool)


# ---------------------------------------------------------------------------
# FilesystemMountsCheck tests
# ---------------------------------------------------------------------------


class TestFilesystemMountsCheck:
    """Tests for FilesystemMountsCheck."""

    # ------------------------------------------------------------------
    # name property
    # ------------------------------------------------------------------

    def test_name_is_filesystem_mounts(self) -> None:
        check = FilesystemMountsCheck()
        assert check.name == "filesystem_mounts"

    # ------------------------------------------------------------------
    # No paths declared — trivial pass
    # ------------------------------------------------------------------

    def test_no_paths_passes(self) -> None:
        check = FilesystemMountsCheck()
        result = check.run()
        assert result.passed is True

    def test_no_paths_message_describes_trivial(self) -> None:
        check = FilesystemMountsCheck()
        result = check.run()
        assert result.passed is True
        assert result.message != ""

    # ------------------------------------------------------------------
    # All paths exist
    # ------------------------------------------------------------------

    def test_all_paths_exist_passes(self, tmp_path: Path) -> None:
        read_dir = tmp_path / "src"
        write_dir = tmp_path / "output"
        read_dir.mkdir()
        write_dir.mkdir()
        check = FilesystemMountsCheck(
            read_paths=[str(read_dir)],
            write_paths=[str(write_dir)],
        )
        result = check.run()
        assert result.passed is True

    def test_all_paths_exist_message_mentions_count(self, tmp_path: Path) -> None:
        d1 = tmp_path / "a"
        d2 = tmp_path / "b"
        d1.mkdir()
        d2.mkdir()
        check = FilesystemMountsCheck(read_paths=[str(d1), str(d2)])
        result = check.run()
        assert result.passed is True
        # Should mention the total number of paths checked.
        assert "2" in result.message

    # ------------------------------------------------------------------
    # Read path missing
    # ------------------------------------------------------------------

    def test_missing_read_path_fails(self) -> None:
        check = FilesystemMountsCheck(read_paths=["/nonexistent/read/path"])
        result = check.run()
        assert result.passed is False

    def test_missing_read_path_message_identifies_path(self) -> None:
        check = FilesystemMountsCheck(read_paths=["/nonexistent/read/path"])
        result = check.run()
        assert "/nonexistent/read/path" in result.message

    def test_missing_read_path_includes_remediation(self) -> None:
        check = FilesystemMountsCheck(read_paths=["/nonexistent/path"])
        result = check.run()
        assert result.remediation != ""

    # ------------------------------------------------------------------
    # Write path missing
    # ------------------------------------------------------------------

    def test_missing_write_path_fails(self) -> None:
        check = FilesystemMountsCheck(write_paths=["/nonexistent/write/path"])
        result = check.run()
        assert result.passed is False

    def test_missing_write_path_message_identifies_path(self) -> None:
        check = FilesystemMountsCheck(write_paths=["/nonexistent/write/path"])
        result = check.run()
        assert "/nonexistent/write/path" in result.message

    # ------------------------------------------------------------------
    # Multiple paths — some missing, some exist
    # ------------------------------------------------------------------

    def test_multiple_paths_one_missing_fails(self, tmp_path: Path) -> None:
        existing = tmp_path / "exists"
        existing.mkdir()
        check = FilesystemMountsCheck(
            read_paths=[str(existing), "/does/not/exist"],
        )
        result = check.run()
        assert result.passed is False

    def test_multiple_paths_one_missing_message_identifies_missing(
        self, tmp_path: Path
    ) -> None:
        existing = tmp_path / "exists"
        existing.mkdir()
        check = FilesystemMountsCheck(
            read_paths=[str(existing), "/does/not/exist"],
        )
        result = check.run()
        assert "/does/not/exist" in result.message

    def test_multiple_paths_both_missing_lists_both(self) -> None:
        check = FilesystemMountsCheck(
            read_paths=["/missing/read"],
            write_paths=["/missing/write"],
        )
        result = check.run()
        assert result.passed is False
        assert "/missing/read" in result.message
        assert "/missing/write" in result.message

    def test_multiple_missing_reports_count(self) -> None:
        check = FilesystemMountsCheck(
            read_paths=["/missing/a", "/missing/b"],
        )
        result = check.run()
        assert result.passed is False
        assert "2" in result.message

    # ------------------------------------------------------------------
    # Read and write paths interleave correctly
    # ------------------------------------------------------------------

    def test_read_exists_write_missing_fails(self, tmp_path: Path) -> None:
        existing = tmp_path / "read"
        existing.mkdir()
        check = FilesystemMountsCheck(
            read_paths=[str(existing)],
            write_paths=["/missing/write"],
        )
        result = check.run()
        assert result.passed is False

    def test_read_missing_write_exists_fails(self, tmp_path: Path) -> None:
        existing = tmp_path / "write"
        existing.mkdir()
        check = FilesystemMountsCheck(
            read_paths=["/missing/read"],
            write_paths=[str(existing)],
        )
        result = check.run()
        assert result.passed is False

    # ------------------------------------------------------------------
    # Result protocol fields
    # ------------------------------------------------------------------

    def test_result_has_name_field(self) -> None:
        check = FilesystemMountsCheck()
        result = check.run()
        assert result.name == "filesystem_mounts"

    def test_result_has_passed_field(self) -> None:
        check = FilesystemMountsCheck()
        result = check.run()
        assert isinstance(result.passed, bool)


# ---------------------------------------------------------------------------
# Integration: checks satisfy PreflightCheck protocol
# ---------------------------------------------------------------------------


class TestPreflightCheckProtocolCompliance:
    """Verify the concrete checks satisfy PreflightCheck protocol."""

    def test_api_key_check_has_name_property(self) -> None:
        check = AnthropicAPIKeyCheck()
        assert isinstance(check.name, str)
        assert check.name

    def test_docker_check_has_name_property(self) -> None:
        check = DockerAvailableCheck()
        assert isinstance(check.name, str)
        assert check.name

    def test_filesystem_check_has_name_property(self) -> None:
        check = FilesystemMountsCheck()
        assert isinstance(check.name, str)
        assert check.name

    def test_api_key_check_run_returns_result_with_protocol_fields(self) -> None:
        check = AnthropicAPIKeyCheck()
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            result = check.run()
        # Must have .passed, .name, .message (PreflightCheckResult fields)
        assert hasattr(result, "passed")
        assert hasattr(result, "name")
        assert hasattr(result, "message")

    def test_docker_check_run_returns_result_with_protocol_fields(self) -> None:
        check = DockerAvailableCheck(trust="elevated")
        result = check.run()
        assert hasattr(result, "passed")
        assert hasattr(result, "name")
        assert hasattr(result, "message")

    def test_filesystem_check_run_returns_result_with_protocol_fields(self) -> None:
        check = FilesystemMountsCheck()
        result = check.run()
        assert hasattr(result, "passed")
        assert hasattr(result, "name")
        assert hasattr(result, "message")

    def test_all_checks_work_with_preflight_checker(self) -> None:
        """Concrete checks should integrate with PreflightChecker.run_all()."""
        from agentry.security.preflight import PreflightChecker

        checks = [
            AnthropicAPIKeyCheck(),
            DockerAvailableCheck(trust="elevated"),
            FilesystemMountsCheck(),
        ]
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            checker = PreflightChecker(checks=checks)
            results = checker.run_all()
        # Three checks, three results.
        assert len(results) == 3
