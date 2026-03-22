# T04: SecurityEnvelope Cleanup - Proof Summary

## Task

Simplify the SecurityEnvelope to work through runners only, eliminating the direct AgentExecutor dependency.

## Changes Made

### src/agentry/security/envelope.py
- Removed `executor` parameter from `SecurityEnvelope.__init__`
- Removed duplicate `RunnerProtocol` definition -- now imported from `agentry.runners.protocol`
- Imports `AgentConfig`, `ExecutionResult`, `RunnerContext`, `RunnerProtocol`, `RunnerStatus` from `agentry.runners.protocol`
- Updated `provision()` call to canonical signature: `self._runner.provision(safety_block, resolved_inputs)` returning `RunnerContext`
- Updated `teardown()` call to canonical signature: `self._runner.teardown(runner_context)`
- Replaced `self._executor.run(...)` with `self._runner.execute(runner_context, agent_config)` where `agent_config` is an `AgentConfig` instance
- `EnvelopeResult.execution_record` (type `ExecutionRecord`) replaced by `execution_result` (type `ExecutionResult`)
- `execute()` method signature updated: replaced `config: LLMConfig` and `retry_config` with `agent_name` and `agent_config` dict

### tests/unit/test_security_envelope.py
- Removed all imports of `AgentExecutor`, `ExecutionRecord`, `ToolInvocation`
- `MockRunner` updated to canonical protocol: `provision(safety_block, resolved_inputs) -> RunnerContext`, `execute(runner_context, agent_config) -> ExecutionResult`, `teardown(runner_context) -> None`, `check_available() -> RunnerStatus`
- Removed `_make_executor()` helper -- no longer needed
- Updated all `SecurityEnvelope(...)` construction calls to remove `executor=` parameter
- Updated assertions to use `execution_result` not `execution_record`
- Added `TestRunnerProtocolImport` class to verify canonical protocol import and no executor in signature

## Proof Artifacts

| File | Type | Status |
|------|------|--------|
| T04-01-test.txt | test | PASS |
| T04-02-cli.txt | cli | PASS |

## Results

- **30 tests pass** (up from 27 -- added 3 new protocol import/delegation tests)
- `agentry validate workflows/code-review.yaml` returns `{"status": "valid", ...}`
- `SecurityEnvelope.__init__` has no `executor` parameter (verified by introspection test)
- `RunnerProtocol` is no longer duplicated in `security/envelope.py`
- Agent execution delegates to `runner.execute(runner_context, agent_config)`
