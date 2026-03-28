# T01 Proof Summary: Replace triage-only CI with planning-pipeline workflow

## Task

Replace the single-workflow issue triage CI trigger with the full planning-pipeline.
Create `.github/workflows/agentry-planning-pipeline.yml` triggered on `issues: [opened]`
that runs `planning-pipeline.yaml`. Update `planning-pipeline.yaml` to add
`source: issue.body` and `fallback: issue.title` to `issue-description` input,
add `issue:comment` and `issue:label` to capabilities, and switch from `model:` block
to `agent:` block.

## Changes Made

**Created:**
- `.github/workflows/agentry-planning-pipeline.yml`
  - Trigger: `issues: [opened]`
  - Permissions: `contents: read`, `issues: write`
  - Runs `agentry run workflows/planning-pipeline.yaml` with `--binder github-actions`
  - Structure mirrors `agentry-issue-triage.yml`

**Modified:**
- `workflows/planning-pipeline.yaml`
  - Added `source: issue.body` and `fallback: issue.title` to `issue-description` input
  - Added `tools.capabilities` block with `issue:comment` and `issue:label`
  - Replaced `model:` block with `agent:` block (`runtime: claude-code`, `model: claude-sonnet-4-20250514`)

## Proof Artifacts

| Artifact | Type | Status |
|----------|------|--------|
| T01-01-file.txt | file | PASS |
| T01-02-file.txt | file | PASS |

## Verification Results

- CI workflow file valid YAML with correct trigger and permissions: PASS
- `planning-pipeline.yaml` has `source: issue.body`: PASS
- `planning-pipeline.yaml` has `fallback: issue.title`: PASS
- `planning-pipeline.yaml` has `issue:comment` capability: PASS
- `planning-pipeline.yaml` has `issue:label` capability: PASS
- `planning-pipeline.yaml` has `agent:` block (no `model:` block): PASS

## Proof Status: COMPLETE
