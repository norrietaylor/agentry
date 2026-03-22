# 06-spec-agent-runtime

## Introduction/Overview

Refactor Agentry's execution architecture to introduce a four-layer model: Agentry (orchestration) → Runner (execution environment) → Agent (coding agent runtime) → Model (LLM). Today, AgentExecutor makes direct LLM API calls via LLMClient, bypassing real agent runtimes. This spec replaces that with a first-class Agent abstraction, implementing ClaudeCodeAgent as the sole runtime. Runners provision the environment and delegate execution to the agent. The SecurityEnvelope is simplified to work through runners only.

## Goals

1. Introduce `AgentProtocol` as a first-class abstraction representing a coding agent runtime (Claude Code, Open Code, Aider, etc.).
2. Implement `ClaudeCodeAgent` that delegates to the Claude Code CLI (`claude --print`).
3. Make runners own agent execution — `Runner.execute()` launches the configured agent inside its provisioned environment.
4. Update `DockerRunner` to run Claude Code inside the container.
5. Unify the two divergent `RunnerProtocol` definitions and remove `AgentExecutor` from `SecurityEnvelope`.
6. Update the workflow YAML schema to declare an `agent` block instead of a `model` block.

## User Stories

- As a workflow author, I want to declare which agent runtime my workflow uses so that execution is handled by a real agent (Claude Code) rather than raw API calls.
- As an operator, I want sandboxed workflows to run Claude Code inside a Docker container so that the agent runtime is isolated alongside the environment.
- As a contributor, I want a clean layered architecture (Runner → Agent → Model) so that adding new agent runtimes (Open Code, Aider) requires implementing one protocol, not rewiring the executor.

## Demoable Units of Work

### Unit 1: AgentProtocol and ClaudeCodeAgent

**Purpose:** Define the Agent abstraction and implement the Claude Code backend.

**Functional Requirements:**
- The system shall define an `AgentProtocol` (PEP-544 runtime-checkable) with a single method: `execute(agent_task: AgentTask) -> AgentResult`.
- `AgentTask` shall carry: `system_prompt`, `task_description` (assembled from resolved inputs), `tool_names`, `output_schema` (optional), `timeout` (optional), `max_iterations` (optional), and `working_directory`.
- `AgentResult` shall carry: `output` (structured dict or None), `raw_output` (string), `exit_code` (int), `token_usage` (input/output tokens), `tool_invocations` (list), `timed_out` (bool), `error` (string).
- The system shall implement `ClaudeCodeAgent` that invokes the `claude` CLI in print mode (`claude -p`) with the system prompt and task description.
- `ClaudeCodeAgent` shall pass `--output-format json` when an output schema is defined, and parse the structured response.
- `ClaudeCodeAgent` shall pass `--model` with the configured model identifier.
- `ClaudeCodeAgent` shall enforce timeout by killing the subprocess if execution exceeds the configured limit.
- `ClaudeCodeAgent` shall capture token usage from Claude Code's JSON output metadata.
- `ClaudeCodeAgent.check_available()` shall verify the `claude` binary is on PATH and executable.
- The system shall provide an `AgentRegistry` that maps agent runtime names (e.g., `"claude-code"`) to agent factory functions.

**Proof Artifacts:**
- Test: `tests/test_agent_protocol.py` passes — verifies ClaudeCodeAgent satisfies AgentProtocol, check_available returns correct status, and a mock subprocess execution produces a valid AgentResult.
- CLI: `which claude` returns a path (prerequisite check).

### Unit 2: Runner-Agent Integration

**Purpose:** Make runners own agent execution by composing with the Agent abstraction.

**Functional Requirements:**
- `RunnerProtocol.execute()` signature shall change to accept `agent_config: AgentConfig` where `AgentConfig` now includes an `agent_name` field identifying the agent runtime.
- `InProcessRunner` shall accept an `AgentProtocol` instance (or resolve one from `AgentRegistry` by name) and delegate `execute()` to `agent.execute(agent_task)`.
- `InProcessRunner` shall no longer import or use `AgentExecutor`.
- `InProcessRunner.__init__` shall no longer require `llm_client` — the agent runtime handles model interaction.
- `RunnerDetector` shall resolve the agent runtime from the workflow's agent configuration and pass it to the runner.
- `AgentConfig` shall replace `llm_config` with `agent_name: str` and `agent_config: dict[str, Any]` (runtime-specific configuration like model ID).
- The `ExecutionResult` shall be populated from `AgentResult` fields (output, token usage, tool invocations, timing).

**Proof Artifacts:**
- Test: `tests/test_in_process_runner.py` passes — InProcessRunner delegates to a mock AgentProtocol, no LLMClient involved.
- Test: `tests/test_runner_detector.py` passes — RunnerDetector resolves agent by name and injects into runner.

### Unit 3: DockerRunner Agent Support

**Purpose:** Run the configured agent inside the Docker container.

**Functional Requirements:**
- `DockerRunner.execute()` shall launch the configured agent (Claude Code) inside the provisioned container instead of running the Python shim.
- The container command shall invoke `claude -p` with the system prompt and task description, matching ClaudeCodeAgent's interface but executing inside the sandbox.
- The Docker image specification in the workflow's safety block shall support images with Claude Code pre-installed (or a standard base image with a setup step).
- `DockerRunner` shall pass agent configuration (model, timeout, tool names) as environment variables or a mounted config file readable by the agent.
- The container's output (stdout JSON) shall be parsed into an `AgentResult` using the same parsing logic as `ClaudeCodeAgent`.
- The existing `agentry.runners.shim` module shall be replaced or updated to act as a thin launcher for the configured agent runtime inside the container.
- Timeout enforcement shall remain: container is killed (SIGKILL) if the agent exceeds the configured timeout.

**Proof Artifacts:**
- Test: `tests/test_docker_runner.py` passes — DockerRunner launches agent command inside container, parses output, enforces timeout (using mock Docker client).
- File: `src/agentry/runners/shim.py` updated to launch agent runtimes instead of AgentExecutor.

### Unit 4: SecurityEnvelope Cleanup

**Purpose:** Simplify the envelope to work through runners only, eliminating the direct executor dependency.

**Functional Requirements:**
- `SecurityEnvelope.__init__` shall no longer accept an `executor` parameter.
- `SecurityEnvelope.execute()` shall delegate agent execution to `self._runner.execute(runner_context, agent_config)` instead of calling `self._executor.run()` directly.
- The duplicate `RunnerProtocol` definition in `security/envelope.py` shall be removed. The envelope shall import and use `RunnerProtocol` from `runners/protocol.py`.
- The envelope's `RunnerProtocol.provision()` signature shall be updated to match the canonical signature: `provision(safety_block, resolved_inputs) -> RunnerContext`.
- The envelope's `RunnerProtocol.teardown()` shall accept `RunnerContext`.
- `EnvelopeResult` shall continue to carry `execution_record` but populated from the runner's `ExecutionResult.execution_record`.
- All call sites that construct `SecurityEnvelope` shall be updated to stop passing an executor.

**Proof Artifacts:**
- Test: `tests/test_security_envelope.py` passes — envelope delegates to runner, no executor parameter, preflight and validation still work.
- CLI: `agentry validate workflows/code-review.yaml` succeeds with updated envelope (no regression).

### Unit 5: Workflow Schema and CLI Update

**Purpose:** Update the workflow YAML schema to declare agent runtime instead of LLM provider, and update the CLI to resolve agent configuration.

**Functional Requirements:**
- The workflow YAML schema shall accept an `agent` block replacing (or alongside) the `model` block:
  ```yaml
  agent:
    runtime: claude-code
    model: claude-sonnet-4-20250514
    system_prompt: prompts/code-review.md
    max_iterations: 20
  ```
- The `model` block shall be accepted as a deprecated alias — if present without an `agent` block, it is auto-converted to `agent: { runtime: claude-code, model: <model_id>, system_prompt: <system_prompt> }`.
- The `WorkflowDefinition` Pydantic model shall include an `agent` field of type `AgentBlock` with: `runtime` (str), `model` (str), `system_prompt` (str path or inline), `max_iterations` (int, optional), and `config` (dict, optional for runtime-specific settings).
- The CLI `run` command shall resolve the agent runtime from the workflow definition and pass it to `RunnerDetector`.
- The CLI shall verify agent availability during preflight (e.g., `claude` binary exists for `claude-code` runtime).
- `agentry validate` shall validate the `agent` block and report errors for unknown runtimes.

**Proof Artifacts:**
- Test: `tests/test_workflow_parser.py` passes — parses new `agent` block, handles `model` block backward compat.
- CLI: `agentry validate workflows/code-review.yaml` succeeds with updated schema.
- File: `workflows/code-review.yaml` updated to use `agent` block.

## Non-Goals (Out of Scope)

- Implementing additional agent runtimes (Open Code, Aider, Ollama-based agents). This spec proves the abstraction with Claude Code; others are follow-up work.
- Gap 1 enhancements (configurable token budgets, advanced iteration control). The `max_iterations` field is included in `AgentTask` but budget enforcement is deferred.
- Write-side tools (Gap 2), task board (Gap 3), or dynamic composition (Gap 5) from the RFC. Those build on top of this foundation.
- Multi-model orchestration within a single workflow. The agent owns model selection.
- Streaming output from the agent runtime.

## Design Considerations

- The `agent` block in YAML is the user-facing contract. Runtime authors implement `AgentProtocol` and register in `AgentRegistry`.
- `model` block backward compatibility ensures existing workflows continue to work without modification during migration.
- Claude Code's `--print` mode with `--output-format json` maps cleanly to the existing structured output model.

## Repository Standards

- Pydantic v2 models for data classes (`AgentBlock`, `AgentTask`, `AgentResult`).
- PEP-544 `@runtime_checkable` protocols for `AgentProtocol`.
- `ruff` lint, `mypy --strict` type checking.
- Test markers: `@pytest.mark.unit` for protocol/parsing tests, `@pytest.mark.docker` for Docker integration.

## Technical Considerations

- **Claude Code CLI dependency**: ClaudeCodeAgent requires `claude` on PATH. The preflight check validates this. In Docker, the image must include Claude Code.
- **Subprocess management**: ClaudeCodeAgent uses `subprocess.run()` (or `Popen` for timeout control) to invoke `claude`. Stdin carries the task description, stdout carries the result.
- **Output parsing**: Claude Code's `--output-format json` produces structured JSON. The parser must handle both structured and plain-text responses gracefully.
- **AgentExecutor removal**: After this spec, `AgentExecutor` and the `llm/` package (`LLMClient`, `AnthropicProvider`) become unused. They should be marked as deprecated but not deleted in this spec — removal is a separate cleanup task.
- **Docker image requirements**: The safety block's `sandbox.base` image must have the agent runtime installed. A standard `agentry-sandbox` image with Claude Code pre-installed should be documented.
- **Environment variables**: ClaudeCodeAgent passes `ANTHROPIC_API_KEY` to the subprocess. In Docker, the runner mounts it as a container env var.

## Security Considerations

- `ANTHROPIC_API_KEY` must be passed to the agent subprocess securely — via environment variable, not command-line argument.
- In Docker, the API key is injected as a container environment variable, not baked into the image.
- The tool manifest enforcement remains in the SecurityEnvelope — the agent receives only allowed tool names.
- Claude Code's own safety controls (permission system) operate independently within the agent runtime.

## Success Metrics

- All existing workflow definitions (`code-review.yaml`, `triage.yaml`, `bug-fix.yaml`, `task-decompose.yaml`, `planning-pipeline.yaml`) execute successfully with the new architecture.
- `SecurityEnvelope` has zero direct dependencies on `AgentExecutor` or `LLMClient`.
- A new agent runtime can be added by implementing `AgentProtocol` and registering in `AgentRegistry` — no changes to runners, envelope, or CLI.
- Test suite passes with no regressions.

## Open Questions

1. Should Claude Code's `--allowedTools` flag be used to enforce the tool manifest at the agent level, or is envelope-level enforcement sufficient?
2. What is the standard Docker base image for Claude Code? Should Agentry publish one, or document how to build it?
3. How should the agent runtime communicate tool invocation records back to Agentry for the execution record? Claude Code's JSON output may not include granular tool-use history.
