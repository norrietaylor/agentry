# 04-spec-agentry-ci

## Introduction/Overview

Phase 4 adds the GitHub Actions environment binder and CI generation command to Agentry. It implements the `GitHubActionsBinder` class conforming to the `EnvironmentBinder` protocol, translating abstract workflow definitions into concrete GitHub Actions execution — resolving inputs from event payloads, binding tools to the GitHub API, mapping outputs to PR comments, and generating `.github/workflows/` YAML from workflow definitions via `agentry ci generate`. This completes the local↔CI symmetry: the same workflow definition that runs locally via `agentry run` can now generate a GitHub Actions pipeline that runs identically in CI.

## Goals

1. **Implement GitHubActionsBinder**: A full `EnvironmentBinder` implementation that resolves inputs from GitHub event payloads, binds tools to the GitHub API (read and write), maps outputs to PR comments, and generates GitHub Actions YAML.
2. **Add `agentry ci generate` command**: CLI command that reads a workflow definition and produces a ready-to-commit `.github/workflows/` YAML file with configurable triggers (`pull_request`, `push`, `schedule`, `issues`).
3. **GitHub event payload resolution**: Map abstract input types to GitHub-specific sources — `repository-ref` → `$GITHUB_WORKSPACE`, `git-diff` → PR diff via GitHub API, `string` → workflow inputs or event payload fields.
4. **Write-side tool binding**: Bind `pr:comment` and `pr:review` tools to the GitHub API, with token scope verification as a preflight check.
5. **Token scope verification**: Preflight check that verifies `GITHUB_TOKEN` permissions match the workflow's declared tool manifest before execution starts.

## User Stories

- As a **developer**, I want to run `agentry ci generate --target github workflows/code-review.yaml` and get a `.github/workflows/agentry-code-review.yaml` file that I can commit directly, so that my local workflow runs in CI without manual YAML authoring.
- As a **developer**, I want the generated GitHub Actions workflow to resolve my workflow's `git-diff` input from the PR event payload automatically, so that the agent receives the same data in CI as it does locally.
- As a **workflow author**, I want my agent's `pr:comment` tool to post results as PR comments in CI, so that code review findings are visible to the team without manual intervention.
- As a **developer**, I want the CI runtime to verify that `GITHUB_TOKEN` has the scopes my workflow needs before the agent starts, so that I get a clear error instead of a cryptic API failure mid-execution.
- As a **workflow author**, I want to configure triggers (`pull_request`, `push`, `schedule`, `issues`) when generating CI config, so that my workflow runs on the appropriate GitHub events.

## Demoable Units of Work

### Unit 1: GitHubActionsBinder — Input Resolution

**Purpose:** Implement `resolve_inputs()` for the GitHub Actions binder, translating abstract input types to GitHub-specific sources using event payloads and runner environment variables.

**Functional Requirements:**
- The system shall implement `GitHubActionsBinder` in `src/agentry/binders/github_actions.py` conforming to the `EnvironmentBinder` protocol.
- The system shall resolve `repository-ref` inputs to `$GITHUB_WORKSPACE` (the checkout path on the GitHub Actions runner).
- The system shall resolve `git-diff` inputs by fetching the PR diff from the GitHub API (`GET /repos/{owner}/{repo}/pulls/{number}` with `Accept: application/vnd.github.diff`). The PR number is extracted from the `GITHUB_EVENT_PATH` JSON payload.
- The system shall resolve `string` inputs from GitHub Actions workflow `inputs` (for `workflow_dispatch`) or from event payload fields via a configurable mapping (e.g. `issue.title`, `issue.body`).
- The system shall raise a clear error when a required input cannot be resolved from the available event context (e.g., `git-diff` on a non-PR event).
- The system shall read `GITHUB_EVENT_NAME`, `GITHUB_EVENT_PATH`, `GITHUB_WORKSPACE`, `GITHUB_REPOSITORY`, and `GITHUB_TOKEN` from environment variables. Missing required variables shall produce actionable error messages.
- The system shall register in the binder registry under the name `"github-actions"` via the `agentry.binders` entry point group.

**Proof Artifacts:**
- Test: `tests/unit/test_github_binder_inputs.py` passes — demonstrates input resolution for `repository-ref`, `git-diff`, and `string` types with mocked environment variables and event payloads.
- Test: Error cases: missing `GITHUB_TOKEN`, non-PR event for `git-diff`, missing required input.
- File: `src/agentry/binders/github_actions.py` exists and satisfies `isinstance(GitHubActionsBinder(), EnvironmentBinder)`.

---

### Unit 2: GitHubActionsBinder — Tool Binding & Output Mapping

**Purpose:** Implement `bind_tools()` and `map_outputs()` for the GitHub Actions binder, binding abstract tool capabilities to GitHub API implementations and mapping outputs to PR comments.

**Functional Requirements:**
- The system shall bind `repository:read` to a callable that reads files from `$GITHUB_WORKSPACE` with path traversal protection (same logic as LocalBinder but rooted at `$GITHUB_WORKSPACE`).
- The system shall bind `shell:execute` to a callable that enforces the same read-only command allowlist as the LocalBinder.
- The system shall bind `pr:comment` to a callable that posts a comment to the current PR via the GitHub API (`POST /repos/{owner}/{repo}/issues/{number}/comments`). The PR number is extracted from the event payload.
- The system shall bind `pr:review` to a callable that creates a review on the current PR via the GitHub API (`POST /repos/{owner}/{repo}/pulls/{number}/reviews`).
- The system shall raise `UnsupportedToolError` for tool names not in the supported set: `{"repository:read", "shell:execute", "pr:comment", "pr:review"}`.
- The system shall implement `map_outputs()` to write agent output to `$GITHUB_WORKSPACE/.agentry/runs/<run_id>/output.json` and, when the event is a PR, post the output as a PR comment via the GitHub API.
- The system shall handle GitHub API errors with structured error messages including the HTTP status, response body, and suggested remediation (e.g., "403 Forbidden: GITHUB_TOKEN may lack `pull_requests:write` scope").

**Proof Artifacts:**
- Test: `tests/unit/test_github_binder_tools.py` passes — demonstrates tool binding for all four tools, unsupported tool rejection, and PR comment posting with mocked GitHub API.
- Test: `tests/unit/test_github_binder_outputs.py` passes — demonstrates output mapping to file and PR comment.
- Test: API error handling: 403 scope error, 404 PR not found, network timeout.

---

### Unit 3: GitHub Token Scope Verification

**Purpose:** Implement a preflight check that verifies `GITHUB_TOKEN` has the permissions required by the workflow's declared tool manifest, failing fast before agent execution.

**Functional Requirements:**
- The system shall implement `GitHubTokenScopeCheck` in `src/agentry/security/checks.py` conforming to the existing `PreflightCheck` protocol.
- The system shall map tool declarations to required GitHub token scopes: `repository:read` → `contents:read`, `pr:comment` → `pull_requests:write` or `issues:write`, `pr:review` → `pull_requests:write`.
- The system shall verify token scopes by making a test API call (`GET /repos/{owner}/{repo}`) and inspecting the response headers (`X-OAuth-Scopes` for OAuth tokens) or by attempting a minimal operation that requires the needed scope.
- The system shall produce a structured `PreflightResult` with: check name, pass/fail status, and on failure a message identifying which scopes are missing and which tools require them, plus remediation guidance (e.g., "Add `permissions: pull-requests: write` to your GitHub Actions workflow YAML").
- The system shall skip scope verification when `GITHUB_TOKEN` is not set (the `AnthropicAPIKeyCheck` pattern — check is only relevant in CI context).
- The system shall be added to the preflight check list when the binder is `github-actions`.

**Proof Artifacts:**
- Test: `tests/unit/test_github_token_check.py` passes — demonstrates scope verification pass, scope verification fail with remediation message, and skip-when-not-in-CI behavior.
- File: `GitHubTokenScopeCheck` is registered in `src/agentry/security/checks.py`.

---

### Unit 4: `agentry ci generate` Command

**Purpose:** Implement the `agentry ci generate` CLI command that reads a workflow definition and produces a GitHub Actions YAML file with configurable triggers.

**Functional Requirements:**
- The system shall replace the `ci` stub command with `agentry ci generate` (a subcommand group: `agentry ci generate --target github WORKFLOW_PATH`).
- The system shall accept `--target github` (required, only supported value in Phase 4), `--triggers` (comma-separated list from `pull_request,push,schedule,issues`, default `pull_request`), `--schedule` (cron expression, required when `schedule` is in triggers), and `--output-dir` (default `.github/workflows/`).
- The system shall load the workflow definition, validate it, and call `GitHubActionsBinder.generate_pipeline_config()` to produce the pipeline configuration dict.
- The system shall render the pipeline config as a GitHub Actions YAML file using template-based generation (Jinja2 or string templates). The generated YAML shall include: `name` (derived from workflow identity), `on` (trigger configuration), `permissions` (derived from tool manifest), `jobs.agentry.runs-on` (default `ubuntu-latest`), `jobs.agentry.steps` (checkout, setup-python, install agentry, run agentry).
- The system shall generate the `run` step as: `agentry run <workflow_path> --input <resolved_inputs>` with environment variables for `ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}` and `GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}`.
- The system shall derive minimal `permissions` from the tool manifest: `contents: read` (always), `pull-requests: write` (when `pr:comment` or `pr:review` is declared), `issues: write` (when `issue:create` or `issue:comment` is declared).
- The system shall write the generated YAML to `<output_dir>/agentry-<workflow-name>.yaml` and report the path.
- The system shall support `--dry-run` flag that prints the generated YAML to stdout without writing to disk.
- The system shall validate that the workflow does not have a non-empty `composition.steps` block and emit an error: "Composed workflow CI generation is not yet supported. Generate CI config for each component workflow individually."

**Proof Artifacts:**
- Test: `tests/unit/test_ci_generate.py` passes — demonstrates YAML generation for each trigger type, permission derivation, dry-run output, and composed workflow rejection.
- CLI: `agentry ci generate --target github workflows/code-review.yaml` produces a valid `.github/workflows/agentry-code-review.yaml`.
- CLI: `agentry ci generate --target github --triggers pull_request,schedule --schedule "0 2 * * 1" workflows/code-review.yaml` produces YAML with both triggers.
- CLI: `agentry ci generate --target github --dry-run workflows/code-review.yaml` prints YAML to stdout.
- File: Generated YAML passes `actionlint` (if available) or manual schema validation.

---

### Unit 5: CI Runtime Shim & End-to-End Integration

**Purpose:** Implement the thin runtime shim that runs inside a GitHub Actions job, handling binder selection and environment detection, and validate the full local→CI generation→execution path.

**Functional Requirements:**
- The system shall auto-detect the GitHub Actions environment when `GITHUB_ACTIONS=true` is set and automatically select the `github-actions` binder (instead of the default `local` binder) in `agentry run`.
- The system shall modify `agentry run` to accept `--binder <name>` flag as an override for explicit binder selection (e.g., `--binder github-actions` or `--binder local`). When not specified, binder is auto-detected: `github-actions` if `GITHUB_ACTIONS=true`, `local` otherwise.
- The system shall wire the `GitHubTokenScopeCheck` into the preflight check list when the active binder is `github-actions`.
- The system shall implement `GitHubActionsBinder.generate_pipeline_config()` returning a structured dict with: `name`, `on` (triggers), `permissions`, `env`, `jobs` (steps list). This dict is consumed by the template renderer in Unit 4.
- The system shall include an integration test that: (a) generates a GitHub Actions YAML from a standard library workflow, (b) validates the YAML structure, (c) verifies that the generated `run` step would invoke `agentry run` with the correct arguments.
- The system shall update the binder registry entry point in `pyproject.toml` to register `github-actions = agentry.binders.github_actions:GitHubActionsBinder`.

**Proof Artifacts:**
- Test: `tests/unit/test_ci_runtime.py` passes — demonstrates auto-detection of GitHub Actions environment, binder selection override, and preflight check wiring.
- Test: `tests/integration/test_ci_generate_e2e.py` passes — demonstrates end-to-end generation from `workflows/code-review.yaml` to valid GitHub Actions YAML.
- File: `pyproject.toml` contains the `github-actions` entry point in `[project.entry-points."agentry.binders"]`.

---

## Non-Goals (Out of Scope for Phase 4)

- **Check annotations** — Mapping structured findings to inline GitHub check annotations on changed files. Deferred to a follow-up.
- **Artifact uploads** — Uploading execution records or outputs as CI artifacts. Deferred.
- **Composed workflow CI generation** — Multi-job pipelines from composition DAGs. Deferred.
- **GitLab/Jenkins binders** — Only GitHub Actions is supported in Phase 4.
- **Custom runner images** — Generated YAML uses `ubuntu-latest`. Custom runner selection is a follow-up.
- **GitHub App authentication** — Only `GITHUB_TOKEN` (automatic token) is supported. GitHub App installation tokens are a follow-up.
- **Workflow dispatch inputs UI** — `workflow_dispatch` trigger with custom input parameters in the GitHub UI. Deferred.
- **Issue-tracker tools** — `issue:create`, `issue:comment`, `issue:label` tool bindings. Deferred to a follow-up (backlog item).

## Design Considerations

- **Binder as compiler backend**: The PRD's analogy — workflow spec is source code, binder is the compiler backend. The GitHub Actions binder "compiles" an abstract workflow into a concrete GitHub Actions pipeline. The generated YAML is a build artifact, not a source file.
- **Template-based generation**: The PRD specifies template-based YAML output. This is simpler and more debuggable than API-driven pipeline creation. The generated YAML is human-readable and can be manually adjusted.
- **Minimal permissions**: Generated YAML declares only the permissions the workflow actually needs (derived from tool manifest). This follows GitHub's recommendation to use least-privilege token scopes.
- **Auto-detection over configuration**: The runtime shim auto-detects the GitHub Actions environment via `GITHUB_ACTIONS=true` rather than requiring explicit binder selection. Explicit `--binder` flag exists as an override for testing and edge cases.

## Repository Standards

- Follows Phase 1/2/3 conventions: src layout, Pydantic v2 models, Click CLI, pytest, ruff, mypy strict
- New file: `src/agentry/binders/github_actions.py`
- New preflight check: `GitHubTokenScopeCheck` in `src/agentry/security/checks.py`
- CLI extension: `ci` command group replacing the stub
- Entry point registration in `pyproject.toml`
- Template files (if Jinja2): `src/agentry/binders/templates/github_actions.yaml.j2`

## Technical Considerations

- **GitHub API client**: Use `httpx` (or `urllib3`) for GitHub API calls rather than adding `PyGithub` as a dependency. The API surface is small (PR comments, diff fetch, scope check) — a thin wrapper is preferable to a heavy SDK dependency.
- **Event payload parsing**: `GITHUB_EVENT_PATH` points to a JSON file containing the full event payload. Parse with `json.load()`. The payload structure varies by event type — the binder must handle `pull_request`, `push`, `schedule`, and `issues` event schemas.
- **Token scope detection**: GitHub Actions' automatic `GITHUB_TOKEN` uses fine-grained permissions, not OAuth scopes. The `X-OAuth-Scopes` header is only present for OAuth/PAT tokens. For the automatic token, scope verification should attempt the required API call and check for 403 responses, or inspect the `permissions` key in the event payload.
- **Template rendering**: Use Jinja2 for YAML template rendering if it's already a dependency; otherwise use Python string templates (`string.Template` or f-strings with a dict). The generated YAML must be valid YAML — use `yaml.dump()` for the structured portions to avoid quoting issues.
- **Binder registry**: The `github-actions` binder registers via `importlib.metadata` entry points in the `agentry.binders` group, consistent with the existing `local` binder registration pattern.
- **Runner↔Binder relationship**: In CI, the runner is the GitHub Actions runner itself (not DockerRunner or InProcessRunner). The binder generates the pipeline config; the CI platform provisions the runner. The `agentry run` command inside the CI job uses `InProcessRunner` (trust: elevated) since the GitHub Actions runner is the isolation boundary.

## Security Considerations

- **Token scope least-privilege**: Generated YAML declares minimal permissions. `pr:comment` requires `pull-requests: write`, not admin access.
- **Secret injection**: `ANTHROPIC_API_KEY` is injected via `${{ secrets.ANTHROPIC_API_KEY }}` — never hardcoded in generated YAML.
- **Fork PR restrictions**: `pull_request` events from forks have restricted `GITHUB_TOKEN` permissions by default. The preflight check must detect this and provide clear guidance.
- **Event payload trust**: Event payloads from `pull_request` events with fork sources contain attacker-controlled data (PR title, body, branch name). The binder must not use these fields in shell commands or as unescaped template variables.
- **Generated YAML review**: The generated YAML is a commit artifact — developers review it before merging. It should not contain secrets or environment-specific paths.

## Success Metrics

- `agentry ci generate --target github workflows/code-review.yaml` produces a valid, committable GitHub Actions YAML file.
- The generated YAML, when executed on a GitHub Actions runner, successfully runs the workflow against a PR and posts results as a PR comment.
- Token scope preflight check catches insufficient permissions before agent execution and provides actionable remediation.
- `agentry run` auto-detects the GitHub Actions environment and selects the correct binder without explicit configuration.
- All existing Phase 1, 2, and 3 tests continue to pass (no regressions).
- The `github-actions` binder is discoverable via `agentry.binders` entry points.

## Open Questions

1. **Jinja2 dependency**: Should we add Jinja2 as a dependency for template rendering, or use stdlib `string.Template`? *Recommendation: use Jinja2 if it's already in the dependency tree (via another package); otherwise use a simple Python-based template approach to minimize dependencies.*
2. **GitHub API rate limiting**: Should the binder handle rate limiting for API calls (diff fetch, comment posting)? *Recommendation: implement basic retry-with-backoff for 429 responses. Full rate limit management is a follow-up.*
3. **Runner OS selection**: Should `ci generate` support `--runs-on` to specify the runner OS? *Recommendation: defer. Default to `ubuntu-latest`. Add `--runs-on` in a follow-up if needed.*

## Phase Roadmap

| Phase | Spec | Focus | Status |
|-------|------|-------|--------|
| 1 | 01-spec-agentry-cli | Core foundation | Complete |
| 2 | 02-spec-agentry-sandbox | Security & isolation | Complete |
| 3 | 03-spec-agentry-composition | Multi-agent composition | Complete |
| **4** | **This spec** | **GitHub Actions binder & CI generation** | **Current** |
