# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is Agentry

Agentry is a Python CLI tool for portable agentic workflow orchestration. It defines workflows in YAML, executes them in sandboxed environments (Docker or in-process), and integrates with CI platforms like GitHub Actions. Workflows compose Claude-powered agents with declarative safety constraints, tool manifests, and multi-agent DAG composition.

## Development Commands

```bash
# Install dependencies (use uv, not pip)
uv sync --all-extras

# Linting and formatting
uv run ruff check src/agentry/
uv run ruff format src/agentry/

# Type checking (strict mode enabled)
uv run mypy src/agentry/ --ignore-missing-imports

# Run all tests
uv run pytest tests/

# Run by marker
uv run pytest tests/ -m unit
uv run pytest tests/ -m integration
uv run pytest tests/ -m docker
uv run pytest tests/ -m e2e

# Run a single test file
uv run pytest tests/unit/test_parser.py -v

# Run a single test
uv run pytest tests/unit/test_parser.py::test_function_name -v
```

## Architecture

The system follows a 5-layer pipeline:

1. **Definition** (`src/agentry/models/`) — Pydantic v2 models parse and validate workflow YAML. `WorkflowDefinition` is the top-level model.
2. **Safety** (`src/agentry/security/`) — `SecurityEnvelope` enforces tool manifests, preflight checks, Ed25519 signing/verification, and audit diffing.
3. **Resolution** (`src/agentry/binders/`) — `EnvironmentBinder` protocol resolves inputs from the execution environment (local CLI or GitHub Actions).
4. **Execution** (`src/agentry/runners/`) — `RunnerProtocol` implementations run agents in Docker containers (sandboxed) or in-process (elevated trust).
5. **Agent** (`src/agentry/agents/`) — `AgentProtocol` (PEP 544) abstracts agent runtimes. `ClaudeCodeAgent` is the primary implementation.

**Composition engine** (`src/agentry/composition/`) orchestrates multi-agent DAGs with async execution, failure policies (abort/skip/retry), and file-based data passing between nodes.

**CLI entry point** is `src/agentry/cli.py` using Click. The `agentry` command is registered via `pyproject.toml` entry points.

**Binders are pluggable** via the `agentry.binders` entry point group. The `github-actions` binder ships built-in.

## Key Design Patterns

- All protocols use PEP 544 structural typing (`Protocol` classes), not ABC inheritance
- Pydantic v2 strict validation throughout the model layer
- Runners auto-detected via `detector.py` based on environment and trust level
- Network isolation uses a custom DNS proxy (`dns_proxy.py`) to enforce allowlists

## Code Quality

- **ruff**: line-length 100, target Python 3.10, rules: E/W/F/I/N/UP/B/C4/SIM (E501 ignored — formatter handles it)
- **mypy**: strict mode. Tests are exempted from strict typing.
- **pytest markers**: `unit`, `integration`, `docker`, `e2e` — always mark new tests appropriately

## Workflow Library

`workflows/` contains standard workflow YAML files (code-review, triage, bug-fix, task-decompose, feature-implement, planning-pipeline). These serve as both usable workflows and reference examples for the YAML schema.

## CI Self-Development Loop

Agentry develops itself via GitHub Actions workflows in `.github/workflows/`:

| CI Workflow | Trigger | Agentry Workflow |
|-------------|---------|-----------------|
| `agentry-planning-pipeline.yml` | `issues: [opened, reopened]` | `planning-pipeline.yaml` (triage → decompose → summarize) |
| `agentry-bug-fix.yml` | `issues: [labeled]` (`category:bug`) | `bug-fix.yaml` |
| `agentry-feature-implement.yml` | `issues: [labeled]` (`category:feature`) | `feature-implement.yaml` |
| `agentry-code-review.yml` | `pull_request` | `code-review.yaml` |

The loop: issue filed → planning-pipeline applies labels → label triggers bug-fix or feature-implement → code-review reviews the resulting PR. The GitHubActionsBinder provides `issue:comment`, `issue:label`, `issue:create`, and `pr:create` tool bindings.
