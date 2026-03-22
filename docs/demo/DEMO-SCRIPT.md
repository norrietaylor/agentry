# Agentry Demo Script

**Duration:** 15-20 minutes
**Audience:** Developers, engineering leads, AI tooling teams
**Setup:** Terminal with `uv` installed, `claude` on PATH, repo cloned locally

---

## Act 1: The Problem (2 min)

**Talking point:** AI agents are powerful but hard to operationalize. You can get Claude Code to review a PR in your terminal, but how do you run that same review on every PR in CI? How do you compose multiple agents into a pipeline? How do you enforce security boundaries?

**Talking point:** Today, teams either build custom GitHub Actions that make raw API calls, or they run agents manually. There's no portable definition that works the same locally and in CI.

**Talking point:** Agentry fixes this. Define once, run everywhere.

---

## Act 2: What is a Workflow? (3 min)

Show the workflow definition:

```bash
cat workflows/code-review.yaml
```

Walk through the blocks:
- **identity** — name, version, description (versionable like any config)
- **agent** — runtime: claude-code, model, system_prompt (the agent runtime, not raw API calls)
- **inputs** — what the agent needs (git-diff, repository-ref)
- **tools** — what the agent can do (repository:read only — least privilege)
- **safety** — timeout, trust level, filesystem controls
- **output** — JSON schema the agent must conform to

**Key point:** This is declarative. The workflow doesn't know if it's running on your laptop or in GitHub Actions.

---

## Act 3: Validate and Run (3 min)

### Validate

```bash
uv run agentry validate workflows/code-review.yaml
```

**Talking point:** Validation catches schema errors, missing fields, invalid compositions before you run anything.

### Run locally

```bash
uv run agentry run workflows/triage.yaml \
  --input issue-description="API endpoint /users returns 500 when email contains unicode characters" \
  --input repository-ref=.
```

**Talking point:** Agentry resolves the inputs, selects the runner (in-process for elevated trust), launches Claude Code as the agent runtime, enforces the tool manifest, validates the output against the schema. All from one command.

---

## Act 4: Security (2 min)

### Sign a workflow

```bash
uv run agentry keygen
uv run agentry sign workflows/code-review.yaml --output /tmp/signed.yaml
```

### Audit

```bash
uv run agentry validate --security-audit workflows/code-review.yaml /tmp/signed.yaml
```

**Talking point:** Workflows are security-critical — they control what an AI agent can access. Signing detects tampering. The security envelope enforces tool manifests: if a workflow declares `repository:read` only, the agent cannot write files, regardless of what it tries.

---

## Act 5: CI Generation (2 min)

```bash
uv run agentry ci generate --target github workflows/code-review.yaml --dry-run
```

**Talking point:** Same workflow, zero new YAML to write. Agentry derives the minimal GitHub token permissions from the tool manifest. `repository:read` → `contents: read`. `pr:comment` → `pull-requests: write`. The generated pipeline installs agentry, runs the workflow, and posts results.

### Multiple triggers

```bash
uv run agentry ci generate --target github \
  --triggers pull_request,schedule \
  --schedule "0 2 * * 1" \
  workflows/code-review.yaml --dry-run
```

**Talking point:** Schedule a weekly deep review alongside PR-triggered reviews. Same workflow, different triggers.

---

## Act 6: Multi-Agent Composition (3 min)

```bash
cat workflows/planning-pipeline.yaml
```

Walk through the composition block:
- triage → task-decompose → summary
- Dependencies, failure policies (retry, skip, abort)

```bash
uv run agentry run workflows/planning-pipeline.yaml \
  --input issue-description="Database connection pool exhaustion under load" \
  --input repository-ref=. \
  --skip-preflight
```

**Talking point:** Independent nodes run concurrently. Failure policies control what happens when an agent fails — retry, skip, or abort the pipeline. Data flows between nodes: triage output feeds into task-decompose.

### Single node debugging

```bash
uv run agentry run workflows/planning-pipeline.yaml --node triage \
  --input issue-description="Test" \
  --input repository-ref=. \
  --skip-preflight
```

**Talking point:** Debug one node in isolation without running the full pipeline.

---

## Act 7: Architecture — The Four Layers (2 min)

Draw or show the diagram:

```
Agentry (orchestration)
  └→ Runner (execution environment)
       └→ Agent (coding agent runtime)
            └→ Model (LLM)
```

**Talking points:**

- **Runners** own the environment. DockerRunner provisions a sandbox with CPU/memory limits, filesystem mounts, network isolation. InProcessRunner runs on the host.
- **Agents** own the intelligence. ClaudeCodeAgent delegates to Claude Code CLI. The agent decides how to use tools, how many turns to take, when to stop. Agentry doesn't micromanage the agent — it provides boundaries.
- **Models** are the agent's choice. The workflow declares a model preference, but the agent runtime handles API calls, authentication, retries. Agentry never makes direct LLM API calls.

**Key point:** Adding a new agent runtime (Open Code, Aider) means implementing one protocol — `AgentProtocol`. No changes to runners, security, or CLI.

---

## Act 8: The Vision — Self-Hosting (2 min)

**Talking point:** Agentry was built with the claude-workflow plugin — an external tool that manages specs, planning, dispatching, and implementation. The goal is to replace that plugin with Agentry itself.

**What's done (Phases 1-5):**
- Workflow parsing, validation, CLI
- Security envelope, signing, preflight
- Composition engine (DAG scheduling)
- CI generation (GitHub Actions)
- Agent runtime abstraction (Runner → Agent → Model)

**What's next (Phases 6-7):**
- Write-side tools (file:write, git:commit) — agents that modify code
- Task board — mutable work tracking across agents
- Human interaction — pause for approval mid-pipeline
- Dynamic composition — runtime-determined DAG shapes
- Role protocols — researcher, planner, implementer, validator as workflows

**Talking point:** When Phase 7 is done, `agentry run workflows/develop.yaml --input spec=docs/specs/07-spec-feature.md` will run the full development lifecycle: research → spec → plan → dispatch → implement → validate. Agentry developing itself.

---

## Closing (1 min)

**Talking point:** Agentry is open source (Apache 2.0), Python 3.10+, and designed to be extended. The workflow definition is the unit of portability. The agent runtime is pluggable. The security model is least-privilege by default.

```
github.com/norrietaylor/agentry
```

---

## Backup Demos (if time permits)

### Docker sandbox

```bash
uv run agentry setup workflows/code-review.yaml
# Shows preflight checks including Docker availability
```

### Binder registry

```bash
uv run python -c "from agentry.binders.registry import discover_binders; print(list(discover_binders().keys()))"
# Shows: ['local', 'github-actions']
```

### Agent registry

```bash
uv run python -c "from agentry.agents.registry import AgentRegistry; print(list(AgentRegistry.default().list_runtimes()))"
# Shows: ['claude-code']
```

### Test suite

```bash
uv run pytest tests/ --tb=short -q 2>&1 | tail -3
# Shows: 1600+ passed
```
