# T05 Proof Summary: Workflow Schema and CLI Update

## Task
Add AgentBlock Pydantic model, update WorkflowDefinition with backward compatibility,
update CLI to validate agent runtime and run with agent-aware RunnerDetector,
update workflows/code-review.yaml to use agent block.

## Artifacts

| File | Type | Status |
|------|------|--------|
| T05-01-test.txt | Unit tests (26 tests) | PASS |
| T05-02-cli.txt | agentry validate CLI | PASS |
| T05-03-file.txt | workflows/code-review.yaml agent block | PASS |

## Summary

### Implementation

1. **`src/agentry/models/agent.py`** (new file)
   - `AgentBlock` Pydantic model with fields: `runtime` (required), `model`, `system_prompt`, `max_iterations`, `config`
   - `KNOWN_RUNTIMES` frozenset containing `"claude-code"`

2. **`src/agentry/models/workflow.py`** (modified)
   - Added `agent: AgentBlock | None = None` field to `WorkflowDefinition`
   - Added `_backfill_agent_from_model` validator: auto-converts `model` block to `AgentBlock` when no `agent` block present
   - Agent block takes precedence when both are supplied

3. **`src/agentry/models/__init__.py`** (modified)
   - Exports `AgentBlock` and `KNOWN_RUNTIMES`

4. **`src/agentry/security/checks.py`** (modified)
   - Added `AgentAvailabilityCheck` class that verifies the required binary (e.g. `claude` for `claude-code` runtime) is on PATH

5. **`src/agentry/security/__init__.py`** (modified)
   - Exports `AgentAvailabilityCheck`

6. **`src/agentry/cli.py`** (modified)
   - `validate` command: checks for unknown agent runtimes and exits non-zero
   - `run` command: resolves agent runtime from workflow, passes to `RunnerDetector`, adds `AgentAvailabilityCheck` to preflight

7. **`workflows/code-review.yaml`** (modified)
   - Replaced deprecated `model` block with `agent` block using `runtime: claude-code`

8. **`tests/unit/test_workflow_parser.py`** (new file)
   - 26 unit tests covering AgentBlock model, backward compatibility, AgentAvailabilityCheck, and integration

## Verification

All 26 new tests pass. 78 existing workflow model tests pass. 180 total tests in relevant files pass.
Pre-existing failures in test_composition_engine.py and test_failure_policies.py are unrelated to T05.
