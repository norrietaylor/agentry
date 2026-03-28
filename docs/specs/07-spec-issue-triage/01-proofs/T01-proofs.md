# T01 Proof Summary: Issue Tool Bindings in GitHubActionsBinder

## Task

Add `issue:comment` and `issue:label` tool bindings to `GitHubActionsBinder` so
workflows can interact with GitHub issues.

## Changes Made

**Modified:**
- `src/agentry/binders/github_actions.py`
  - Extended `SUPPORTED_TOOLS` frozenset with `"issue:comment"` and `"issue:label"`
  - Added `_extract_issue_number()` static method (parallel to `_extract_pr_number`)
  - Store `self._issue_number` in `__init__` from issues event payload
  - Implemented `_make_issue_comment()` — POST `/repos/{owner}/{repo}/issues/{number}/comments`
  - Implemented `_make_issue_label()` — POST `/repos/{owner}/{repo}/issues/{number}/labels`
  - Wired both in `bind_tools()` dispatch

**Modified:**
- `tests/unit/test_github_binder_tools.py`
  - Added `TestExtractIssueNumber` class (6 tests)
  - Added `TestBindToolsIssueComment` class (11 tests)
  - Added `TestBindToolsIssueLabel` class (12 tests)

**Created:**
- `tests/integration/test_issue_tools.py`
  - `TestIssueCommentIntegration` (8 tests)
  - `TestIssueLabelIntegration` (9 tests)
  - `TestSupportedToolsContainsIssueTools` (3 tests)

## Proof Artifacts

| Artifact | Type | Status |
|----------|------|--------|
| T01-01-test.txt | test | PASS (97/97) |
| T01-02-cli.txt | cli | PASS |

## Test Results

- 97 tests total, 97 passed, 0 failed
- All existing binder tests continue to pass (48 pre-existing)
- 49 new tests covering issue:comment and issue:label bindings

## Key Behaviours Verified

1. `issue:comment` and `issue:label` appear in `SUPPORTED_TOOLS`
2. `_extract_issue_number()` returns the issue number from `issues` events, `None` otherwise
3. `_issue_number` is `None` when event is not `issues`
4. `issue:comment` posts to `POST /repos/{owner}/{repo}/issues/{number}/comments`
5. `issue:label` posts to `POST /repos/{owner}/{repo}/issues/{number}/labels`
6. Both tools raise `ValueError` with "issues event" message when called outside an issues context
7. Both tools raise `RuntimeError` with structured remediation on 403/404 API errors
8. Both tools raise `RuntimeError` with "timeout" on network timeout
9. `issue:label` additionally handles 422 (validation failure) with remediation hint
