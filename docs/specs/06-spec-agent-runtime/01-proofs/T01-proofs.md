# T01 Proof Summary: AgentProtocol and ClaudeCodeAgent

## Task

Define the Agent abstraction and implement the Claude Code backend.

## Artifacts

| File | Type | Status |
|------|------|--------|
| T01-01-test.txt | test | PASS |
| T01-02-cli.txt | cli | PASS |

## Details

### T01-01-test.txt — Unit test suite

All 34 unit tests pass covering:

- `ClaudeCodeAgent` satisfies `AgentProtocol` at runtime (isinstance check with @runtime_checkable)
- `AgentTask` carries all required and optional fields (system_prompt, task_description, tool_names, output_schema, timeout, max_iterations, working_directory)
- `AgentResult` carries all required fields (output, raw_output, exit_code, token_usage, tool_invocations, timed_out, error)
- `ClaudeCodeAgent.check_available()` returns `True` when `claude` binary is on PATH (mocked)
- `ClaudeCodeAgent.check_available()` returns `False` when `claude` is absent (mocked)
- Mock subprocess execution produces a valid `AgentResult` with correct exit code and raw output
- `--model` flag is included in subprocess command
- `-p` (print mode) flag is included
- `--system-prompt` flag is passed
- `--output-format json` is included when output_schema is set; omitted otherwise
- Structured JSON response parsed into `AgentResult.output`
- Timeout enforcement: `AgentResult.timed_out = True` when TimeoutExpired
- Token usage extracted from JSON `usage` envelope (input_tokens, output_tokens)
- `AgentRegistry.default()` includes `"claude-code"` runtime
- `AgentRegistry.get("claude-code")` returns a `ClaudeCodeAgent` instance
- `AgentRegistry.get("unknown-agent")` raises `KeyError`
- Custom factories can be registered and resolved

### T01-02-cli.txt — Prerequisite check

`which claude` returns `/Users/norrie/.local/bin/claude` confirming the Claude Code CLI is installed and available on PATH.

## Files Created

- `src/agentry/agents/__init__.py`
- `src/agentry/agents/protocol.py` — `AgentProtocol` (PEP-544 @runtime_checkable)
- `src/agentry/agents/models.py` — `AgentTask`, `AgentResult`, `TokenUsage` (Pydantic v2)
- `src/agentry/agents/claude_code.py` — `ClaudeCodeAgent`
- `src/agentry/agents/registry.py` — `AgentRegistry`
- `tests/unit/test_agent_protocol.py` — 34 unit tests
