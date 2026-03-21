"""Composition progress display with TTY-aware formatting.

Provides human-readable and JSON output for composed workflow execution,
including per-node start/complete/fail/skip events and an execution summary.

Usage::

    from agentry.composition.display import CompositionDisplay

    display = CompositionDisplay(is_tty=True, output_format="text")
    display.on_node_start("triage")
    display.on_node_complete("triage", duration=2.3)
    display.on_node_fail("decompose", error="timeout after 60s")
    display.on_node_skip("assign")
    display.print_summary(record)
"""

from __future__ import annotations

import sys
import threading
import time
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from agentry.composition.record import CompositionRecord


# ---------------------------------------------------------------------------
# Spinner helper (TTY only)
# ---------------------------------------------------------------------------


class _Spinner:
    """A very small ASCII spinner for TTY progress indication.

    The spinner runs in a background thread and writes to *stream* using
    ``\\r`` to overwrite the current line.  It is stopped (and the line
    cleared) by calling :meth:`stop`.

    Args:
        label: The text to display next to the spinner.
        stream: Output stream; defaults to ``sys.stdout``.
    """

    _FRAMES = ["-", "\\", "|", "/"]

    def __init__(self, label: str, stream: TextIO | None = None) -> None:
        self._label = label
        self._stream = stream or sys.stdout
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the spinner thread."""
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the spinner thread and clear the line."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        # Clear the spinner line.
        self._stream.write("\r" + " " * (len(self._label) + 10) + "\r")
        self._stream.flush()

    def _spin(self) -> None:
        idx = 0
        while not self._stop_event.is_set():
            frame = self._FRAMES[idx % len(self._FRAMES)]
            line = f"\r[{frame}] {self._label}..."
            self._stream.write(line)
            self._stream.flush()
            idx += 1
            time.sleep(0.1)


# ---------------------------------------------------------------------------
# Main display class
# ---------------------------------------------------------------------------


class CompositionDisplay:
    """TTY-aware progress display for composed workflow execution.

    In TTY mode, active nodes show a spinning indicator.  In non-TTY mode
    (pipes / CI), plain log lines are emitted instead.  Both modes emit the
    same final execution summary.

    Args:
        is_tty: Whether output is going to a terminal.
        output_format: ``"text"`` for human-readable output, ``"json"`` to
            suppress per-event output (summary is still emitted as JSON via
            the CompositionRecord).
        stream: Output stream; defaults to ``sys.stdout``.
    """

    def __init__(
        self,
        is_tty: bool = False,
        output_format: str = "text",
        stream: TextIO | None = None,
    ) -> None:
        self._is_tty = is_tty
        self._output_format = output_format
        self._stream = stream or sys.stdout

        # Active spinners, keyed by node_id.
        self._spinners: dict[str, _Spinner] = {}
        # Per-node start timestamps for duration calculation.
        self._start_times: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Event callbacks (called by CompositionEngine during execution)
    # ------------------------------------------------------------------

    def on_node_start(self, node_id: str) -> None:
        """Called when a node begins execution.

        In TTY mode, starts an animated spinner.
        In non-TTY mode, emits a plain log line.

        Args:
            node_id: The identifier of the node that started.
        """
        if self._output_format == "json":
            return

        self._start_times[node_id] = time.time()

        if self._is_tty:
            spinner = _Spinner(f"Running node '{node_id}'", stream=self._stream)
            self._spinners[node_id] = spinner
            spinner.start()
        else:
            self._emit(f"[*] Running node '{node_id}'...")

    def on_node_complete(
        self, node_id: str, duration: float | None = None
    ) -> None:
        """Called when a node completes successfully.

        Stops the spinner (TTY) and emits a completion line.

        Args:
            node_id: The identifier of the completed node.
            duration: Wall-clock duration in seconds.  Computed from the
                recorded start time if not provided.
        """
        if self._output_format == "json":
            return

        self._stop_spinner(node_id)
        elapsed = duration if duration is not None else self._elapsed(node_id)
        self._emit(f"[OK] Node '{node_id}' completed ({elapsed:.1f}s)")

    def on_node_fail(self, node_id: str, error: str = "") -> None:
        """Called when a node fails.

        Stops the spinner (TTY) and emits an error line.

        Args:
            node_id: The identifier of the failed node.
            error: Short description of the failure.
        """
        if self._output_format == "json":
            return

        self._stop_spinner(node_id)
        msg = f"[FAIL] Node '{node_id}' failed"
        if error:
            msg += f": {error}"
        self._emit(msg)

    def on_node_skip(self, node_id: str) -> None:
        """Called when a node is skipped due to upstream failure.

        Args:
            node_id: The identifier of the skipped node.
        """
        if self._output_format == "json":
            return

        self._stop_spinner(node_id)
        self._emit(f"[SKIP] Node '{node_id}' skipped (upstream failure)")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def print_summary(self, record: CompositionRecord) -> None:
        """Print the execution summary after a composition finishes.

        In ``"text"`` mode, prints overall status, per-node table, and total
        wall-clock time to *stream*.  In ``"json"`` mode this method is a
        no-op (the caller is responsible for emitting the full record as JSON).

        Args:
            record: The completed :class:`~agentry.composition.record.CompositionRecord`.
        """
        if self._output_format == "json":
            return

        total = record.wall_clock_seconds
        status = record.overall_status.value

        self._emit("")
        self._emit(f"Composition {status} ({total:.2f}s total)")
        self._emit("")

        # Per-node status table.
        if record.node_statuses:
            self._emit(f"{'Node':<30} {'Status':<15} {'Duration':>10}")
            self._emit("-" * 57)
            for node_id, node_status in record.node_statuses.items():
                # Best-effort duration from start/end times.
                node_record = record.node_records.get(node_id)
                duration_str = "n/a"
                if node_id in self._start_times and node_record is not None:
                    # ExecutionRecord doesn't carry timing — placeholder for
                    # future per-node wall-clock tracking.
                    pass
                self._emit(
                    f"{node_id:<30} {node_status.value:<15} {duration_str:>10}"
                )
            self._emit("")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _emit(self, text: str) -> None:
        """Write a line to the output stream."""
        self._stream.write(text + "\n")
        self._stream.flush()

    def _stop_spinner(self, node_id: str) -> None:
        """Stop and remove the spinner for *node_id* if one is active."""
        spinner = self._spinners.pop(node_id, None)
        if spinner is not None:
            spinner.stop()

    def _elapsed(self, node_id: str) -> float:
        """Return elapsed seconds since the node start event was recorded."""
        start = self._start_times.get(node_id)
        if start is None:
            return 0.0
        return time.time() - start
