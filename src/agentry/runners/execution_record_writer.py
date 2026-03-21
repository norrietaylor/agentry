"""Execution record writer for sandboxed agent runs.

Writes execution records to ``.agentry/runs/TIMESTAMP/execution-record.json``.
The execution record captures DNS query logs from the DNS filtering proxy,
allowing operators to audit which domains the sandboxed agent attempted to
resolve during execution.

The record structure is::

    {
        "execution_id": "...",
        "timestamp": "2026-03-20T12:34:56Z",
        "dns_queries": [
            {
                "domain": "api.anthropic.com",
                "action": "resolved",
                "query_type": "A",
                "timestamp": "2026-03-20T12:34:56Z"
            },
            {
                "domain": "example.com",
                "action": "blocked",
                "query_type": "A",
                "timestamp": "2026-03-20T12:34:57Z"
            }
        ]
    }

Usage::

    from agentry.runners.execution_record_writer import ExecutionRecordWriter
    from agentry.runners.dns_proxy import DNSFilteringProxy

    proxy = DNSFilteringProxy(allowed_domains=["api.anthropic.com"])
    # ... run agent ...
    writer = ExecutionRecordWriter(runs_dir=Path(".agentry/runs"))
    record_path = writer.write(
        execution_id="exec-abc123",
        dns_proxy=proxy,
    )
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentry.runners.dns_proxy import DNSFilteringProxy, DNSQueryLog

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class DNSQueryEntry:
    """A single DNS query entry in the execution record.

    Attributes:
        domain: The queried domain name (normalised, no trailing dot).
        action: Either ``"resolved"`` or ``"blocked"``.
        query_type: DNS query type (e.g. ``"A"``, ``"AAAA"``).
        timestamp: ISO 8601 timestamp of the query.
    """

    domain: str
    action: str  # "resolved" or "blocked"
    query_type: str
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary."""
        return {
            "domain": self.domain,
            "action": self.action,
            "query_type": self.query_type,
            "timestamp": self.timestamp,
        }


@dataclass
class ExecutionRecord:
    """An execution record for a single sandboxed agent run.

    Attributes:
        execution_id: Unique identifier for the execution.
        timestamp: ISO 8601 UTC timestamp when the record was created.
        dns_queries: All DNS queries made during the execution.
        extra: Additional arbitrary fields for extensibility.
    """

    execution_id: str
    timestamp: str
    dns_queries: list[DNSQueryEntry] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary."""
        record: dict[str, Any] = {
            "execution_id": self.execution_id,
            "timestamp": self.timestamp,
            "dns_queries": [entry.to_dict() for entry in self.dns_queries],
        }
        record.update(self.extra)
        return record


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------


def dns_query_log_to_entry(log: DNSQueryLog) -> DNSQueryEntry:
    """Convert a :class:`~agentry.runners.dns_proxy.DNSQueryLog` to a record entry.

    Maps the ``allowed`` boolean to the ``action`` string (``"resolved"`` or
    ``"blocked"``).

    Args:
        log: A DNS query log entry from the filtering proxy.

    Returns:
        A :class:`DNSQueryEntry` suitable for inclusion in the execution record.
    """
    action = "resolved" if log.allowed else "blocked"
    return DNSQueryEntry(
        domain=log.domain,
        action=action,
        query_type=log.query_type,
        timestamp=log.timestamp,
    )


def build_dns_query_entries(proxy: DNSFilteringProxy) -> list[DNSQueryEntry]:
    """Extract DNS query entries from a proxy's query log.

    Args:
        proxy: The :class:`~agentry.runners.dns_proxy.DNSFilteringProxy`
            instance that handled DNS queries during execution.

    Returns:
        A list of :class:`DNSQueryEntry` instances, one per logged query.
    """
    return [dns_query_log_to_entry(log) for log in proxy.query_log]


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


class ExecutionRecordWriter:
    """Writes execution records to ``.agentry/runs/TIMESTAMP/execution-record.json``.

    Each call to :meth:`write` creates a new timestamped directory under
    *runs_dir* and writes a JSON execution record containing DNS query logs
    and any additional metadata.

    Args:
        runs_dir: Base directory for run artefacts. Defaults to
            ``Path.cwd() / ".agentry" / "runs"``.
    """

    _FILENAME = "execution-record.json"

    def __init__(self, runs_dir: Path | None = None) -> None:
        self._runs_dir = runs_dir or (Path.cwd() / ".agentry" / "runs")

    def write(
        self,
        execution_id: str,
        dns_proxy: DNSFilteringProxy | None = None,
        dns_queries: list[DNSQueryEntry] | None = None,
        timestamp: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Path:
        """Write an execution record to disk.

        Exactly one of *dns_proxy* or *dns_queries* should be provided.
        When *dns_proxy* is given, its query log is extracted automatically.
        When *dns_queries* is given directly, it is used as-is.  When neither
        is given, the ``dns_queries`` section is empty.

        Args:
            execution_id: Unique identifier for the execution (used in the
                record and to label the run directory).
            dns_proxy: Optional :class:`~agentry.runners.dns_proxy.DNSFilteringProxy`
                from which to extract DNS query logs.
            dns_queries: Optional explicit list of :class:`DNSQueryEntry`
                instances to include in the record.
            timestamp: ISO 8601 timestamp for the record.  When *None*, the
                current UTC time is used.
            extra: Additional fields to include in the record JSON.

        Returns:
            The :class:`pathlib.Path` of the written execution record file.
        """
        if timestamp is None:
            timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # Collect DNS query entries.
        if dns_proxy is not None:
            entries = build_dns_query_entries(dns_proxy)
        elif dns_queries is not None:
            entries = dns_queries
        else:
            entries = []

        record = ExecutionRecord(
            execution_id=execution_id,
            timestamp=timestamp,
            dns_queries=entries,
            extra=extra or {},
        )

        record_path = self._record_path(timestamp)
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(
            json.dumps(record.to_dict(), indent=2),
            encoding="utf-8",
        )
        logger.info(
            "Execution record written to %s (%d DNS queries)",
            record_path,
            len(entries),
        )
        return record_path

    def _record_path(self, timestamp: str) -> Path:
        """Compute the path for an execution record file.

        Derives a filesystem-safe directory name from *timestamp* by stripping
        punctuation (colons, dashes, plus signs) and truncating at the seconds
        boundary.

        Args:
            timestamp: ISO 8601 timestamp string.

        Returns:
            Absolute :class:`pathlib.Path` for the record file.
        """
        ts_safe = (
            timestamp.replace("-", "")
            .replace(":", "")
            .replace("+", "")
            .split(".")[0]
            .rstrip("Z")
        )
        # Re-attach the Z suffix if present in the original.
        if timestamp.endswith("Z"):
            ts_safe = ts_safe + "Z"
        run_dir = self._runs_dir / ts_safe
        return run_dir / self._FILENAME
