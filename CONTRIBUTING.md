# Contributing to Agentry

Thank you for your interest in contributing to Agentry. This guide covers the essentials for getting started.

## Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (package manager)
- Docker (optional, required for container-based runner tests)
- Claude Code CLI (for agent development workflows)

## Setup

```bash
git clone https://github.com/<org>/agentry.git
cd agentry
uv sync --all-extras
```

## Code Style

- **Linting**: ruff. Run `uv run ruff check .` and `uv run ruff format .` before committing.
- **Type checking**: mypy. Run `uv run mypy .` to verify type annotations.
- **Protocols**: Follow PEP-544 structural subtyping. Prefer `Protocol` classes over abstract base classes for interface definitions.

## Testing

Run the full test suite:

```bash
uv run pytest tests/
```

### Markers

- `@pytest.mark.unit` -- fast, isolated unit tests (no external dependencies).
- `@pytest.mark.docker` -- tests that require a running Docker daemon.

Run a specific subset:

```bash
uv run pytest tests/ -m unit
uv run pytest tests/ -m docker
```

All new features must include tests. Aim for unit-level coverage at minimum.

## Commit Conventions

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(runners): add timeout support for DockerRunner
fix(safety): correct envelope validation for nested policies
docs: update architecture diagram
chore: bump ruff to 0.5.x
refactor(resolution): simplify provider lookup logic
test(agents): add integration tests for agent lifecycle
```

- Use the imperative mood in the subject line ("add", not "added" or "adds").
- Keep the subject line under 72 characters.
- Reference related issues where applicable (e.g., `Closes #42`).

## Pull Request Process

1. Branch from `main`. Use a descriptive branch name (e.g., `feat/docker-timeout`, `fix/envelope-validation`).
2. Make focused, incremental commits.
3. Ensure CI passes -- linting, type checking, and all tests must be green.
4. Include tests for any new functionality.
5. Provide a clear PR description explaining what changed and why.
6. Request review from at least one maintainer.

## Architecture Overview

Agentry follows a **5-layer pipeline model**. Understanding this layering helps you place contributions in the right location.

```
Definition --> Safety --> Resolution --> Execution --> Agent
```

| Layer        | Responsibility                                      |
|--------------|-----------------------------------------------------|
| Definition   | Declarative task and environment specifications      |
| Safety       | Security envelopes, policy enforcement, validation   |
| Resolution   | Provider resolution, dependency injection, binding   |
| Execution    | Runner lifecycle, container management, orchestration|
| Agent        | Agent runtime, tool use, reasoning loop              |

### Key Protocols

- **RunnerProtocol** -- defines how execution environments are started, managed, and torn down.
- **AgentProtocol** -- defines the interface for agent implementations (tool dispatch, message handling, lifecycle).
- **EnvironmentBinder** -- binds resolved dependencies and configuration into an execution context.

These are PEP-544 `Protocol` classes. You do not need to inherit from them; structural compatibility is sufficient.

## License

Agentry is licensed under the [Apache License 2.0](LICENSE). By submitting a contribution, you agree that your work will be licensed under the same terms.
