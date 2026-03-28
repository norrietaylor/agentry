# 08-spec-ci-self-development-loop

## Introduction/Overview

Wire Agentry's standard workflows into a CI-driven self-development loop. New issues trigger the planning-pipeline (triage → decompose → summarize), and when triage labels an issue as `category:bug`, a bug-fix workflow diagnoses the issue and opens a fix PR. Combined with the existing code-review workflow on PRs, this creates a closed loop: issue filed → triaged → decomposed → bug diagnosed → fix PR opened → PR reviewed — all without human intervention.

## Goals

1. Replace the single-workflow issue triage CI trigger with the full planning-pipeline (triage → decompose → summarize)
2. Add a label-triggered bug-fix workflow that runs when `category:bug` is applied
3. The bug-fix workflow shall diagnose the bug and open a fix PR via `pr:create`
4. All agent-generated PRs get automatically reviewed by the existing code-review workflow
5. Update workflow YAMLs to use source mapping and the `agent:` block consistently

## User Stories

- As a **maintainer**, I want new issues to be fully planned (triaged, decomposed, and summarized) so I have a complete picture without manual work.
- As the **Agentry project**, I want bug reports to be automatically diagnosed and fixed so the self-development loop is closed end-to-end.
- As a **contributor**, I want to see a diagnosis, root cause, and a fix PR linked to my bug report so the issue progresses without waiting for a human.

## Demoable Units of Work

### Unit 1: Planning Pipeline CI Workflow

**Purpose:** Replace the triage-only CI workflow with the full planning-pipeline on new issues.

**Functional Requirements:**
- Replace `.github/workflows/agentry-issue-triage.yml` with `.github/workflows/agentry-planning-pipeline.yml` triggered on `issues: [opened]`
- The workflow shall run `agentry --output-format json run workflows/planning-pipeline.yaml --input repository-ref=. --binder github-actions`
- The `planning-pipeline.yaml` shall be updated: add `source: issue.body` and `fallback: issue.title` to `issue-description` input
- The `planning-pipeline.yaml` shall add `issue:comment` and `issue:label` to its tool capabilities so triage results can be posted and labels applied
- Permissions shall be `contents: read`, `issues: write`
- The composed steps (triage → task-decompose → summary) shall each post their results as separate issue comments
- The triage step shall still apply `severity:*` and `category:*` labels via `map_outputs()`

**Proof Artifacts:**
- File: `.github/workflows/agentry-planning-pipeline.yml` exists with correct trigger, permissions, and run command
- File: `workflows/planning-pipeline.yaml` has `source: issue.body`, `fallback: issue.title`, and `issue:comment` + `issue:label` in capabilities
- Test: Existing e2e tests pass (no regressions from workflow YAML changes)

### Unit 2: Bug-Fix Workflow with PR Creation

**Purpose:** Update the bug-fix workflow to support CI execution with `pr:create` for automatic fix PRs.

**Functional Requirements:**
- The `workflows/bug-fix.yaml` shall be updated to use the `agent:` block (replacing `model:` block) with `max_iterations: 3` (bug-fix needs shell:execute iteration)
- The `bug-fix.yaml` shall add `source: issue.body` and `fallback: issue.title` to the `issue-description` input
- The `bug-fix.yaml` shall add `pr:create` and `issue:comment` to its `tools.capabilities`
- The bug-fix system prompt (`prompts/bug-fix-system-prompt.md`) shall be updated to instruct the agent to: diagnose, fix, commit, and open a PR with the `agent-proposed` label
- The fix PR body shall reference the originating issue number (e.g., "Fixes #42")
- After creating the PR, the agent shall post a comment on the original issue linking to the fix PR

**Proof Artifacts:**
- File: `workflows/bug-fix.yaml` uses `agent:` block with `pr:create`, `issue:comment`, and source mapping
- CLI: `agentry run workflows/bug-fix.yaml --input issue-description="X returns 500" --input repository-ref=. --output-format json` returns valid JSON with diagnosis, root_cause, suggested_fix, and confidence
- Test: Existing e2e tests pass

### Unit 3: Label-Triggered Bug-Fix CI Workflow

**Purpose:** Create a CI workflow that triggers the bug-fix workflow when triage applies the `category:bug` label.

**Functional Requirements:**
- Create `.github/workflows/agentry-bug-fix.yml` triggered on `issues: [labeled]`
- The workflow shall include a conditional: only run when the applied label is `category:bug`
- The `agentry run` command shall be: `agentry --output-format json run workflows/bug-fix.yaml --input repository-ref=. --binder github-actions`
- The issue body resolves via source mapping (no `--input issue-description` needed)
- Permissions shall be `contents: write` (for creating branches/commits), `issues: write` (for commenting), `pull-requests: write` (for opening PRs)
- The workflow shall require `CLAUDE_CODE_OAUTH_TOKEN` and `GITHUB_TOKEN` secrets
- The workflow structure shall follow the established pattern from `agentry-code-review.yml`

**Proof Artifacts:**
- File: `.github/workflows/agentry-bug-fix.yml` exists with `issues: [labeled]` trigger, label conditional, and correct permissions
- File: Workflow YAML passes syntax validation

### Unit 4: Remove Superseded Triage-Only Workflow

**Purpose:** Clean up the triage-only workflow now that the planning-pipeline replaces it.

**Functional Requirements:**
- Delete `.github/workflows/agentry-issue-triage.yml` (superseded by `agentry-planning-pipeline.yml`)
- Update the top-level comment in `agentry-planning-pipeline.yml` to note it replaces the triage-only workflow
- Verify no other workflows or docs reference the deleted file

**Proof Artifacts:**
- File: `.github/workflows/agentry-issue-triage.yml` does not exist
- File: `.github/workflows/agentry-planning-pipeline.yml` exists
- CLI: `grep -r "agentry-issue-triage" .github/ docs/` returns no results

## Non-Goals (Out of Scope)

- **Scheduled/cron-triggered pipeline runs** — backlog grooming is a future spec
- **Auto-merge of agent PRs** — all PRs require human review
- **Token budget enforcement** — cost caps per execution are deferred
- **Auto-assignment to GitHub users** — triage recommends assignees but doesn't assign
- **CI generation via `agentry ci generate`** — workflows are hand-authored
- **Multi-step composition nesting** — bug-fix runs as a standalone workflow, not nested in planning-pipeline

## Design Considerations

No specific design requirements. CI workflows follow the established pattern from `agentry-code-review.yml`. The label conditional in the bug-fix workflow uses GitHub Actions `if:` syntax.

## Repository Standards

- ruff formatting (line-length 100, Python 3.10 target)
- mypy strict mode for `src/agentry/`
- pytest markers: `unit`, `integration`, `e2e`
- Workflow YAMLs use `agent:` block (not legacy `model:` block)

## Technical Considerations

- **Planning-pipeline composition:** The pipeline runs 3 steps sequentially (triage → task-decompose → summary). Each step is a separate workflow execution. The `map_outputs()` method in `GitHubActionsBinder` handles issue comment posting and labeling for each step that has issue tools.
- **Label conditional:** GitHub Actions `if: github.event.label.name == 'category:bug'` filters the `issues: labeled` event to only trigger on the specific label.
- **Bug-fix needs `contents: write`:** Unlike triage (read-only), bug-fix creates branches and commits via `pr:create`, requiring write permissions on repository contents.
- **Source mapping for bug-fix:** The `issue-description` input in `bug-fix.yaml` will use the same `source: issue.body` / `fallback: issue.title` pattern as triage.
- **Race condition:** If triage and bug-fix both trigger from the same issue event, the label trigger ensures bug-fix runs after triage completes and applies the label. No race.

## Security Considerations

- Bug-fix workflow has `contents: write` — scoped to creating branches and commits only, never force-push
- `pr:create` enforces protected branch guardrails (cannot push to main/master)
- Agent-generated PRs get the `agent-proposed` label for easy identification
- All fix PRs are reviewed by the code-review workflow before merge
- `CLAUDE_CODE_OAUTH_TOKEN` and `GITHUB_TOKEN` must be repository secrets

## Success Metrics

- New issues receive full planning results (triage + decomposition + summary) within 5 minutes
- Bug-labeled issues get a fix PR opened within 10 minutes of the label being applied
- Fix PRs reference the originating issue and include diagnosis in the body
- Zero manual intervention required from issue creation to PR review

## Open Questions

1. Should the planning-pipeline post one combined comment or three separate step comments?
2. Should bug-fix PRs target a specific base branch (e.g., `develop`) or always `main`?
3. When the fix PR is merged, should a bot comment on the original issue closing it?
