"""TTY-aware output formatting and progress display for Agentry CLI.

Provides:
- ``emit()`` — color-coded human-readable output in TTY mode; structured JSON
  when piped.
- ``Spinner`` — context manager that shows an animated spinner with elapsed
  time during LLM calls when running interactively.
- ``InterruptHandler`` — context manager that installs a SIGINT handler that
  prints partial results and exits with code 130.

Color coding (TTY mode only):
  critical  → red
  warning   → yellow
  info      → blue
  success   → green (default for plain messages)
"""

from __future__ import annotations

import itertools
import json
import signal
import sys
import threading
import time
from typing import Any

# ---------------------------------------------------------------------------
# Level constants
# ---------------------------------------------------------------------------

LEVEL_CRITICAL = "critical"
LEVEL_WARNING = "warning"
LEVEL_INFO = "info"
LEVEL_SUCCESS = "success"

# ANSI escape codes
_ANSI_RED = "\033[31m"
_ANSI_YELLOW = "\033[33m"
_ANSI_BLUE = "\033[34m"
_ANSI_GREEN = "\033[32m"
_ANSI_RESET = "\033[0m"

_LEVEL_COLORS: dict[str, str] = {
    LEVEL_CRITICAL: _ANSI_RED,
    LEVEL_WARNING: _ANSI_YELLOW,
    LEVEL_INFO: _ANSI_BLUE,
    LEVEL_SUCCESS: _ANSI_GREEN,
}


def _supports_color(stream: Any = None) -> bool:
    """Return True if the given stream supports ANSI color codes.

    Falls back to ``sys.stdout`` if no stream is provided.
    """
    if stream is None:
        stream = sys.stdout
    return hasattr(stream, "isatty") and stream.isatty()


def emit(
    message: str,
    level: str = LEVEL_SUCCESS,
    *,
    output_format: str = "auto",
    data: dict[str, Any] | None = None,
    stream: Any = None,
) -> None:
    """Write a message to *stream* (default: stdout) using the given format.

    Parameters
    ----------
    message:
        Human-readable message to display.
    level:
        Severity level — one of ``critical``, ``warning``, ``info``, or
        ``success``.  Controls color in TTY mode.
    output_format:
        ``"auto"`` (default) — use TTY detection to pick text/JSON.
        ``"json"`` — always emit structured JSON.
        ``"text"`` — always emit colored/plain text.
    data:
        Optional extra fields to include in JSON output.
    stream:
        Output stream.  Defaults to ``sys.stdout``.
    """
    if stream is None:
        stream = sys.stdout

    if output_format == "auto":
        effective = "text" if _supports_color(stream) else "json"
    else:
        effective = output_format

    if effective == "json":
        payload: dict[str, Any] = {"level": level, "message": message}
        if data:
            payload.update(data)
        stream.write(json.dumps(payload) + "\n")
        stream.flush()
    else:
        # Text / TTY mode
        is_tty = _supports_color(stream)
        if is_tty and level in _LEVEL_COLORS:
            color = _LEVEL_COLORS[level]
            stream.write(f"{color}{message}{_ANSI_RESET}\n")
        else:
            stream.write(message + "\n")
        stream.flush()


# ---------------------------------------------------------------------------
# Spinner
# ---------------------------------------------------------------------------

_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
# Fallback for environments that cannot render Braille
_SPINNER_FRAMES_ASCII = ["-", "\\", "|", "/"]


class Spinner:
    """Context manager that shows an animated spinner on stderr while work runs.

    Only activates when stderr is a TTY.  Safe to use in non-TTY environments —
    it simply becomes a no-op.

    Usage::

        with Spinner("Calling LLM"):
            result = llm_client.call(...)

    The spinner shows the label and elapsed time, e.g.::

        ⠋ Calling LLM (1.2s)
    """

    def __init__(
        self,
        label: str = "Working",
        *,
        interval: float = 0.1,
        stream: Any = None,
    ) -> None:
        self.label = label
        self.interval = interval
        self._stream = stream if stream is not None else sys.stderr
        self._active = False
        self._thread: threading.Thread | None = None
        self._start_time: float = 0.0
        self._is_tty = hasattr(self._stream, "isatty") and self._stream.isatty()

        # Choose frame set based on encoding support
        try:
            "⠋".encode(self._stream.encoding or "utf-8")
            self._frames = _SPINNER_FRAMES
        except (UnicodeEncodeError, AttributeError):
            self._frames = _SPINNER_FRAMES_ASCII

    # ------------------------------------------------------------------
    # Context manager protocol
    # ------------------------------------------------------------------

    def __enter__(self) -> Spinner:
        self.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the spinner in a background thread."""
        if not self._is_tty:
            return
        self._active = True
        self._start_time = time.monotonic()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the spinner and clear the line."""
        if not self._is_tty:
            return
        self._active = False
        if self._thread is not None:
            self._thread.join(timeout=self.interval * 3)
            self._thread = None
        # Clear the spinner line
        self._stream.write("\r\033[K")
        self._stream.flush()

    def elapsed(self) -> float:
        """Return elapsed seconds since the spinner was started."""
        if self._start_time == 0.0:
            return 0.0
        return time.monotonic() - self._start_time

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _spin(self) -> None:
        for frame in itertools.cycle(self._frames):
            if not self._active:
                break
            elapsed = time.monotonic() - self._start_time
            line = f"\r{frame} {self.label} ({elapsed:.1f}s)"
            self._stream.write(line)
            self._stream.flush()
            time.sleep(self.interval)


# ---------------------------------------------------------------------------
# Interrupt handler
# ---------------------------------------------------------------------------


class InterruptHandler:
    """Context manager that handles SIGINT with a graceful summary exit.

    When Ctrl+C is pressed:
    1. Prints any partial results collected so far.
    2. Exits with code 130.

    Usage::

        partial: dict[str, object] = {}
        with InterruptHandler(partial_results=partial):
            partial["step1"] = run_step1()
            partial["step2"] = run_step2()
    """

    def __init__(
        self,
        partial_results: dict[str, Any] | None = None,
        *,
        output_format: str = "text",
        stream: Any = None,
    ) -> None:
        self._partial = partial_results if partial_results is not None else {}
        self._output_format = output_format
        self._stream = stream if stream is not None else sys.stdout
        self._previous_handler: Any = signal.SIG_DFL

    def __enter__(self) -> InterruptHandler:
        self._previous_handler = signal.signal(signal.SIGINT, self._handle)
        return self

    def __exit__(self, *args: Any) -> None:
        signal.signal(signal.SIGINT, self._previous_handler)

    @property
    def partial_results(self) -> dict[str, Any]:
        """Live reference to the partial results dict."""
        return self._partial

    def _handle(self, signum: int, frame: Any) -> None:  # noqa: ARG002
        if self._output_format == "json":
            payload: dict[str, Any] = {
                "status": "interrupted",
                "partial_results": self._partial,
            }
            self._stream.write("\n" + json.dumps(payload) + "\n")
        else:
            self._stream.write("\nInterrupted. Partial results:\n")
            if self._partial:
                for k, v in self._partial.items():
                    self._stream.write(f"  {k}: {v}\n")
            else:
                self._stream.write("  (no partial results collected)\n")
        self._stream.flush()
        sys.exit(130)
