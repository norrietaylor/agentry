# T03 Proof Summary: DockerRunner Agent Support

## Task

Update DockerRunner.execute() to launch the configured agent (Claude Code) inside the container. Update the shim module to act as a thin launcher for agent runtimes. Parse container stdout JSON into AgentResult using shared parsing logic. Pass agent configuration via environment variables or mounted config file.

## Implementation

### Files Modified

- `src/agentry/runners/docker_runner.py` - Updated to:
  - Pass `ANTHROPIC_API_KEY` from host env into container environment via `provision()`
  - Serialize `agent_name` and `agent_config` (replacing `llm_config`) in `_build_config_payload()`
  - Add `_parse_agent_result()` to extract AgentResult fields from shim output
  - Populate `ExecutionResult.output`, `token_usage`, `tool_invocations` from parsed result

- `src/agentry/runners/shim.py` - Updated to:
  - Remove all `AgentExecutor`, `LLMConfig`, `create_llm_client` references
  - Use `AgentRegistry.default().get(agent_name)` to resolve agent runtime
  - Build `AgentTask` from config (system_prompt, resolved_inputs, tool_names, timeout)
  - Call `agent.execute(task)` and serialize `AgentResult` to result.json
  - Default `agent_name` to `"claude-code"` if not specified in config

- `tests/unit/test_docker_runner.py` - Updated to:
  - Fix `_make_agent_config()` to use `agent_name`/`agent_config` (not `llm_config`)
  - Update `TestExecuteConfigPayload` for new payload structure
  - Add `TestProvisionAgentEnvironment`: ANTHROPIC_API_KEY forwarding tests
  - Add `TestParseAgentResult`: `_parse_agent_result()` unit tests
  - Add `TestExecuteAgentResultParsing`: integration of execute + result parsing

- `tests/unit/test_shim.py` - Updated to:
  - Fix config fixtures to use `agent_name`/`agent_config` instead of `llm_config`
  - Add `TestRunShimAgentRuntime`: 6 tests verifying shim uses agent runtime
  - Add `TestShimNoAgentExecutor`: verifies AgentExecutor absent from module imports

## Proof Artifacts

| File | Type | Status |
|------|------|--------|
| T03-01-test.txt | test | PASS |
| T03-02-file.txt | file | PASS |

## Test Results

- 84 tests passed, 1 skipped (docker integration test, requires Docker daemon)
- 0 new failures introduced
- Pre-existing 33 failures in other test files are unrelated to T03

## Architecture

The shim acts as a thin launcher:
1. Reads `/config/agent_config.json` from the mounted config file
2. Resolves agent runtime via `AgentRegistry` (defaults to `ClaudeCodeAgent`)
3. Builds `AgentTask` from `system_prompt` + assembled `task_description`
4. Calls `agent.execute(task)` which invokes `claude -p` inside the container
5. Writes `AgentResult` fields as JSON to `/output/result.json`

DockerRunner.execute() then reads the result file and populates ExecutionResult.
