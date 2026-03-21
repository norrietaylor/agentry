# 01-spec-agentry-cli

## Introduction/Overview

Agentry is a CLI tool that treats agentic workflows as portable, declarative definitions. Phase 1 establishes the core foundation: a YAML-based workflow definition parser, a Click-based CLI with `run` and `validate` commands, single-agent local execution powered by the Anthropic Claude API, a full three-layer output validation pipeline, and a local environment binder that resolves git diffs and repository references. This phase delivers a working tool that can parse workflow definitions, validate them, execute a single agent locally against a real codebase, and validate the agent's output against its declared contract.

## Goals

1. **Parse and validate workflow definitions**: Implement Pydantic v2 models that load YAML workflow definitions and enforce strict schema validation (unknown keys are errors).
2. **Execute single agents locally**: Run a single agent in-process against a local codebase using the Anthropic Claude SDK, resolving abstract inputs (git-diff, repository-ref) from the local filesystem.
3. **Validate agent output end-to-end**: Implement the three-layer output validation pipeline (JSON Schema + side-effect allowlist + output path enforcement) that gates all output before emission.
4. **Ship multiple example workflows**: Deliver code-review, bug-fix, and triage workflow definitions that exercise the parser and serve as reference implementations.
5. **Establish the project foundation**: Set up the Python project structure with pyproject.toml, Click CLI, test infrastructure, and the plugin interface for environment binders.

## User Stories

- As a **developer**, I want to run `agentry validate workflows/code-review.yaml` to check my workflow definition for structural correctness so that I catch errors before execution.
- As a **developer**, I want to run `agentry run workflows/code-review.yaml --input diff=HEAD~1` to execute a code review agent against my local changes so that I get structured feedback without leaving my terminal.
- As a **developer**, I want to see structured JSON output when piping `agentry run` to another process, and human-readable output when running interactively, so that the tool integrates with both human and machine workflows.
- As a **workflow author**, I want to define an output schema in my workflow and have the runtime reject malformed agent output, so that downstream consumers can trust the output structure.
- As a **workflow author**, I want to fork an example workflow from the standard library and customize it for my team's needs, so that I don't start from scratch.

## Demoable Units of Work

### Unit 1: Workflow Definition Parser & Validator

**Purpose:** Establish the core data model that represents workflow definitions. Parse YAML files into typed Pydantic models with strict validation. This is the foundation everything else builds on.

**Functional Requirements:**
- The system shall parse YAML workflow definitions into Pydantic v2 models covering all seven blocks: identity, inputs, tools, model, safety, output, and composition (composition parsed but not executed in Phase 1).
- The system shall reject YAML files with unknown keys at any nesting level and report the unknown key path in the error message.
- The system shall use discriminated unions for input types (`git-diff`, `repository-ref`, `document-ref`) so that each input type carries its own validation rules.
- The system shall validate that `required: true` inputs are present in the input contract and that all `$variable` references in the definition resolve to declared inputs or well-known runtime variables (`$output_dir`, `$codebase`, `$diff`, `$pr_url`).
- The system shall enforce version field presence and validate that the version string follows semantic versioning format.
- The system shall implement the `agentry validate <path>` CLI command that loads, parses, and validates a workflow definition, reporting all errors with file path and location context.
- The system shall exit with code 0 on successful validation and code 1 on validation failure, printing errors to stderr and a success summary to stdout.

**Proof Artifacts:**
- Test: `tests/unit/test_workflow_parser.py` passes — demonstrates parsing of valid workflow YAML into typed models with all seven blocks.
- Test: `tests/unit/test_workflow_validation.py` passes — demonstrates rejection of malformed YAML (unknown keys, missing required fields, invalid types, bad version format).
- CLI: `agentry validate workflows/code-review.yaml` returns exit code 0 with "Validation successful" message.
- CLI: `agentry validate tests/fixtures/invalid-workflow.yaml` returns exit code 1 with structured error output.

---

### Unit 2: CLI Framework & Output Formatting

**Purpose:** Establish the Click-based CLI with the `run` and `validate` subcommands, global options, and TTY-aware output formatting. This is the user-facing shell that all other units plug into.

**Functional Requirements:**
- The system shall implement a Click group with subcommands: `run`, `validate`. Additional commands (`setup`, `ci`, `registry`) shall be registered as stubs that print "Not yet implemented" and exit with code 0.
- The system shall support global options: `--verbose` (increases log level), `--config <path>` (overrides default config location), `--output-format <auto|json|text>` (forces output format).
- The system shall auto-detect TTY when `--output-format auto` (default): emit colored, human-readable output when stdout is a terminal; emit structured JSON when stdout is piped or redirected.
- The system shall implement `agentry run <workflow-path>` with options: `--input <key>=<value>` (repeatable, passes inputs to the workflow), `--target <path>` (sets the working directory for local execution, defaults to cwd).
- The system shall display a progress indicator during LLM calls when running interactively (TTY mode) showing elapsed time and current status.
- The system shall handle keyboard interrupts (Ctrl+C) gracefully, printing a summary of any partial results and exiting with code 130.
- The system shall provide `--help` for every command and subcommand with usage examples.
- The system shall be installable via `pip install -e .` and expose the `agentry` command via the `[project.scripts]` entry point in pyproject.toml.

**Proof Artifacts:**
- CLI: `agentry --help` shows all available commands with descriptions.
- CLI: `agentry validate --help` shows validation-specific options and usage examples.
- CLI: `agentry run workflows/code-review.yaml --input diff=HEAD~1` executes and produces human-readable output to terminal.
- CLI: `agentry run workflows/code-review.yaml --input diff=HEAD~1 | cat` produces JSON output (TTY detection triggers JSON mode when piped).
- Test: `tests/unit/test_cli.py` passes — demonstrates CliRunner tests for all commands, global options, and error handling.

---

### Unit 3: Local Environment Binder & Input Resolution

**Purpose:** Implement the local environment binder that resolves abstract workflow inputs (git-diff, repository-ref) against the local filesystem. This is the translation layer that makes workflow definitions concrete for local execution.

**Functional Requirements:**
- The system shall implement the `EnvironmentBinder` Protocol with methods: `resolve_inputs()`, `bind_tools()`, `map_outputs()`. The `generate_pipeline_config()` method shall raise `NotImplementedError` (CI generation is Phase 3).
- The system shall resolve `git-diff` inputs by running `git diff <ref>` in the target directory using `subprocess.run()`, where `<ref>` comes from the `--input diff=<ref>` CLI argument.
- The system shall resolve `repository-ref` inputs to the absolute path of the target directory (from `--target` or cwd), verifying it is a git repository by checking for `.git/`.
- The system shall bind the `repository:read` tool capability to a concrete implementation that reads files from the resolved repository path, restricting read access to files within the repository root (no path traversal above the repo root).
- The system shall bind the `shell:execute` tool capability to a concrete implementation that executes read-only shell commands (limited to a configurable allowlist: `git log`, `git diff`, `git show`, `git blame`, `ls`, `find`, `grep`, `cat`, `head`, `tail`, `wc`).
- The system shall map outputs to a local results directory: `<target>/.agentry/runs/<timestamp>/` containing the agent's output JSON and an execution record JSON.
- The system shall discover binders via `importlib.metadata.entry_points(group='agentry.binders')` and select the local binder by default when no `--environment` flag is provided.
- The system shall fail with a clear error if the target directory is not a git repository (when `git-diff` or `repository-ref` inputs are declared).

**Proof Artifacts:**
- Test: `tests/unit/test_local_binder.py` passes — demonstrates resolution of git-diff and repository-ref inputs against a test git repository.
- Test: `tests/unit/test_tool_binding.py` passes — demonstrates repository:read returns file contents and rejects path traversal attempts. shell:execute runs allowed commands and rejects disallowed commands.
- CLI: `agentry run workflows/code-review.yaml --input diff=HEAD~1 --target /path/to/repo` resolves inputs from the specified repository.
- File: `.agentry/runs/<timestamp>/execution-record.json` contains resolved input values, tool invocations, and timing.

---

### Unit 4: LLM Integration & Agent Execution

**Purpose:** Implement the provider-abstracted LLM client (Anthropic-only for Phase 1) and the agent execution engine that sends the system prompt, resolved inputs, and tool bindings to the model and collects structured output.

**Functional Requirements:**
- The system shall implement an `LLMClient` Protocol with methods: `call(system_prompt, messages, tools, config) -> LLMResponse` supporting both sync and async invocation.
- The system shall implement an `AnthropicProvider` that uses the Anthropic Python SDK to call Claude models, reading the API key from the `ANTHROPIC_API_KEY` environment variable.
- The system shall construct the LLM call from the workflow definition: system prompt from the model configuration's `system_prompt` field (loaded from the referenced file path), temperature, max_tokens, and model identifier.
- The system shall format resolved inputs (git diff, file contents) as user messages and bind declared tools as Claude tool-use definitions, enabling the agent to invoke repository:read and shell:execute during its reasoning.
- The system shall implement retry logic with exponential backoff as declared in the workflow definition's `model.retry` block (max_attempts, backoff strategy).
- The system shall enforce a per-execution timeout from the safety block's `resources.timeout` field, cancelling the LLM call if it exceeds the limit.
- The system shall collect the agent's final structured output (the last tool-use result or text response) and pass it to the output validation pipeline.
- The system shall record token usage (input tokens, output tokens) and wall-clock timing in the execution record.
- The system shall fail with a clear error if `ANTHROPIC_API_KEY` is not set, suggesting the user set it before running.

**Proof Artifacts:**
- Test: `tests/unit/test_llm_client.py` passes — demonstrates AnthropicProvider call construction with correct parameters (mocked SDK).
- Test: `tests/unit/test_agent_executor.py` passes — demonstrates full execution flow: input formatting, tool binding, LLM call, output collection, retry on failure, timeout enforcement.
- CLI: `agentry run workflows/code-review.yaml --input diff=HEAD~1` produces a structured code review output from Claude.
- File: `.agentry/runs/<timestamp>/execution-record.json` contains token usage, timing, and tool invocation log.

---

### Unit 5: Output Validation Pipeline

**Purpose:** Implement the three-layer output validation that gates all agent output before emission. This is the core safety mechanism that ensures agents produce structurally valid output through permitted channels to declared destinations.

**Functional Requirements:**
- The system shall implement Layer 1 (Schema Validation): validate agent output against the JSON Schema declared in the workflow's `output.schema` block using the `jsonschema` library. Malformed output shall be rejected with a structured error indicating the schema path, failed keyword, and human-readable message.
- The system shall implement Layer 2 (Side-Effect Allowlist): extract side effects attempted by the agent (tool invocations that produce external state changes) and verify each against the `output.side_effects` allowlist. Any undeclared side effect shall be blocked and reported.
- The system shall implement Layer 3 (Output Path Enforcement): verify that all file writes target paths within the declared `output.output_paths` list. Writes to undeclared paths shall be blocked and reported.
- The system shall execute all three layers in sequence: schema first, then allowlist, then paths. Failure at any layer shall halt processing and report the specific layer that failed, the specific check that failed, and a remediation suggestion.
- The system shall produce a structured validation result: `{ validation_status, layer_results: [{ layer, passed, error? }] }` that is included in the execution record.
- The system shall enforce the `output.budget.max_findings` limit when present: if the agent produces more findings than the budget allows, the system shall truncate to the budget limit and include a note in the output indicating truncation occurred.
- The system shall emit validated output to the terminal (TTY mode: human-readable, non-TTY: JSON) and write it to the execution record's output path.

**Proof Artifacts:**
- Test: `tests/unit/test_output_validator.py` passes — demonstrates all three validation layers: schema passes/fails, allowlist passes/blocks, path enforcement passes/blocks.
- Test: `tests/unit/test_output_budget.py` passes — demonstrates truncation when findings exceed max_findings budget.
- Test: `tests/unit/test_validation_pipeline.py` passes — demonstrates sequential layer execution and halt-on-failure behavior.
- CLI: `agentry run workflows/code-review.yaml --input diff=HEAD~1` with a workflow that has strict output schema — validates output before displaying.

---

### Unit 6: Standard Workflow Library

**Purpose:** Deliver three production-grade workflow definitions (code review, bug fix, triage) that exercise the parser, serve as reference implementations, and provide immediate value to users.

**Functional Requirements:**
- The system shall include a `workflows/` directory at the project root containing three workflow definitions: `code-review.yaml`, `bug-fix.yaml`, and `triage.yaml`.
- The `code-review.yaml` workflow shall match the example in PRD Section 4.1: inputs (git-diff, repository-ref, optional document-ref), tools (repository:read), model config (Claude Sonnet, temperature 0.2), output schema (findings array with file/line/severity/category/description, summary, confidence), side-effect allowlist (none — output to terminal only in Phase 1), output budget (max 10 findings).
- The `bug-fix.yaml` workflow shall accept inputs (issue-description: string, repository-ref), use tools (repository:read, shell:execute), and produce output schema (diagnosis, root_cause, suggested_fix with file/line/change, confidence).
- The `triage.yaml` workflow shall accept inputs (issue-description: string, repository-ref), use tools (repository:read), and produce output schema (severity: critical/high/medium/low, category, affected_components array, recommended_assignee, reasoning).
- Each workflow shall include a system prompt file in `workflows/prompts/` referenced by the model configuration's `system_prompt` field.
- Each workflow shall pass `agentry validate` without errors.
- The system shall include a `workflows/README.md` documenting each workflow's purpose, inputs, outputs, and usage examples.

**Proof Artifacts:**
- CLI: `agentry validate workflows/code-review.yaml` returns exit code 0.
- CLI: `agentry validate workflows/bug-fix.yaml` returns exit code 0.
- CLI: `agentry validate workflows/triage.yaml` returns exit code 0.
- CLI: `agentry run workflows/code-review.yaml --input diff=HEAD~1` produces a structured code review with findings, summary, and confidence score.
- CLI: `agentry run workflows/triage.yaml --input issue-description="Login page returns 500 error when email contains a plus sign"` produces a triage classification.
- File: `workflows/README.md` documents all three workflows with usage examples.

---

## Non-Goals (Out of Scope for Phase 1)

- **Docker sandbox execution** — agents run in-process, not in containers. Sandbox isolation is Phase 2.
- **Composition/DAG execution** — composition blocks are parsed but not executed. Multi-agent pipelines are Phase 2.
- **CI pipeline generation** — the `agentry ci generate` command is Phase 3 (GitHub Actions binder).
- **Security Envelope enforcement** — the safety block is parsed and validated but not enforced at runtime (no container, no network isolation). Full enforcement is Phase 2.
- **Workflow definition signing** — Ed25519 signing is Phase 2 (security).
- **Preflight token scope verification** — no CI tokens to verify in Phase 1 (local only).
- **Setup phase / setup manifest** — the mandatory setup gate is Phase 2.
- **Workflow registry** — the `agentry registry list` command is deferred.
- **OpenAI or other LLM providers** — Anthropic only in Phase 1.
- **Write-side tool capabilities** — no `pr:comment`, `issue:create`, `file:write` (beyond output directory). Write tools are Phase 2+.
- **Worktree management for parallel execution** — single agent, single process.

## Design Considerations

- **Terminal output**: Human-readable output uses color coding for severity levels (red=critical, yellow=warning, blue=info). JSON output uses the exact schema from the workflow definition's output contract.
- **Error messages**: All errors include the file path, the specific field/line that failed, and a remediation suggestion. Follow the Rust compiler's error message philosophy: tell the user what went wrong, where, and how to fix it.
- **Progress display**: During LLM calls, show a spinner with elapsed time and "Calling Claude..." status. On completion, show token usage summary.

## Repository Standards

- **Python 3.10+** minimum (for `graphlib`, `importlib.metadata` improvements, `match` statements)
- **pyproject.toml** (PEP 621) as single source of truth
- **src layout**: `src/agentry/` package root
- **ruff** for linting and formatting
- **mypy** in strict mode for type checking
- **pytest** with `tests/unit/` and `tests/integration/` directories
- **Click** for CLI with `CliRunner` for testing
- Commit messages: imperative mood, 50-char subject line

## Technical Considerations

- **Pydantic v2 strict mode**: Use `model_config = ConfigDict(strict=True, extra='forbid')` to reject unknown keys.
- **Discriminated unions**: Input types use `Literal` discriminator field: `type: Literal["git-diff"]`, `type: Literal["repository-ref"]`, etc.
- **YAML loading safety**: Always use `yaml.safe_load()`, never `yaml.load()`.
- **Variable resolution**: `$variable` references in the definition are resolved during environment binding, not during parsing. The parser validates that references exist but does not resolve values.
- **Async support**: The LLM client uses `asyncio` internally for timeout enforcement, but the CLI presents a synchronous interface (Click commands are synchronous; async execution is internal).
- **Execution record format**: JSON file with fields: `workflow_version`, `inputs`, `outputs`, `tool_invocations[]`, `token_usage`, `timing`, `validation_result`, `status`.

## Security Considerations

- **API keys**: `ANTHROPIC_API_KEY` read from environment variable only. Never logged, never included in execution records.
- **Path traversal**: The `repository:read` tool must reject paths that resolve outside the repository root via symlink following or `../` traversal.
- **Shell command allowlist**: The `shell:execute` tool only permits a hardcoded list of read-only commands. No `rm`, `mv`, `cp`, `chmod`, or write operations.
- **Output directory isolation**: Agent output files are written only to `.agentry/runs/<timestamp>/` within the target directory.
- **No network access beyond LLM API**: In Phase 1 (no sandbox), this is advisory. Phase 2 will enforce it.

## Success Metrics

- `agentry validate` correctly validates all three standard workflow definitions without false positives or negatives.
- `agentry run` successfully executes the code-review workflow against a real repository and produces structured output matching the declared schema.
- The three-layer output validation pipeline catches and reports: (a) schema-invalid output, (b) undeclared side effects, (c) writes to undeclared paths.
- All unit tests pass with >80% code coverage across core modules.
- The tool installs cleanly via `pip install -e .` and the `agentry` command is available on PATH.
- End-to-end execution time for a single agent run (excluding LLM latency) is under 2 seconds.

## Open Questions

1. **System prompt templating**: Should system prompts support variable interpolation (e.g., `{{codebase_language}}`, `{{review_focus}}`), or are they static files in Phase 1? *Recommendation: static files for Phase 1, templating in Phase 2.*
2. **Execution record retention**: How many execution records should be retained locally before cleanup? *Recommendation: no automatic cleanup in Phase 1; add configurable retention in Phase 2.*
3. **Tool invocation logging**: Should tool invocations (file reads, shell commands) be logged at the individual call level in the execution record, or just summarized? *Recommendation: individual call level for auditability.*

## Phase Roadmap

| Phase | Spec | Focus | Key Deliverables |
|-------|------|-------|-----------------|
| **1** | **This spec** | Core foundation | Parser, CLI, local execution, output validation, example workflows |
| 2 | 02-spec-agentry-sandbox | Security & isolation | Docker sandbox, Security Envelope, preflight checks, definition signing, setup phase |
| 3 | 03-spec-agentry-composition | Multi-agent & CI | Composition DAG executor, GitHub Actions binder, CI generation, worktree management |
