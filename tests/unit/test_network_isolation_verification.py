"""Unit tests for T02.3: DNS query logging and network isolation verification.

Tests cover:
- DNSFilteringProxy.get_execution_record_entries() converts query logs to record format.
- get_execution_record_entries() maps allowed=True to action="resolved".
- get_execution_record_entries() maps allowed=False to action="blocked".
- get_execution_record_entries() returns empty list when no queries.
- ExecutionRecordWriter.write() creates a JSON file with dns_queries section.
- ExecutionRecordWriter.write() includes all DNS query entries.
- ExecutionRecordWriter.write() sets action="resolved" for allowed queries.
- ExecutionRecordWriter.write() sets action="blocked" for blocked queries.
- ExecutionRecordWriter.write() accepts dns_proxy directly.
- ExecutionRecordWriter.write() accepts explicit dns_queries list.
- ExecutionRecordWriter.write() works with no DNS queries.
- NetworkIsolationVerifier.verify() passes when blocked domain is blocked.
- NetworkIsolationVerifier.verify() passes when allowed domain resolves.
- NetworkIsolationVerifier.verify() fails when blocked domain unexpectedly resolves.
- NetworkIsolationVerifier.verify() fails when allowed domain is unexpectedly blocked.
- NetworkIsolationVerifier.verify() returns diagnostic on failure.
- SetupPhase raises NetworkIsolationError when isolation fails.
- SetupPhase succeeds when isolation passes.
- SetupPhase skips verification when no dns_proxy in runner metadata.
- dns_query_log_to_entry() converts DNSQueryLog correctly.
- build_dns_query_entries() converts all proxy log entries.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agentry.runners.dns_proxy import DNSFilteringProxy, DNSQueryLog
from agentry.runners.execution_record_writer import (
    DNSQueryEntry,
    ExecutionRecord,
    ExecutionRecordWriter,
    build_dns_query_entries,
    dns_query_log_to_entry,
)
from agentry.runners.network_isolation import (
    IsolationCheckResult,
    NetworkIsolationResult,
    NetworkIsolationVerifier,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_proxy(
    allowed: list[str] | None = None,
    provider: str | None = "anthropic",
) -> DNSFilteringProxy:
    """Create a proxy with a mock upstream resolver that returns a fake IP."""
    return DNSFilteringProxy(
        allowed_domains=allowed or [],
        provider=provider,
        upstream_resolver=lambda _d, _q: "1.2.3.4",
    )


# ---------------------------------------------------------------------------
# DNSFilteringProxy.get_execution_record_entries
# ---------------------------------------------------------------------------


class TestGetExecutionRecordEntries:
    def test_empty_when_no_queries(self) -> None:
        proxy = _make_proxy(allowed=["example.com"])
        assert proxy.get_execution_record_entries() == []

    def test_resolved_query_action(self) -> None:
        proxy = _make_proxy(allowed=["example.com"])
        proxy.resolve_query("example.com", "A")
        entries = proxy.get_execution_record_entries()
        assert len(entries) == 1
        assert entries[0]["action"] == "resolved"

    def test_blocked_query_action(self) -> None:
        proxy = _make_proxy(allowed=["example.com"])
        proxy.resolve_query("evil.com", "A")
        entries = proxy.get_execution_record_entries()
        assert len(entries) == 1
        assert entries[0]["action"] == "blocked"

    def test_entry_fields_present(self) -> None:
        proxy = _make_proxy(allowed=["example.com"])
        proxy.resolve_query("example.com", "AAAA")
        entries = proxy.get_execution_record_entries()
        entry = entries[0]
        assert entry["domain"] == "example.com"
        assert entry["action"] == "resolved"
        assert entry["query_type"] == "AAAA"
        assert entry["timestamp"]  # non-empty

    def test_multiple_queries(self) -> None:
        proxy = _make_proxy(allowed=["example.com"])
        proxy.resolve_query("example.com", "A")
        proxy.resolve_query("evil.com", "A")
        proxy.resolve_query("sub.example.com", "A")
        entries = proxy.get_execution_record_entries()
        assert len(entries) == 3
        actions = {e["domain"]: e["action"] for e in entries}
        assert actions["example.com"] == "resolved"
        assert actions["evil.com"] == "blocked"
        assert actions["sub.example.com"] == "resolved"

    def test_returns_new_list_each_call(self) -> None:
        proxy = _make_proxy(allowed=["example.com"])
        proxy.resolve_query("example.com", "A")
        entries1 = proxy.get_execution_record_entries()
        entries2 = proxy.get_execution_record_entries()
        assert entries1 == entries2
        assert entries1 is not entries2


# ---------------------------------------------------------------------------
# dns_query_log_to_entry
# ---------------------------------------------------------------------------


class TestDNSQueryLogToEntry:
    def test_allowed_becomes_resolved(self) -> None:
        log = DNSQueryLog(
            domain="api.anthropic.com",
            query_type="A",
            allowed=True,
            timestamp="2026-03-20T12:00:00Z",
        )
        entry = dns_query_log_to_entry(log)
        assert entry.action == "resolved"
        assert entry.domain == "api.anthropic.com"
        assert entry.query_type == "A"
        assert entry.timestamp == "2026-03-20T12:00:00Z"

    def test_blocked_becomes_blocked(self) -> None:
        log = DNSQueryLog(
            domain="evil.com",
            query_type="A",
            allowed=False,
            timestamp="2026-03-20T12:00:01Z",
        )
        entry = dns_query_log_to_entry(log)
        assert entry.action == "blocked"

    def test_to_dict_output(self) -> None:
        log = DNSQueryLog(
            domain="example.com",
            query_type="AAAA",
            allowed=False,
            timestamp="2026-03-20T12:00:02Z",
        )
        entry = dns_query_log_to_entry(log)
        d = entry.to_dict()
        assert d == {
            "domain": "example.com",
            "action": "blocked",
            "query_type": "AAAA",
            "timestamp": "2026-03-20T12:00:02Z",
        }


# ---------------------------------------------------------------------------
# build_dns_query_entries
# ---------------------------------------------------------------------------


class TestBuildDNSQueryEntries:
    def test_empty_proxy_returns_empty(self) -> None:
        proxy = _make_proxy(allowed=["example.com"])
        assert build_dns_query_entries(proxy) == []

    def test_converts_all_entries(self) -> None:
        proxy = _make_proxy(allowed=["example.com"])
        proxy.resolve_query("example.com", "A")
        proxy.resolve_query("evil.com", "A")
        entries = build_dns_query_entries(proxy)
        assert len(entries) == 2
        assert all(isinstance(e, DNSQueryEntry) for e in entries)

    def test_action_mapping(self) -> None:
        proxy = _make_proxy(allowed=["allowed.com"])
        proxy.resolve_query("allowed.com", "A")
        proxy.resolve_query("blocked.com", "A")
        entries = build_dns_query_entries(proxy)
        by_domain = {e.domain: e for e in entries}
        assert by_domain["allowed.com"].action == "resolved"
        assert by_domain["blocked.com"].action == "blocked"


# ---------------------------------------------------------------------------
# ExecutionRecord
# ---------------------------------------------------------------------------


class TestExecutionRecord:
    def test_to_dict_includes_dns_queries(self) -> None:
        entries = [
            DNSQueryEntry(
                domain="api.anthropic.com",
                action="resolved",
                query_type="A",
                timestamp="2026-03-20T12:00:00Z",
            )
        ]
        record = ExecutionRecord(
            execution_id="exec-001",
            timestamp="2026-03-20T12:00:00Z",
            dns_queries=entries,
        )
        d = record.to_dict()
        assert d["execution_id"] == "exec-001"
        assert d["timestamp"] == "2026-03-20T12:00:00Z"
        assert len(d["dns_queries"]) == 1
        assert d["dns_queries"][0]["domain"] == "api.anthropic.com"
        assert d["dns_queries"][0]["action"] == "resolved"

    def test_to_dict_empty_queries(self) -> None:
        record = ExecutionRecord(
            execution_id="exec-002",
            timestamp="2026-03-20T12:00:00Z",
        )
        d = record.to_dict()
        assert d["dns_queries"] == []

    def test_to_dict_includes_extra_fields(self) -> None:
        record = ExecutionRecord(
            execution_id="exec-003",
            timestamp="2026-03-20T12:00:00Z",
            extra={"custom_key": "custom_value"},
        )
        d = record.to_dict()
        assert d["custom_key"] == "custom_value"


# ---------------------------------------------------------------------------
# ExecutionRecordWriter
# ---------------------------------------------------------------------------


class TestExecutionRecordWriter:
    def test_write_creates_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = ExecutionRecordWriter(runs_dir=Path(tmpdir))
            path = writer.write(
                execution_id="exec-abc",
                timestamp="2026-03-20T12:00:00Z",
            )
            assert path.exists()
            assert path.name == "execution-record.json"

    def test_write_with_dns_proxy(self) -> None:
        proxy = _make_proxy(allowed=["example.com"])
        proxy.resolve_query("example.com", "A")
        proxy.resolve_query("evil.com", "A")

        with tempfile.TemporaryDirectory() as tmpdir:
            writer = ExecutionRecordWriter(runs_dir=Path(tmpdir))
            path = writer.write(
                execution_id="exec-def",
                dns_proxy=proxy,
                timestamp="2026-03-20T12:00:00Z",
            )
            data = json.loads(path.read_text())

        assert "dns_queries" in data
        assert len(data["dns_queries"]) == 2

    def test_write_resolved_and_blocked_actions(self) -> None:
        proxy = _make_proxy(allowed=["allowed.com"])
        proxy.resolve_query("allowed.com", "A")
        proxy.resolve_query("blocked.com", "A")

        with tempfile.TemporaryDirectory() as tmpdir:
            writer = ExecutionRecordWriter(runs_dir=Path(tmpdir))
            path = writer.write(
                execution_id="exec-ghi",
                dns_proxy=proxy,
                timestamp="2026-03-20T12:00:00Z",
            )
            data = json.loads(path.read_text())

        by_domain = {e["domain"]: e["action"] for e in data["dns_queries"]}
        assert by_domain["allowed.com"] == "resolved"
        assert by_domain["blocked.com"] == "blocked"

    def test_write_with_explicit_dns_queries(self) -> None:
        entries = [
            DNSQueryEntry(
                domain="api.anthropic.com",
                action="resolved",
                query_type="A",
                timestamp="2026-03-20T12:00:00Z",
            )
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = ExecutionRecordWriter(runs_dir=Path(tmpdir))
            path = writer.write(
                execution_id="exec-jkl",
                dns_queries=entries,
                timestamp="2026-03-20T12:00:00Z",
            )
            data = json.loads(path.read_text())

        assert len(data["dns_queries"]) == 1
        assert data["dns_queries"][0]["domain"] == "api.anthropic.com"

    def test_write_no_queries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = ExecutionRecordWriter(runs_dir=Path(tmpdir))
            path = writer.write(
                execution_id="exec-mno",
                timestamp="2026-03-20T12:00:00Z",
            )
            data = json.loads(path.read_text())

        assert data["dns_queries"] == []

    def test_write_execution_id_in_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = ExecutionRecordWriter(runs_dir=Path(tmpdir))
            path = writer.write(
                execution_id="my-exec-id",
                timestamp="2026-03-20T12:00:00Z",
            )
            data = json.loads(path.read_text())

        assert data["execution_id"] == "my-exec-id"

    def test_write_creates_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runs_dir = Path(tmpdir) / "deep" / "path" / "runs"
            writer = ExecutionRecordWriter(runs_dir=runs_dir)
            path = writer.write(
                execution_id="exec-pqr",
                timestamp="2026-03-20T12:00:00Z",
            )
            assert path.exists()

    def test_write_with_extra_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = ExecutionRecordWriter(runs_dir=Path(tmpdir))
            path = writer.write(
                execution_id="exec-stu",
                timestamp="2026-03-20T12:00:00Z",
                extra={"agent_version": "1.2.3", "workflow": "code-review"},
            )
            data = json.loads(path.read_text())

        assert data["agent_version"] == "1.2.3"
        assert data["workflow"] == "code-review"

    def test_write_json_is_valid(self) -> None:
        proxy = _make_proxy(allowed=["api.anthropic.com"])
        proxy.resolve_query("api.anthropic.com", "A")

        with tempfile.TemporaryDirectory() as tmpdir:
            writer = ExecutionRecordWriter(runs_dir=Path(tmpdir))
            path = writer.write(
                execution_id="exec-vwx",
                dns_proxy=proxy,
                timestamp="2026-03-20T12:00:00Z",
            )
            # Should not raise.
            data = json.loads(path.read_text())
            assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# NetworkIsolationVerifier
# ---------------------------------------------------------------------------


class TestNetworkIsolationVerifier:
    def _make_verifier(
        self,
        allowed_domains: list[str] | None = None,
        blocked_domain: str = "example.com",
        allowed_domain: str = "api.anthropic.com",
    ) -> NetworkIsolationVerifier:
        """Create a verifier with a proxy configured to allow specified domains."""
        proxy = DNSFilteringProxy(
            allowed_domains=allowed_domains or [],
            provider="anthropic",
            upstream_resolver=lambda _d, _q: "1.2.3.4",
        )
        return NetworkIsolationVerifier(
            proxy=proxy,
            blocked_domain=blocked_domain,
            allowed_domain=allowed_domain,
        )

    def test_verify_passes_with_correct_config(self) -> None:
        # allowed_domains is empty; anthropic domain auto-included.
        # example.com is NOT in allow list so it should be blocked.
        verifier = self._make_verifier(allowed_domains=[])
        result = verifier.verify()
        assert result.passed is True

    def test_verify_fails_when_blocked_domain_resolves(self) -> None:
        # Include example.com in allow list — it should be blocked but isn't.
        verifier = self._make_verifier(
            allowed_domains=["example.com"],
            blocked_domain="example.com",
        )
        result = verifier.verify()
        # block check should fail because example.com IS allowed
        block_checks = [
            c for c in result.checks if c.check_name == "proxy_blocks_unlisted_domain"
        ]
        assert len(block_checks) == 1
        assert block_checks[0].passed is False
        assert result.passed is False

    def test_verify_fails_when_allowed_domain_is_blocked(self) -> None:
        # The verifier checks that api.anthropic.com is allowed.
        # Create a proxy with NO provider auto-include and no explicit domain.
        proxy = DNSFilteringProxy(
            allowed_domains=["some-other-domain.com"],
            provider=None,  # no default LLM domains
            upstream_resolver=lambda _d, _q: "1.2.3.4",
        )
        # Override DEFAULT_LLM_DOMAINS effect: create proxy so that
        # api.anthropic.com is NOT in the allow set.
        # We need to ensure api.anthropic.com is not in the set.
        # With provider=None, DEFAULT_LLM_DOMAINS (["api.anthropic.com"]) ARE included.
        # So instead, check with a non-default allowed_domain.
        verifier = NetworkIsolationVerifier(
            proxy=proxy,
            blocked_domain="truly-blocked.example",
            allowed_domain="never-allowed.example",  # not in any allow list
        )
        result = verifier.verify()
        allow_checks = [
            c for c in result.checks if c.check_name == "proxy_allows_llm_api_domain"
        ]
        assert len(allow_checks) == 1
        assert allow_checks[0].passed is False
        assert result.passed is False

    def test_verify_returns_diagnostic_on_failure(self) -> None:
        verifier = self._make_verifier(
            allowed_domains=["example.com"],
            blocked_domain="example.com",
        )
        result = verifier.verify()
        assert result.passed is False
        assert result.diagnostic  # non-empty

    def test_verify_result_contains_checks(self) -> None:
        verifier = self._make_verifier(allowed_domains=[])
        result = verifier.verify()
        assert len(result.checks) >= 2
        check_names = {c.check_name for c in result.checks}
        assert "proxy_blocks_unlisted_domain" in check_names
        assert "proxy_allows_llm_api_domain" in check_names

    def test_verify_metadata_populated(self) -> None:
        verifier = self._make_verifier(allowed_domains=[])
        result = verifier.verify()
        assert result.metadata["blocked_domain"] == "example.com"
        assert result.metadata["allowed_domain"] == "api.anthropic.com"
        assert result.metadata["checks_run"] >= 2

    def test_isolation_check_result_fields(self) -> None:
        check = IsolationCheckResult(
            check_name="test_check",
            passed=True,
            domain="example.com",
            expected_action="blocked",
            actual_action="blocked",
        )
        assert check.passed is True
        assert check.domain == "example.com"
        assert check.expected_action == "blocked"
        assert check.actual_action == "blocked"

    def test_network_isolation_result_all_pass(self) -> None:
        verifier = self._make_verifier(allowed_domains=[])
        result = verifier.verify()
        assert result.passed is True
        assert result.diagnostic == ""


# ---------------------------------------------------------------------------
# SetupPhase integration with NetworkIsolationError
# ---------------------------------------------------------------------------


class TestSetupPhaseNetworkIsolation:
    """Test that SetupPhase correctly integrates network isolation verification."""

    def _make_workflow(self) -> MagicMock:
        """Create a minimal mock WorkflowDefinition."""
        wf = MagicMock()
        wf.safety.sandbox.base = "agentry/sandbox:1.0"
        wf.safety.filesystem.read = []
        wf.safety.filesystem.write = []
        wf.safety.network.allow = []
        wf.safety.resources.cpu = 1.0
        wf.safety.resources.memory = "2GB"
        wf.safety.resources.timeout = 300
        wf.safety.trust.value = "sandboxed"
        wf.identity.name = "test-workflow"
        wf.identity.version = "1.0.0"
        wf.output.schema_def = {}
        wf.output.side_effects = []
        wf.output.output_paths = []
        return wf

    def _make_runner(self, metadata: dict) -> MagicMock:
        """Create a mock runner that returns the given metadata."""
        runner = MagicMock()
        runner.provision.return_value = metadata
        return runner

    def test_setup_skips_verification_when_no_dns_proxy(self, tmp_path: Path) -> None:
        from agentry.security.setup import SetupPhase

        wf = self._make_workflow()
        runner = self._make_runner({"runner_type": "inprocess"})

        phase = SetupPhase(
            workflow=wf,
            runner=runner,
            runs_dir=tmp_path / "runs",
        )
        # Should not raise even though there's no dns_proxy.
        result = phase.run()
        assert not result.aborted

    def test_setup_passes_with_valid_dns_proxy(self, tmp_path: Path) -> None:
        from agentry.security.setup import SetupPhase

        wf = self._make_workflow()

        proxy = DNSFilteringProxy(
            allowed_domains=[],
            provider="anthropic",
            upstream_resolver=lambda _d, _q: "1.2.3.4",
        )
        runner = self._make_runner({"dns_proxy": proxy})

        phase = SetupPhase(
            workflow=wf,
            runner=runner,
            runs_dir=tmp_path / "runs",
        )
        result = phase.run()
        assert not result.aborted

    def test_setup_raises_on_isolation_failure(self, tmp_path: Path) -> None:
        from agentry.security.setup import NetworkIsolationError, SetupPhase

        wf = self._make_workflow()

        # Create a proxy where example.com is ALLOWED (isolation is broken).
        proxy = DNSFilteringProxy(
            allowed_domains=["example.com"],
            provider="anthropic",
            upstream_resolver=lambda _d, _q: "1.2.3.4",
        )
        runner = self._make_runner({"dns_proxy": proxy})

        phase = SetupPhase(
            workflow=wf,
            runner=runner,
            blocked_verification_domain="example.com",
            runs_dir=tmp_path / "runs",
        )
        with pytest.raises(NetworkIsolationError):
            phase.run()
