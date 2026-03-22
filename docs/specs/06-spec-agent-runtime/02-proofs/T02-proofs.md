# T02 Proof Artifacts: Runner-Agent Integration

## Task Summary

Refactored runners to own agent execution by composing with the Agent abstraction:

- **`AgentConfig`** (runners/protocol.py): Replaced `llm_config` field with `agent_name: str` and `agent_config: dict[str, Any]`. Added `output`, `token_usage`, and `tool_invocations` fields to `ExecutionResult`.
- **`InProcessRunner`** (runners/in_process.py): Changed constructor to accept `agent: AgentProtocol` instead of `llm_client`. The `execute()` method builds an `AgentTask` and delegates to `agent.execute()`, then maps `AgentResult` fields onto `ExecutionResult`. Removed `AgentExecutor` dependency entirely.
- **`RunnerDetector`** (runners/detector.py): Changed constructor to accept `agent_registry: AgentRegistry` and `agent_name: str` instead of `llm_client`. The `get_runner()` method resolves the agent by name from the registry and injects it into `InProcessRunner` for elevated trust mode.

## Proof Artifacts

| File | Type | Status |
|------|------|--------|
| T02-01-test.txt | test | PASS |
| T02-02-test.txt | test | PASS |

## Evidence

### T02-01: InProcessRunner delegates to mock AgentProtocol, no LLMClient

- 15 tests in `tests/unit/test_in_process_runner.py` all pass.
- Tests verify: `agent` parameter replaces `llm_client`, no `AgentExecutor` in source, `execute()` calls `agent.execute()` with `AgentTask`, `ExecutionResult` populated from `AgentResult.output`, `.token_usage`, and `.tool_invocations`.

### T02-02: RunnerDetector resolves agent by name

- 12 tests in `tests/unit/test_runner_detector.py` all pass.
- Tests verify: `AgentRegistry` replaces `llm_client`, `get_runner(trust=elevated)` calls `registry.get(agent_name)`, resolved agent is injected into `InProcessRunner`, `agent_kwargs` are forwarded, Docker path unchanged for sandboxed trust.

## Files Modified

- `src/agentry/runners/protocol.py` - AgentConfig and ExecutionResult updated
- `src/agentry/runners/in_process.py` - Delegates to AgentProtocol, no AgentExecutor
- `src/agentry/runners/detector.py` - Uses AgentRegistry to resolve agent by name
- `tests/unit/test_runner_protocol.py` - Updated to use new interfaces
- `tests/unit/test_in_process_runner.py` - New focused tests for InProcessRunner
- `tests/unit/test_runner_detector.py` - New focused tests for RunnerDetector
