# 09-spec-feature-implementation-pipeline

## Introduction/Overview

Add a feature implementation workflow that triggers when triage labels an issue `category:feature`. The agent reads the decomposed tasks from the planning-pipeline, implements the feature, and opens a PR. For features that exceed a safe blast radius, the agent creates scoped sub-issues instead of attempting a monolithic change. This completes the self-development loop for both bugs and features.

## Goals

1. Create a `feature-implement` workflow YAML that implements features based on planning-pipeline decomposition
2. Add a label-triggered CI workflow for `category:feature` issues
3. The agent shall self-assess scope and create sub-issues when the feature is too large for a single PR
4. The agent shall open a PR with tests for implementable features, referencing the originating issue
5. Reuse existing infrastructure: source mapping, `pr:create`, `issue:comment`, label triggers

## User Stories

- As a **maintainer**, I want feature requests to be automatically implemented when they're small enough, so the project progresses without manual coding for straightforward features.
- As a **contributor**, I want my feature request to either get a PR or scoped sub-issues, so I know the next steps regardless of complexity.
- As the **Agentry project**, I want features and bugs to follow the same automated pipeline pattern so the self-development loop is symmetric.

## Demoable Units of Work

### Unit 1: Feature-Implement Workflow YAML

**Purpose:** Create the workflow definition that implements features based on planning-pipeline output.

**Functional Requirements:**
- Create `workflows/feature-implement.yaml` with identity `feature-implement` v1.0.0
- The workflow shall use the `agent:` block with `runtime: claude-code`, `model: claude-sonnet-4-20250514`, and `max_iterations: 10` (features need more iteration than bug-fix)
- Inputs: `issue-description` (string, required, `source: issue.body`, `fallback: issue.title`) and `repository-ref` (repository-ref, required)
- Tools: `repository:read`, `shell:execute`, `pr:create`, `issue:comment`, `issue:label`
- Add a new tool capability `issue:create` to allow the agent to create sub-issues
- The system prompt (`prompts/feature-implement-system-prompt.md`) shall instruct the agent to:
  1. Read the issue body and any planning-pipeline comments for decomposed tasks
  2. Assess whether the feature is implementable in a single PR (heuristic: ≤5 files changed, ≤500 lines added)
  3. If implementable: implement the feature with tests, commit, open a PR with `agent-proposed` label, and comment on the issue linking the PR
  4. If too large: create scoped sub-issues (one per decomposed task), label them `category:feature` and `agent-decomposed`, and comment on the parent issue explaining the decomposition
- Output schema: JSON with `action` (enum: `implemented`, `decomposed`), `pr_url` or `sub_issues` array, and `reasoning`
- Safety: `trust: elevated`, timeout: 600s

**Proof Artifacts:**
- File: `workflows/feature-implement.yaml` exists with correct structure
- File: `workflows/prompts/feature-implement-system-prompt.md` exists with implementation instructions
- CLI: `agentry run workflows/feature-implement.yaml --input issue-description="Add a --verbose flag to the CLI" --input repository-ref=. --output-format json` returns valid JSON

### Unit 2: issue:create Tool Binding

**Purpose:** Add `issue:create` tool to GitHubActionsBinder and LocalBinder so the agent can create sub-issues.

**Functional Requirements:**
- The `GitHubActionsBinder` shall support an `issue:create` tool that creates a new issue via `POST /repos/{owner}/{repo}/issues`
- The tool signature: `issue_create(*, title: str, body: str, labels: list[str] | None = None) -> dict[str, Any]`
- The tool shall return `{"number": int, "url": str, "status": "created"}`
- The `SUPPORTED_TOOLS` frozenset in both binders shall include `issue:create`
- The `LocalBinder` shall have a stub that prints metadata and returns a placeholder
- Error handling shall follow the established pattern (403/404/timeout with remediation hints)

**Proof Artifacts:**
- Test: `tests/unit/test_github_binder_tools.py` passes — new tests for `issue:create` with mocked HTTP
- Test: `tests/integration/test_issue_tools.py` passes — integration test for `issue:create`

### Unit 3: Label-Triggered Feature CI Workflow

**Purpose:** Create the CI workflow that triggers feature implementation when `category:feature` is applied.

**Functional Requirements:**
- Create `.github/workflows/agentry-feature-implement.yml` triggered on `issues: [labeled]`
- The workflow shall include a conditional: only run when the applied label is `category:feature`
- The `agentry run` command: `agentry --output-format json run workflows/feature-implement.yaml --input repository-ref=. --binder github-actions`
- Permissions: `contents: write`, `issues: write`, `pull-requests: write`
- Secrets: `CLAUDE_CODE_OAUTH_TOKEN`, `GITHUB_TOKEN`
- Follow the established pattern from `agentry-bug-fix.yml`

**Proof Artifacts:**
- File: `.github/workflows/agentry-feature-implement.yml` exists with correct trigger, label conditional, and permissions
- File: Workflow YAML is valid

## Non-Goals (Out of Scope)

- **Auto-merge of feature PRs** — all PRs require human review
- **Multi-PR features** — the agent implements in one PR or decomposes into sub-issues; it does not orchestrate sequential PRs
- **Interactive clarification** — the agent works with the information available; it does not ask the issue author for more details
- **Token budget enforcement** — deferred to a future spec
- **Recursive sub-issue implementation** — sub-issues labeled `category:feature` will trigger the same workflow, but there is no depth limit enforcement in this spec (addressed by the decomposition heuristic)

## Design Considerations

No specific design requirements. The feature-implement system prompt is the critical design artifact — it must guide the agent to make good scope decisions.

## Repository Standards

- ruff formatting (line-length 100, Python 3.10 target)
- mypy strict mode for `src/agentry/`
- pytest markers: `unit`, `integration`, `e2e`
- Workflow YAMLs use `agent:` block consistently
- All new tool bindings follow the `_make_*` factory pattern in GitHubActionsBinder

## Technical Considerations

- **Planning-pipeline context:** The agent reads decomposed tasks from issue comments posted by the planning-pipeline. These are Markdown-formatted comments with task titles, descriptions, priorities, and effort estimates. The system prompt must instruct the agent to parse these.
- **Scope heuristic:** The ≤5 files / ≤500 lines threshold is a soft guide in the system prompt, not enforced programmatically. The agent uses judgment.
- **Sub-issue creation:** New `issue:create` tool posts to `POST /repos/{owner}/{repo}/issues`. Each sub-issue body references the parent issue number. Labels `category:feature` + `agent-decomposed` are applied.
- **Recursive triggering:** Sub-issues labeled `category:feature` will trigger this same workflow. This is intentional — each sub-issue is scoped small enough to implement. The `agent-decomposed` label helps distinguish agent-created sub-issues from user-filed ones.
- **Source mapping:** Same pattern as triage and bug-fix — `source: issue.body` with `fallback: issue.title`.

## Security Considerations

- Feature implementation needs `contents: write` for branch creation and commits
- `pr:create` enforces protected branch guardrails
- `issue:create` cannot create issues in other repositories (scoped to `GITHUB_REPOSITORY`)
- Agent-generated PRs get `agent-proposed` label; sub-issues get `agent-decomposed` label
- All PRs are reviewed by the code-review workflow

## Success Metrics

- Feature-labeled issues get either a PR or sub-issues within 15 minutes
- Implemented features include tests in the PR
- Sub-issue decomposition produces 2-5 focused sub-issues (not 1 or 20+)
- Agent-proposed feature PRs pass CI on first attempt >50% of the time

## Open Questions

1. Should there be a `skip-implement` label to suppress automatic implementation for sensitive features?
2. Should the agent add itself as a reviewer on the PR it creates?
3. What's the right depth limit for recursive sub-issue decomposition (e.g., max 2 levels)?
