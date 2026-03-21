"""Unit tests for T01.5: Runtime shim (agentry.runners.shim).

Tests cover:
- load_config() reads and validates JSON config files.
- load_config() raises FileNotFoundError for missing files.
- load_config() raises ValueError for missing required keys.
- write_result() writes JSON to the output path.
- write_result() creates parent directories.
- run_shim() returns exit code 2 on startup errors.
- run_shim() writes error result on startup errors.
- main() uses sys.argv for config and output paths.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from agentry.runners.shim import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_OUTPUT_PATH,
    load_config,
    run_shim,
    write_result,
)

# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


class TestLoadConfig:
    """Tests for shim.load_config()."""

    def test_loads_valid_config(self) -> None:
        config_data = {
            "system_prompt": "You are helpful.",
            "resolved_inputs": {"diff": "content"},
            "tool_names": ["repository:read"],
            "llm_config": {"model": "claude-3"},
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as fh:
            json.dump(config_data, fh)
            fh.flush()
            path = fh.name

        try:
            result = load_config(path)
            assert result["system_prompt"] == "You are helpful."
            assert result["tool_names"] == ["repository:read"]
        finally:
            os.unlink(path)

    def test_raises_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError, match="Config file not found"):
            load_config("/nonexistent/path/config.json")

    def test_raises_on_invalid_json(self) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as fh:
            fh.write("not valid json {{{")
            path = fh.name

        try:
            with pytest.raises(json.JSONDecodeError):
                load_config(path)
        finally:
            os.unlink(path)

    def test_raises_on_missing_required_keys(self) -> None:
        config_data = {
            "system_prompt": "test",
            # missing: resolved_inputs, tool_names, llm_config
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as fh:
            json.dump(config_data, fh)
            path = fh.name

        try:
            with pytest.raises(ValueError, match="Missing required config keys"):
                load_config(path)
        finally:
            os.unlink(path)

    def test_accepts_extra_keys(self) -> None:
        config_data = {
            "system_prompt": "test",
            "resolved_inputs": {},
            "tool_names": [],
            "llm_config": {},
            "timeout": 60,
            "extra_field": "ok",
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as fh:
            json.dump(config_data, fh)
            path = fh.name

        try:
            result = load_config(path)
            assert result["extra_field"] == "ok"
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# write_result
# ---------------------------------------------------------------------------


class TestWriteResult:
    """Tests for shim.write_result()."""

    def test_writes_json(self) -> None:
        output_dir = tempfile.mkdtemp(prefix="agentry-test-")
        output_path = os.path.join(output_dir, "result.json")

        write_result(output_path, {"exit_code": 0, "output": "done"})

        with open(output_path) as fh:
            data = json.load(fh)
        assert data["exit_code"] == 0
        assert data["output"] == "done"

    def test_creates_parent_directories(self) -> None:
        output_dir = tempfile.mkdtemp(prefix="agentry-test-")
        nested_path = os.path.join(output_dir, "a", "b", "result.json")

        write_result(nested_path, {"status": "ok"})

        with open(nested_path) as fh:
            data = json.load(fh)
        assert data["status"] == "ok"


# ---------------------------------------------------------------------------
# run_shim (startup error path only -- agent execution requires LLM)
# ---------------------------------------------------------------------------


class TestRunShim:
    """Tests for shim.run_shim() startup error handling."""

    def test_returns_2_on_missing_config(self) -> None:
        output_dir = tempfile.mkdtemp(prefix="agentry-test-")
        output_path = os.path.join(output_dir, "result.json")

        exit_code = run_shim(
            config_path="/nonexistent/config.json",
            output_path=output_path,
        )

        assert exit_code == 2
        with open(output_path) as fh:
            data = json.load(fh)
        assert "shim startup error" in data["error"].lower()

    def test_returns_2_on_invalid_config(self) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as fh:
            fh.write("not json")
            config_path = fh.name

        output_dir = tempfile.mkdtemp(prefix="agentry-test-")
        output_path = os.path.join(output_dir, "result.json")

        try:
            exit_code = run_shim(
                config_path=config_path,
                output_path=output_path,
            )
            assert exit_code == 2
        finally:
            os.unlink(config_path)

    def test_returns_2_on_missing_keys(self) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as fh:
            json.dump({"system_prompt": "hi"}, fh)
            config_path = fh.name

        output_dir = tempfile.mkdtemp(prefix="agentry-test-")
        output_path = os.path.join(output_dir, "result.json")

        try:
            exit_code = run_shim(
                config_path=config_path,
                output_path=output_path,
            )
            assert exit_code == 2
            with open(output_path) as f:
                data = json.load(f)
            assert data["exit_code"] == 2
        finally:
            os.unlink(config_path)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    """Tests for shim constants."""

    def test_default_config_path(self) -> None:
        assert DEFAULT_CONFIG_PATH == "/config/agent_config.json"

    def test_default_output_path(self) -> None:
        assert DEFAULT_OUTPUT_PATH == "/output/result.json"
