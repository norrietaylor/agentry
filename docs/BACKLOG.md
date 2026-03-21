# Agentry Backlog

Deferred items collected from Phase 1-4 specs, PRD, and source code.
Items are grouped by category. Each item notes its source and any recommended priority.

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
- [ ] **CI/CD config** — `.github/workflows/` for automated lint, type check, tests
- [ ] **Commit outstanding changes** — uncommitted files from Phase 2/3/4 work
- [ ] **CONTRIBUTING.md** — code style (ruff/mypy), testing, commit conventions
- [ ] **CHANGELOG.md** — document Phase 1-4 in v0.1.0

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

## LLM & Provider Support

- [ ] **OpenAI provider** — extend LLMClient protocol with OpenAI backend _(Phase 1 Non-Goals)_
- [ ] **Multi-model orchestration** — different models for different sub-tasks within a workflow _(PRD Decision Point)_
- [ ] **Streaming output validation** — validate output incrementally instead of blocking _(PRD Decision Point)_

---

## Tool Capabilities

- [x] **PR tools** — `pr:comment`, `pr:review` bound in GitHub Actions binder _(Phase 4)_
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
- [ ] **`.env` pattern in .gitignore** — add `*.env` and `.env*` explicitly
- [ ] **mypy strict errors** — 9 mypy strict errors in `github_actions.py` (consistent with codebase patterns) _(Phase 4 validation)_
