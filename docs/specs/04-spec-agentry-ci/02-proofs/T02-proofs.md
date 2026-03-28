# T02 Proof Summary: Issue Input Resolution via Source Mapping

## Task
T02 — Enable the triage workflow's `issue-description` string input to resolve from
the GitHub issue event payload using existing source mapping.

## Changes Implemented

### 1. `workflows/triage.yaml`
- Added `source: issue.body` field to the `issue-description` input declaration
- Added `fallback: issue.title` field so that when `issue.body` is null/empty, the
  issue title is used as a fallback with a warning log

### 2. `src/agentry/binders/github_actions.py`
- Added `import logging` and `logger = logging.getLogger(__name__)`
- Updated `_resolve_string()` to support the `fallback` key in input specs:
  - When `source` resolves to null or empty string, the `fallback` dotpath is tried
  - A `WARNING` level log is emitted when falling back, including the input name,
    source path, fallback path, and resolved fallback value
  - CLI `--input` overrides (via `provided_values`) still take strict priority
    over both `source` and `fallback`

### 3. `tests/unit/test_github_binder_inputs.py`
- Added `TestResolveInputsIssueBodySource` class with 9 new tests covering:
  - `issue.body` resolution from `issues` event payload
  - Fallback to `issue.title` when body is null
  - Fallback to `issue.title` when body key is absent
  - Fallback to `issue.title` when body is an empty string
  - Warning log emission on fallback
  - CLI `--input` override taking precedence over source and fallback
  - CLI override wins even when body is null
  - No fallback key: returns None for optional missing source
  - Fallback not triggered when source resolves to a non-empty value

## Proof Artifacts

| File | Type | Status |
|------|------|--------|
| T02-01-test.txt | test (new tests only) | PASS |
| T02-02-test.txt | test (full unit suite) | PASS |

## Test Counts
- Baseline: 1534 passed, 1 skipped
- After T02: 1572 passed, 1 skipped (includes T01's tests too)
- T02 adds 9 new tests in `TestResolveInputsIssueBodySource`
