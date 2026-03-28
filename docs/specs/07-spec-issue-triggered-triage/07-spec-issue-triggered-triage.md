# 07-spec-issue-triggered-triage

## Introduction/Overview

Enable Agentry to automatically triage new GitHub issues by running the existing `triage.yaml` workflow when an issue is opened. The agent classifies severity, category, and routing, then posts results as an issue comment and applies corresponding GitHub labels. This closes the first half of the self-development loop: issues in → triage results out, without human intervention.

## Goals

1. Run `workflows/triage.yaml` automatically when a GitHub issue is opened
2. Post structured triage results (severity, category, routing, reasoning) as an issue comment
3. Apply GitHub labels derived from triage output (e.g., `severity:high`, `category:bug`)
4. Reuse existing infrastructure — no new input types, no new runner logic, no new agent runtimes
5. Follow the established CI workflow pattern from `agentry-code-review.yml`

## User Stories

- As a **maintainer**, I want new issues to be automatically triaged so that I can prioritize without manual classification.
- As a **contributor**, I want to see triage results on my issue so that I understand how it was categorized and who will handle it.
- As the **Agentry project**, I want to dogfood issue triage in CI so that the self-development loop extends beyond PR review.

## Demoable Units of Work

### Unit 1: Issue Tool Bindings in GitHubActionsBinder

**Purpose:** Add `issue:comment` and `issue:label` tool bindings so workflows can interact with GitHub issues, not just pull requests.

**Functional Requirements:**
- The `GitHubActionsBinder` shall support an `issue:comment` tool that posts a comment to the issue that triggered the workflow, using `POST /repos/{owner}/{repo}/issues/{number}/comments`
- The `GitHubActionsBinder` shall support an `issue:label` tool that applies one or more labels to the triggering issue, using `POST /repos/{owner}/{repo}/issues/{number}/labels`
- The `issue:label` tool shall create labels that don't yet exist in the repository (GitHub API does this implicitly via the labels endpoint)
- Both tools shall extract the issue number from the `issues` event payload (`event_payload["issue"]["number"]`)
- Both tools shall raise a clear error if invoked outside an `issues` event context
- The `SUPPORTED_TOOLS` frozenset shall be extended to include `issue:comment` and `issue:label`
- The binder's `bind_tools()` method shall wire these tools when declared in a workflow's `tools.capabilities`

**Proof Artifacts:**
- Test: `tests/unit/test_github_actions_binder.py` passes — unit tests for `issue:comment` and `issue:label` with mocked HTTP responses
- Test: `tests/integration/test_issue_tools.py` passes — integration test verifying tool dispatch against a mock event payload

### Unit 2: Issue Input Resolution via Source Mapping

**Purpose:** Enable the triage workflow's `issue-description` string input to resolve from the GitHub issue event payload without adding new input types.

**Functional Requirements:**
- The triage workflow YAML shall add a `source` field to the `issue-description` input: `source: issue.body` (dot-notation path into the event payload)
- The `GitHubActionsBinder.resolve_inputs()` method shall resolve `StringInput` fields with a `source` mapping by traversing the event payload (this capability already exists — verify it works for `issues` events, not just `workflow_dispatch`)
- When the `source` path resolves to `null` or empty string (issue with no body), the binder shall fall back to the issue title (`issue.title`) and log a warning
- The `repository-ref` input shall continue resolving to `GITHUB_WORKSPACE` as it does today
- CLI `--input` overrides shall still take precedence over source mapping

**Proof Artifacts:**
- Test: `tests/unit/test_github_actions_binder.py` passes — input resolution test with a mock `issues` event payload containing `issue.body` and `issue.title`
- CLI: `agentry run workflows/triage.yaml --input issue-description="test issue" --input repository-ref=. --target . --output-format json` returns valid JSON with `severity`, `category`, `affected_components`, `recommended_assignee`, `reasoning`

### Unit 3: Triage Output Formatting and Label Derivation

**Purpose:** Format triage results as a readable issue comment and derive label names from the structured output.

**Functional Requirements:**
- The `GitHubActionsBinder.map_outputs()` method shall detect `issues` events (in addition to existing `pull_request` detection) and post output as an issue comment
- The output formatter shall render triage results as Markdown: severity badge, category, affected components list, recommended assignee, and reasoning — following the existing `_format_output_comment()` pattern
- After posting the comment, `map_outputs()` shall extract `severity` and `category` from the output JSON and call `issue:label` to apply labels in the format `severity:<value>` and `category:<value>`
- Label application shall be best-effort — if labeling fails (permissions, API error), log a warning but do not fail the workflow run
- The execution record shall still be written to `.agentry/runs/<run_id>/` as with any other workflow

**Proof Artifacts:**
- Test: `tests/unit/test_issue_output_formatting.py` passes — verifies Markdown rendering of triage output and label derivation logic
- Test: `tests/integration/test_issue_triage_pipeline.py` passes — end-to-end test with mocked GitHub API verifying comment posted + labels applied

### Unit 4: GitHub Actions Workflow for Issue Triage

**Purpose:** Create the CI workflow file that triggers the triage pipeline on new issues.

**Functional Requirements:**
- A new workflow file `.github/workflows/agentry-issue-triage.yml` shall trigger on `issues: [opened]`
- The workflow shall follow the same structure as `agentry-code-review.yml`: checkout → setup Python → install Claude Code → install agentry → run
- The `agentry run` command shall be: `agentry --output-format json run workflows/triage.yaml --input issue-description="${{ github.event.issue.body }}" --input repository-ref=. --binder github-actions`
- Permissions shall be `contents: read`, `issues: write` (for commenting and labeling)
- The workflow shall require `CLAUDE_CODE_OAUTH_TOKEN` and `GITHUB_TOKEN` secrets
- The triage workflow YAML (`workflows/triage.yaml`) shall add `issue:comment` and `issue:label` to its `tools.capabilities` list

**Proof Artifacts:**
- File: `.github/workflows/agentry-issue-triage.yml` exists and passes `actionlint` validation
- CLI: Manual local test — `agentry run workflows/triage.yaml --input issue-description="Login page returns 500 after password reset" --input repository-ref=. --output-format json` returns valid triage JSON

## Non-Goals (Out of Scope)

- **Full planning-pipeline on issues** — only triage runs initially; extending to triage → decompose → summarize is a future spec
- **New `IssueInput` type in `models/inputs.py`** — use existing `StringInput` with source mapping
- **Auto-assignment to GitHub users** — labels and routing recommendations only, no `issue:assign` tool
- **Re-triage on issue edits or reopens** — trigger is `opened` only
- **Auto-merge or auto-fix from triage** — triage classifies; the bug-fix loop is a separate concern
- **CI generation via `agentry ci generate`** — the workflow file is hand-authored following the established pattern
- **Label color/description management** — labels are created with GitHub defaults

## Design Considerations

No specific design requirements identified. The issue comment format should be consistent with existing PR comment formatting in `GitHubActionsBinder._format_output_comment()`.

## Repository Standards

- PEP 544 protocols for all new abstractions (none expected — extending existing binder class)
- Pydantic v2 validation for any new model fields
- ruff formatting (line-length 100, Python 3.10 target)
- mypy strict mode for `src/agentry/`
- pytest markers: `unit` for mock-based tests, `integration` for tests requiring mock HTTP

## Technical Considerations

- **Event payload structure:** GitHub `issues` events place issue data at `event_payload["issue"]`. The binder already loads `GITHUB_EVENT_PATH` JSON — no new parsing needed.
- **Source mapping already works:** `GitHubActionsBinder.resolve_inputs()` traverses event payloads via dot-notation for `StringInput` fields. Verify this codepath handles `issues` events (currently tested only with `workflow_dispatch` and `pull_request`).
- **Issue vs. PR comment API:** GitHub issues and PRs share the same comments endpoint (`/repos/{owner}/{repo}/issues/{number}/comments`). The existing `pr:comment` implementation can likely be reused with minor refactoring to extract the number from the correct event type.
- **Label creation:** `POST /repos/{owner}/{repo}/issues/{number}/labels` with `{"labels": ["severity:high", "category:bug"]}` creates missing labels automatically.
- **Workflow YAML change is minimal:** Add `issue:comment` and `issue:label` to `tools.capabilities` in `triage.yaml`. The `source: issue.body` field on `issue-description` input enables event-driven resolution.

## Security Considerations

- The workflow requires `issues: write` permission (not `admin`) — scoped to commenting and labeling only
- `CLAUDE_CODE_OAUTH_TOKEN` must be a repository secret, never logged
- Issue bodies are untrusted user input passed to the agent — the existing SecurityEnvelope and trust model apply
- No filesystem write tools are granted to the triage workflow (only `repository:read`)

## Success Metrics

- New issues in the Agentry repo receive a triage comment within 2 minutes of creation
- Triage labels (`severity:*`, `category:*`) are applied correctly on >90% of issues
- Zero false `critical` severity classifications on routine feature requests
- Workflow cost stays under $0.05 per triage run (single Sonnet call, ~2K output tokens)

## Open Questions

1. Should the triage comment include a "re-triage" button (workflow_dispatch link) for cases where the initial classification is wrong?
2. Should there be a label (e.g., `skip-triage`) that suppresses the workflow for issues that don't need classification?
3. When the planning-pipeline is wired in a future spec, should it replace this workflow or run as a separate trigger?
