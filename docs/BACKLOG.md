# Agentry Backlog

Deferred items collected from Phase 1-6 specs, PRD, RFC, and source code.
Items are grouped by category. Each item notes its source and any recommended priority.

---

## Phase 6 Completed — Self-Development

Phase 6 (Self-Development) is complete. Agentry can now execute its own workflows, review its own PRs in CI, resolve git-diff inputs, and propose fixes via agent-generated PRs.

- [x] **Wire single-workflow execution** — replaced CLI stub with real RunnerDetector → Runner → Agent pipeline _(Phase 6, T01)_
- [x] **Execution records** — writes to `.agentry/runs/TIMESTAMP/` after each run _(Phase 6, T01.2)_
- [x] **Git-diff input resolution** — `--input diff=HEAD~1` auto-resolves git refs to diff content _(Phase 6, T02)_
- [x] **Self-reviewing PRs in CI** — `.github/workflows/agentry-code-review.yml` runs code-review on every PR _(Phase 6, T03)_
- [x] **`pr:create` tool binding** — both LocalBinder (via `gh` CLI) and GitHubActionsBinder (via REST API) _(Phase 6, T04)_
- [ ] **Issue-triggered triage** — run triage workflow on new GitHub issues _(Phase 6 Non-Goals)_
- [ ] **Scheduled pipeline runs** — backlog grooming via cron-triggered planning-pipeline _(Phase 6 Non-Goals)_
- [ ] **Auto-merge for small fixes** — agent PRs that pass CI and are under N lines _(Phase 6 Non-Goals)_
- [ ] **Token budget enforcement** — per-execution cost caps _(Phase 6 Non-Goals)_
- [ ] **Streaming agent output** — real-time output during execution _(Phase 6 Non-Goals)_

_Source: Phase 6 spec Non-Goals, Open Questions_

---

## Phase 5 Completed — Agent Runtime Refactoring

Phase 5 (Agent Runtime) is complete. The four-layer architecture is in place: Agentry → Runner → Agent → Model. These items were deferred from the spec or emerged during implementation.

- [x] **AgentProtocol** — PEP-544 runtime-checkable protocol for agent runtimes _(Phase 5)_
- [x] **ClaudeCodeAgent** — Claude Code CLI backend via `claude -p` _(Phase 5)_
- [x] **AgentRegistry** — maps runtime names to factory functions _(Phase 5)_
- [x] **Runner-Agent integration** — runners own agent execution, InProcessRunner delegates to agent _(Phase 5)_
- [x] **DockerRunner agent support** — runs Claude Code inside container, updated shim _(Phase 5)_
- [x] **SecurityEnvelope cleanup** — unified RunnerProtocol, removed executor dependency _(Phase 5)_
- [x] **Workflow `agent` block** — replaces `model` block with backward compat _(Phase 5)_
- [ ] **Additional agent runtimes** — Open Code, Aider, Ollama-based agents _(Phase 5 Non-Goals)_
- [ ] **Token budget enforcement** — configurable token budget per agent execution _(Phase 5 Non-Goals, RFC Gap 1)_
- [ ] **Streaming agent output** — stream agent runtime output to CLI in real-time _(Phase 5 Non-Goals)_
- [x] **Remove AgentExecutor and LLM layer** — deleted `executor.py` and `llm/` package, relocated `ExecutionRecord` to `models/execution.py` _(Phase 5 cleanup)_
- [ ] **Standard Docker image with Claude Code** — publish `agentry-sandbox` base image _(Phase 5 Open Q)_
- [ ] **Agent tool invocation records** — capture granular tool-use history from agent runtimes _(Phase 5 Open Q)_
- [ ] **Claude Code `--allowedTools` integration** — enforce tool manifest at agent level _(Phase 5 Open Q)_

_Source: Phase 5 spec Non-Goals, Open Questions, Technical Considerations_

---

## Phase 4 Completed — Remaining CI/Binder Enhancements

Phase 4 (GitHub Actions binder & CI generation) is complete. These items were deferred from that spec or emerged during implementation.

- [ ] **Check annotations** — map structured findings to inline GitHub check annotations on changed files _(Phase 4 Non-Goals)_
- [ ] **CI artifact uploads** — upload execution records/outputs as GitHub Actions artifacts _(Phase 4 Non-Goals)_
- [ ] **Composed workflow CI generation** — multi-job pipelines from composition DAGs _(Phase 4 Non-Goals)_
- [ ] **Custom runner OS selection** — `--runs-on` flag for `ci generate` _(Phase 4 Open Q)_
- [ ] **GitHub App authentication** — support GitHub App installation tokens in addition to `GITHUB_TOKEN` _(Phase 4 Non-Goals)_
- [ ] **Workflow dispatch inputs UI** — `workflow_dispatch` trigger with custom input parameters in GitHub UI _(Phase 4 Non-Goals)_
- [ ] **Issue-tracker tools** — `issue:create`, `issue:comment`, `issue:label` tool bindings _(Phase 4 Non-Goals)_
- [ ] **GitHub API rate limiting** — retry-with-backoff for 429 responses in binder API calls _(Phase 4 Open Q)_
- [ ] **GitLab CI binder** — second `EnvironmentBinder` implementation _(PRD Decision Point)_
- [ ] **Jenkins binder** — third `EnvironmentBinder` implementation _(PRD Decision Point)_
- [ ] **API semantics translation** — pagination, rate limits, error mapping per provider _(PRD Section 4.2)_

_Source: Phase 4 spec Non-Goals, Open Questions, PRD Section 4.2_

---

## Public Release Blockers

- [x] **README.md** — installation, quick start, usage examples
- [x] **LICENSE file** — Apache 2.0
- [x] **.gitignore** — added `.env` patterns
- [x] **CI/CD config** — `.github/workflows/ci.yml` for lint, type check, tests
- [x] **Commit outstanding changes** — all committed and pushed
- [x] **CONTRIBUTING.md** — code style (ruff/mypy), testing, commit conventions
- [x] **CHANGELOG.md** — documents Phases 1-5 in v0.1.0

_Source: repo readiness assessment_

---

## Composition Enhancements

- [ ] **Composition-level timeout** — in addition to per-node timeouts _(Phase 3 Open Q)_
- [ ] **Parallel node limit** — configurable cap on concurrent nodes, default 3 _(Phase 3 Open Q)_
- [ ] **Git worktree parallelism** — each parallel composition node gets its own worktree copy of the repo _(PRD Section 4.2, Phase 1 Non-Goals)_
- [ ] **Dynamic composition** — runtime-determined DAG shapes (e.g., "run N agents based on input count") _(Phase 3 Non-Goals)_
- [ ] **Composition nesting** — a composition node that is itself a composed workflow _(Phase 3 Non-Goals)_
- [ ] **Cross-repo composition** — remote workflow references, not just local file paths _(Phase 3 Non-Goals)_
- [ ] **Human approval gates** — pause composition at irreversible actions, require human confirmation _(Phase 3 Non-Goals, PRD Section 8.1)_

---

## Security & Sandbox Enhancements

- [ ] **Bubblewrap (Tier 2) sandbox** — Linux process-level isolation without Docker _(Phase 2 Non-Goals)_
- [ ] **Sigstore keyless signing** — replace/supplement Ed25519 local keys with Sigstore _(Phase 2 Non-Goals)_
- [ ] **Dependency layer caching** — `sandbox.dependencies` block execution, automated layer building _(Phase 2 Non-Goals)_
- [ ] **Container image publishing** — publish base sandbox image to a registry _(Phase 2 Non-Goals)_
- [ ] **iptables-based egress filtering** — IP-level network control as alternative to DNS proxy _(Phase 2 Non-Goals)_
- [ ] **Docker AI Sandbox integration** — future `DockerSandboxRunner` if/when programmatic API ships _(Phase 2 Tech Considerations)_

---

## Agent Runtimes & LLM Support

- [ ] **Open Code agent** — `AgentProtocol` implementation for Open Code _(Phase 5 Non-Goals)_
- [ ] **Aider agent** — `AgentProtocol` implementation for Aider _(Phase 5 Non-Goals)_
- [ ] **Ollama agent** — `AgentProtocol` implementation for local Ollama models _(Phase 5 Non-Goals)_
- [ ] **OpenAI provider** — extend LLMClient protocol with OpenAI backend _(Phase 1 Non-Goals, may be superseded by agent runtimes)_
- [ ] **Multi-model orchestration** — different models for different sub-tasks within a workflow _(PRD Decision Point)_
- [ ] **Streaming output validation** — validate output incrementally instead of blocking _(PRD Decision Point)_

---

## Tool Capabilities

- [x] **PR tools** — `pr:comment`, `pr:review`, `pr:create` bound in both binders _(Phase 4, Phase 6)_
- [ ] **Issue tools** — `issue:create`, `issue:comment`, `issue:label` _(Phase 4 Non-Goals)_
- [ ] **File write tools** — `file:write` beyond output directory _(Phase 1 Non-Goals)_
- [ ] **Custom side-effect plugin interface** — domain-specific side effects beyond the fixed allowlist _(PRD Decision Point)_

---

## Workflow Authoring

- [ ] **System prompt templating** — variable interpolation in system prompts (e.g., `{{codebase_language}}`) _(Phase 1 Open Q)_
- [ ] **Workflow module system** — reusable fragments for workflows >200 lines _(PRD Decision Point)_
- [ ] **Cross-repo workflow registry** — share workflows across repositories _(PRD Decision Point)_
- [ ] **`agentry registry list`** — workflow discovery command (stub exists in CLI) _(Phase 1 Non-Goals)_

---

## Operational

- [ ] **Execution record retention policy** — configurable cleanup of old records _(Phase 1 Open Q)_
- [ ] **Execution record querying** — local SQLite or observability integration for cross-run queries _(PRD Decision Point)_
- [ ] **Remote execution binder** — offload workflow execution to remote compute _(PRD Decision Point)_
- [ ] **Organization-wide agent governance** — approval workflows, policy validation _(PRD Decision Point)_
- [ ] **Lightweight sandbox alternatives** — Wasm, gVisor, Firecracker when Docker unavailable _(PRD Decision Point)_

---

## Source Code Cleanup

- [ ] **Remove stale phase references** — comments referencing "Phase 2" / "Phase 3" / "Phase 4" in code that's now implemented
- [x] **CLI `ci` stub** — replaced with full `ci generate` subcommand group _(Phase 4)_
- [ ] **CLI `registry` stub** — still prints "Not yet implemented"
- [ ] **Graceful fallback stubs** — several try/except ImportError blocks in cli.py for components that now exist
- [x] **`.env` pattern in .gitignore** — `.env`, `.env.*`, `*.env` patterns present; `agentry-prd.md` added
- [x] **mypy strict errors** — resolved all 37 mypy errors across 9 files; mypy now passes cleanly _(Phase 4 validation)_
- [x] **Delete `executor.py` and `llm/` package** — dead code removed in Phase 5 cleanup
- [x] **Remove duplicate RunnerProtocol** — unified in Phase 5 SecurityEnvelope cleanup
