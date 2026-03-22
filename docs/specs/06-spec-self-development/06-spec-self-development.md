# 06-spec-self-development

## Introduction/Overview

Agentry's four-layer execution pipeline (Runner → Agent → Model) is fully built and tested, but the CLI's single-workflow `run` path emits a stub instead of invoking it. This spec wires the last mile — connecting the CLI to the real pipeline — then dogfoods the result by having Agentry review its own PRs in CI and propose fixes via agent-generated branches.

The end state: Agentry develops itself. PRs are reviewed by `agentry run workflows/code-review.yaml`, issues are triaged by `agentry run workflows/triage.yaml`, and the planning pipeline decomposes backlog items into tasks — all using the same tool that runs them.

## Goals

1. **Wire single-workflow execution** — Replace the JSON stub in `cli.py` with real RunnerDetector → Runner → Agent pipeline invocation, returning structured `ExecutionResult` output.
2. **Wire git-diff input resolution** — LocalBinder resolves `--input diff=HEAD~1` into actual git diff content, completing the input pipeline for code-review workflows.
3. **Dogfood in CI** — Generate and commit a GitHub Actions workflow that runs `agentry run workflows/code-review.yaml` on every PR to the agentry repo itself.
4. **Self-modification via PR** — The bug-fix workflow creates a branch and opens a PR with proposed changes; human reviews and merges.
5. **Prove all standard workflows execute** — Every workflow in `workflows/` runs end-to-end against the agentry repo and returns valid structured output.

## User Stories

- As a **developer**, I want `agentry run workflows/code-review.yaml --input diff=HEAD~1 --target .` to return real code review findings so that I can use Agentry for actual work.
- As a **maintainer**, I want PRs to the agentry repo automatically reviewed by its own code-review workflow so that every PR gets consistent agent-powered feedback.
- As a **contributor**, I want `agentry run workflows/bug-fix.yaml --input issue-description="..." --target .` to open a PR with a proposed fix so that the agent's work is auditable and reviewable before merge.
- As an **operator**, I want `agentry run workflows/triage.yaml --input issue-description="..." --target .` to return a structured classification so that I can prioritize work programmatically.

## Demoable Units of Work

### Unit 1: Wire Single-Workflow Execution

**Purpose:** Replace the stub in `cli.py` with real pipeline invocation so that `agentry run <workflow>` executes the agent and returns results.

**Functional Requirements:**
- The system shall replace the `"not_implemented"` stub in the single-workflow path of the `run` command with a call to `RunnerDetector.get_runner()` → `runner.provision()` → `runner.execute()` → `runner.teardown()`.
- The system shall pass `AgentConfig` constructed from the loaded workflow's agent block (runtime, model, system_prompt, max_iterations), resolved inputs, and tool declarations.
- The system shall emit `ExecutionResult` as JSON when `--output-format json` is specified, and a human-readable summary when `--output-format text` is used.
- The system shall respect `--skip-preflight` and `--target` flags in the execution path.
- The system shall handle agent execution errors (timeout, non-zero exit, missing binary) gracefully with informative error messages and appropriate exit codes.
- The system shall write an execution record to `.agentry/runs/<timestamp>/` for auditability.

**Proof Artifacts:**
- CLI: `agentry run workflows/triage.yaml --input issue-description="Login fails on Safari" --input repository-ref=. --target . --output-format json` returns JSON with `status`, `output`, and `token_usage` fields.
- CLI: `agentry run workflows/code-review.yaml --input diff="$(git diff HEAD~1)" --input codebase=. --target . --output-format json` returns JSON with `findings` array.
- File: `.agentry/runs/<timestamp>/execution-record.json` exists after a successful run.
- Test: Unit tests verify RunnerDetector integration, error handling, and output formatting for the single-workflow path.

### Unit 2: Wire Git-Diff Input Resolution

**Purpose:** Enable `--input diff=HEAD~1` to automatically resolve to the actual git diff content, removing the need to shell out manually.

**Functional Requirements:**
- The system shall detect when an input declared as `type: git-diff` in the workflow receives a git ref (e.g., `HEAD~1`, `main..feature`, a commit SHA) instead of raw diff text.
- The system shall resolve git refs to diff content by running `git diff <ref>` in the target directory.
- The system shall fall back to treating the value as raw diff text when it does not match a git ref pattern or when `git diff` fails.
- The system shall support `--target PATH` to specify the repository directory for diff resolution.
- The system shall raise a clear error when the target directory is not a git repository and the input type requires git operations.

**Proof Artifacts:**
- CLI: `agentry run workflows/code-review.yaml --input diff=HEAD~1 --input codebase=. --target .` resolves the diff automatically and returns findings.
- Test: Unit tests verify git ref detection, diff resolution, fallback to raw text, and error cases (non-git directory, invalid ref).

### Unit 3: Dogfood in CI — Self-Reviewing PRs

**Purpose:** Agentry reviews its own PRs using `agentry run` in GitHub Actions, proving the full loop works in CI.

**Functional Requirements:**
- The system shall include a GitHub Actions workflow file (`.github/workflows/agentry-code-review.yml`) that runs on `pull_request` events.
- The generated CI workflow shall invoke `agentry run workflows/code-review.yaml --input diff=${{ github.event.pull_request.head.sha }}~1 --input codebase=. --binder github-actions --output-format json`.
- The CI workflow shall post a PR comment with the agent's findings summary using the github-actions binder's `pr:comment` tool binding.
- The CI workflow shall require `ANTHROPIC_API_KEY` as a repository secret.
- The CI workflow shall use minimal GitHub token permissions derived from the code-review workflow's tool manifest.

**Proof Artifacts:**
- File: `.github/workflows/agentry-code-review.yml` exists and is valid YAML.
- CLI: `agentry ci generate --target github --dry-run workflows/code-review.yaml` produces output matching the committed workflow (or the committed file is a manually refined version of the generated output).
- URL: A PR to the agentry repo shows a bot comment with code review findings from the agentry workflow.

### Unit 4: Self-Modification — Agent-Generated PRs

**Purpose:** The bug-fix workflow can create a branch and open a PR with proposed changes, enabling agent-driven development with human review.

**Functional Requirements:**
- The system shall support a `pr:create` tool binding in the github-actions binder that creates a branch, commits changes, and opens a pull request.
- The system shall support a `pr:create` tool binding in the local binder that creates a branch, commits changes, and optionally opens a PR via `gh` CLI.
- The bug-fix workflow shall produce output containing the branch name and PR URL (or local branch name) when changes are proposed.
- The system shall enforce that agent-created PRs target a configurable base branch (default: `main`) and include a standard label (e.g., `agent-proposed`).
- The system shall NOT auto-merge any PRs — all agent-proposed changes require human review.

**Proof Artifacts:**
- CLI: `agentry run workflows/bug-fix.yaml --input issue-description="Fix typo in README" --target . --output-format json` returns JSON with `branch` and `pr_url` fields.
- URL: A PR opened by the agent against the agentry repo, labeled `agent-proposed`, with a descriptive title and body.
- Test: Integration test verifying `pr:create` tool binding creates a branch with commits in a temporary git repo.

## Non-Goals (Out of Scope)

- **Auto-merging PRs** — All agent changes require human review and approval.
- **Issue trigger in CI** — Only `pull_request` trigger for v1; issue-triggered triage is a future enhancement.
- **Scheduled pipeline runs** — Backlog grooming via scheduled `planning-pipeline` runs deferred.
- **Multi-provider agent support** — Only Claude Code agent runtime; Open Code, Aider, Ollama deferred.
- **Token budget enforcement** — No per-execution cost caps in this phase.
- **Streaming output** — Agent output is collected after completion, not streamed.

## Design Considerations

No specific UI/UX requirements beyond existing CLI conventions. Output formats (JSON and text) follow established patterns from `validate` and `setup` commands.

## Repository Standards

- Python 3.10+ with `from __future__ import annotations`
- Ruff for linting, mypy for type checking (both must pass clean)
- Tests in `tests/unit/`, `tests/integration/`, `tests/e2e/`
- Click-based CLI with `CliRunner` tests
- Commit messages: `type(scope): description`

## Technical Considerations

- **RunnerDetector** already exists and works — the CLI just needs to call it instead of emitting a stub.
- **InProcessRunner** handles `trust: elevated` workflows (no Docker needed). All standard workflows use `trust: elevated`, so Docker is not required for dogfooding.
- **ClaudeCodeAgent** requires `claude` binary on PATH and `ANTHROPIC_API_KEY` env var.
- **LocalBinder** input resolution for `git-diff` type needs implementation (currently deferred as T03.2).
- **SecurityEnvelope** wraps the runner — the CLI should use it for tool manifest enforcement and output validation, not bypass it.
- **Execution records** should be written by the same path composition uses, ensuring consistency.
- **CI dogfooding** requires `ANTHROPIC_API_KEY` as a GitHub repository secret — document this in CONTRIBUTING.md.

## Security Considerations

- Agent-created PRs must be labeled and reviewed by humans before merge.
- `ANTHROPIC_API_KEY` stored as GitHub Actions secret, never logged or exposed in output.
- The `pr:create` tool binding must not allow force-push or push to protected branches.
- Workflow signing should be verified before execution when signatures are present.
- Agent execution runs with `trust: elevated` (no Docker sandbox) for dogfooding — acceptable since the agent operates on its own repo.

## Success Metrics

- All 5 standard workflows (`code-review`, `triage`, `bug-fix`, `task-decompose`, `planning-pipeline`) execute end-to-end against the agentry repo and return valid structured output.
- PRs to the agentry repo receive automated code review comments from the agentry workflow within 5 minutes.
- `agentry run` exit code 0 on successful execution, non-zero on agent failure.
- Zero mypy errors, zero ruff errors, all existing tests continue to pass.
- At least one agent-generated PR demonstrates the self-modification loop.

## Open Questions

- Should the `pr:create` tool binding use the GitHub API directly or delegate to `gh` CLI? (GitHub API is more portable; `gh` CLI is simpler for local use. Recommendation: support both via binder abstraction.)
- What is the maximum acceptable latency for CI code review before it becomes annoying? (Likely 2-5 minutes — set agent timeout accordingly.)
- Should execution records include the full agent transcript or just structured output? (Full transcript is valuable for debugging but may contain sensitive content.)
