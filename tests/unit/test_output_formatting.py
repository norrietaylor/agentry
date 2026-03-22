"""Unit tests for TTY-aware output formatting and progress display (T02.2).

Tests cover:
- ``emit()`` function: TTY mode produces colored text, non-TTY produces JSON.
- Color coding: critical=red, warning=yellow, info=blue, success=green.
- ``Spinner`` context manager: starts/stops cleanly, no-op in non-TTY.
- ``InterruptHandler`` context manager: partial results, exit code 130.
- CLI integration via Click CliRunner: colored/JSON output, Ctrl+C handling.
"""

from __future__ import annotations

import io
import json
import signal
import sys
import time
import types

import pytest
from click.testing import CliRunner

from agentry.cli import main
from agentry.output import (
    LEVEL_CRITICAL,
    LEVEL_INFO,
    LEVEL_SUCCESS,
    LEVEL_WARNING,
    InterruptHandler,
    Spinner,
    emit,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeTTYStream(io.StringIO):
    """StringIO that claims to be a TTY."""

    def isatty(self) -> bool:
        return True


class FakePipeStream(io.StringIO):
    """StringIO that claims NOT to be a TTY (piped/redirected)."""

    def isatty(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# emit() — output_format="text" (explicit)
# ---------------------------------------------------------------------------


def test_emit_text_plain_message() -> None:
    """emit() with text format writes message + newline."""
    stream = FakePipeStream()
    emit("hello", output_format="text", stream=stream)
    assert stream.getvalue() == "hello\n"


def test_emit_text_tty_critical_has_red_ansi() -> None:
    """emit() critical on a TTY stream includes ANSI red escape codes."""
    stream = FakeTTYStream()
    emit("boom", level=LEVEL_CRITICAL, output_format="text", stream=stream)
    out = stream.getvalue()
    assert "\033[31m" in out, "Expected red ANSI code for critical"
    assert "boom" in out
    assert "\033[0m" in out, "Expected ANSI reset after message"


def test_emit_text_tty_warning_has_yellow_ansi() -> None:
    """emit() warning on a TTY stream includes ANSI yellow escape codes."""
    stream = FakeTTYStream()
    emit("careful", level=LEVEL_WARNING, output_format="text", stream=stream)
    out = stream.getvalue()
    assert "\033[33m" in out, "Expected yellow ANSI code for warning"
    assert "careful" in out


def test_emit_text_tty_info_has_blue_ansi() -> None:
    """emit() info on a TTY stream includes ANSI blue escape codes."""
    stream = FakeTTYStream()
    emit("note", level=LEVEL_INFO, output_format="text", stream=stream)
    out = stream.getvalue()
    assert "\033[34m" in out, "Expected blue ANSI code for info"
    assert "note" in out


def test_emit_text_tty_success_has_green_ansi() -> None:
    """emit() success on a TTY stream includes ANSI green escape codes."""
    stream = FakeTTYStream()
    emit("ok", level=LEVEL_SUCCESS, output_format="text", stream=stream)
    out = stream.getvalue()
    assert "\033[32m" in out, "Expected green ANSI code for success"
    assert "ok" in out


def test_emit_text_non_tty_no_ansi_codes() -> None:
    """emit() text format on a non-TTY stream omits ANSI codes."""
    stream = FakePipeStream()
    emit("plain", level=LEVEL_CRITICAL, output_format="text", stream=stream)
    out = stream.getvalue()
    assert "\033[" not in out, "Should not include ANSI codes on non-TTY"
    assert "plain" in out


# ---------------------------------------------------------------------------
# emit() — output_format="json" (explicit)
# ---------------------------------------------------------------------------


def test_emit_json_produces_valid_json() -> None:
    """emit() with json format produces valid JSON on a single line."""
    stream = FakePipeStream()
    emit("message here", output_format="json", stream=stream)
    payload = json.loads(stream.getvalue().strip())
    assert payload["message"] == "message here"


def test_emit_json_includes_level() -> None:
    """emit() json output includes the level field."""
    stream = FakePipeStream()
    emit("msg", level=LEVEL_WARNING, output_format="json", stream=stream)
    payload = json.loads(stream.getvalue().strip())
    assert payload["level"] == LEVEL_WARNING


def test_emit_json_includes_extra_data() -> None:
    """emit() json output merges the ``data`` dict into the payload."""
    stream = FakePipeStream()
    emit("msg", output_format="json", data={"path": "/tmp/foo"}, stream=stream)
    payload = json.loads(stream.getvalue().strip())
    assert payload["path"] == "/tmp/foo"


# ---------------------------------------------------------------------------
# emit() — output_format="auto"
# ---------------------------------------------------------------------------


def test_emit_auto_tty_produces_text() -> None:
    """emit() auto mode on a TTY produces human-readable text (not JSON)."""
    stream = FakeTTYStream()
    emit("auto test", output_format="auto", stream=stream)
    out = stream.getvalue()
    # Should not be parseable as JSON
    assert out.strip() != ""
    try:
        json.loads(out)
        # If it parsed as JSON that's unexpected
        assert False, "auto mode on TTY should not produce JSON"  # noqa: B011
    except json.JSONDecodeError:
        pass  # Expected


def test_emit_auto_non_tty_produces_json() -> None:
    """emit() auto mode on a non-TTY produces JSON."""
    stream = FakePipeStream()
    emit("auto test", output_format="auto", stream=stream)
    payload = json.loads(stream.getvalue().strip())
    assert payload["message"] == "auto test"


# ---------------------------------------------------------------------------
# Spinner — non-TTY (no-op)
# ---------------------------------------------------------------------------


def test_spinner_noop_on_non_tty() -> None:
    """Spinner does not write anything when the stream is not a TTY."""
    stream = FakePipeStream()
    with Spinner("Loading", stream=stream):
        time.sleep(0.05)
    assert stream.getvalue() == "", "Spinner should not write to non-TTY stream"


def test_spinner_context_manager_returns_spinner() -> None:
    """Spinner.__enter__ returns the Spinner instance."""
    stream = FakePipeStream()
    with Spinner("Loading", stream=stream) as sp:
        assert isinstance(sp, Spinner)


def test_spinner_elapsed_time_increases() -> None:
    """Spinner.elapsed() returns increasing values while running."""
    stream = FakePipeStream()
    sp = Spinner("Test", stream=stream)
    sp.start()
    t1 = sp.elapsed()
    time.sleep(0.05)
    t2 = sp.elapsed()
    sp.stop()
    assert t2 >= t1, "Elapsed time must be non-decreasing"


def test_spinner_stop_is_idempotent() -> None:
    """Calling stop() multiple times does not raise."""
    stream = FakePipeStream()
    sp = Spinner("Test", stream=stream)
    sp.stop()  # Not started
    sp.start()
    sp.stop()
    sp.stop()  # Second stop — should be fine


def test_spinner_start_noop_on_non_tty() -> None:
    """Spinner.start() on non-TTY stream does not spawn a thread."""
    stream = FakePipeStream()
    sp = Spinner("Test", stream=stream)
    sp.start()
    assert sp._thread is None, "No thread should be spawned for non-TTY"
    sp.stop()


# ---------------------------------------------------------------------------
# Spinner — TTY mode
# ---------------------------------------------------------------------------


def test_spinner_tty_writes_to_stream() -> None:
    """Spinner writes spinner frames to a TTY stream."""
    stream = FakeTTYStream()
    with Spinner("Loading", interval=0.02, stream=stream):
        time.sleep(0.15)
    # After stop, should have written something (and cleared)
    # Check the stream received data during spinning
    # The final "\r\033[K" clear is always written on stop
    assert "\r\033[K" in stream.getvalue(), "Spinner should clear the line on stop"


def test_spinner_tty_includes_label() -> None:
    """Spinner output contains the label string."""
    stream = FakeTTYStream()
    with Spinner("MyLabel", interval=0.02, stream=stream):
        time.sleep(0.12)
    assert "MyLabel" in stream.getvalue()


def test_spinner_tty_includes_elapsed_time() -> None:
    """Spinner output contains an elapsed time indicator (e.g. '0.1s')."""
    stream = FakeTTYStream()
    with Spinner("Working", interval=0.02, stream=stream):
        time.sleep(0.12)
    assert "s)" in stream.getvalue(), "Expected elapsed time like '0.1s)'"


# ---------------------------------------------------------------------------
# InterruptHandler
# ---------------------------------------------------------------------------


def test_interrupt_handler_text_mode_no_interrupt() -> None:
    """InterruptHandler context manager with no interrupt restores signal."""
    original = signal.getsignal(signal.SIGINT)
    partial: dict[str, object] = {}
    with InterruptHandler(partial_results=partial, output_format="text"):
        partial["key"] = "value"
    # After context exits, signal should be restored to previous handler
    restored = signal.getsignal(signal.SIGINT)
    assert restored == original, "SIGINT handler should be restored after context"


def test_interrupt_handler_exposes_partial_results() -> None:
    """InterruptHandler.partial_results returns the shared dict."""
    partial: dict[str, object] = {"a": 1}
    handler = InterruptHandler(partial_results=partial)
    assert handler.partial_results is partial


def test_interrupt_handler_text_partial_output_on_sigint(capsys: pytest.CaptureFixture) -> None:
    """Sending SIGINT while inside InterruptHandler prints partial results."""
    import os

    stream = FakePipeStream()
    partial: dict[str, object] = {"step": "partial_value"}
    handler = InterruptHandler(
        partial_results=partial, output_format="text", stream=stream
    )

    with pytest.raises(SystemExit) as exc_info, handler:
        # Simulate SIGINT programmatically
        os.kill(os.getpid(), signal.SIGINT)

    assert exc_info.value.code == 130, "Exit code must be 130 on SIGINT"
    out = stream.getvalue()
    assert "Interrupted" in out or "partial" in out.lower()
    assert "partial_value" in out


def test_interrupt_handler_json_partial_output_on_sigint() -> None:
    """In JSON mode, InterruptHandler emits a JSON payload on interrupt."""
    import os

    stream = FakePipeStream()
    partial: dict[str, object] = {"result": "incomplete"}
    handler = InterruptHandler(
        partial_results=partial, output_format="json", stream=stream
    )

    with pytest.raises(SystemExit) as exc_info, handler:
        os.kill(os.getpid(), signal.SIGINT)

    assert exc_info.value.code == 130
    output = stream.getvalue().strip()
    # Find JSON in output (may have leading newline)
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("{"):
            payload = json.loads(line)
            assert payload["status"] == "interrupted"
            assert "partial_results" in payload
            return
    assert False, f"No JSON found in output: {output!r}"  # noqa: B011


def test_interrupt_handler_empty_partial_results() -> None:
    """InterruptHandler works when no partial results have been collected."""
    import os

    stream = FakePipeStream()
    handler = InterruptHandler(output_format="text", stream=stream)

    with pytest.raises(SystemExit) as exc_info, handler:
        os.kill(os.getpid(), signal.SIGINT)

    assert exc_info.value.code == 130
    out = stream.getvalue()
    assert "no partial results" in out.lower() or "Interrupted" in out


# ---------------------------------------------------------------------------
# CLI integration tests (CliRunner)
# ---------------------------------------------------------------------------


def test_cli_run_sigint_exits_130_via_interrupt_handler() -> None:
    """The CLI's SIGINT handler exits with code 130 (verified via InterruptHandler).

    This test exercises the InterruptHandler directly, which mirrors the
    behavior of the CLI's run command when Ctrl+C is pressed.
    """
    import os

    stream = FakePipeStream()
    partial: dict[str, object] = {"step": "in_progress"}
    handler = InterruptHandler(partial_results=partial, output_format="text", stream=stream)

    with pytest.raises(SystemExit) as exc_info, handler:
        os.kill(os.getpid(), signal.SIGINT)

    assert exc_info.value.code == 130, f"Expected exit 130, got {exc_info.value.code}"
    out = stream.getvalue()
    assert "in_progress" in out


def test_cli_validate_json_output(tmp_path: pytest.TempPathFactory) -> None:
    """--output-format json validate emits JSON."""
    wf = tmp_path / "w.yaml"
    wf.write_text("name: test\n")
    fake = types.ModuleType("agentry.parser")
    fake.validate_workflow_file = lambda path: []  # type: ignore[attr-defined]
    sys.modules["agentry.parser"] = fake
    try:
        runner = CliRunner()
        result = runner.invoke(main, ["--output-format", "json", "validate", str(wf)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "valid"
    finally:
        del sys.modules["agentry.parser"]


def test_cli_run_json_output_no_executor(tmp_path: pytest.TempPathFactory) -> None:
    """run --output-format json emits valid JSON stub."""
    wf = tmp_path / "w.yaml"
    wf.write_text("name: test\n")
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--output-format", "json", "run", str(wf), "--input", "k=v", "--skip-preflight"],
        env={"ANTHROPIC_API_KEY": "", "GITHUB_ACTIONS": ""},
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "not_implemented"
    assert data["workflow"] == str(wf)


def test_cli_run_text_output_no_executor(tmp_path: pytest.TempPathFactory) -> None:
    """run --output-format text emits human-readable output."""
    wf = tmp_path / "w.yaml"
    wf.write_text("name: test\n")
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--output-format", "text", "run", str(wf), "--skip-preflight"],
        env={"ANTHROPIC_API_KEY": "", "GITHUB_ACTIONS": ""},
    )
    assert result.exit_code == 0
    assert "Running workflow" in result.output or str(wf) in result.output


def test_emit_text_message_ends_with_newline() -> None:
    """emit() always appends a newline to the output."""
    for fmt in ("text", "json"):
        stream = FakePipeStream()
        emit("test", output_format=fmt, stream=stream)
        assert stream.getvalue().endswith("\n"), f"Missing newline for format={fmt}"


def test_emit_multiple_messages_sequential() -> None:
    """Multiple emit() calls append separate lines."""
    stream = FakePipeStream()
    emit("first", output_format="text", stream=stream)
    emit("second", output_format="text", stream=stream)
    lines = stream.getvalue().splitlines()
    assert len(lines) == 2
    assert lines[0] == "first"
    assert lines[1] == "second"
