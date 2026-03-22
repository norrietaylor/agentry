# RFC: Self-Hosting — Using Agentry to Develop Agentry

## Status

Active — Phase 5 (Agent Runtime) complete. Remaining gaps: write tools, task board, human interaction, dynamic composition, role protocols.

## Context

Agentry was built using the claude-workflow plugin, which provides a full spec → plan → dispatch → implement → validate pipeline. The goal is to replace claude-workflow with Agentry itself, making Agentry a self-hosting development tool.

The original Agentry architecture made direct LLM API calls via `AgentExecutor` → `LLMClient`. Phase 5 replaced this with a four-layer model: **Agentry** (orchestration) → **Runner** (execution environment) → **Agent** (coding agent runtime) → **Model** (LLM). Runners now own agent execution, and `ClaudeCodeAgent` delegates to the Claude Code CLI rather than making raw API calls.

This document identifies the remaining capabilities that Agentry needs to support self-hosting, and proposes how each fits into the existing architecture.

---

## ~~Gap 1: Multi-Turn Agent Execution Loop~~ — RESOLVED (Phase 5)

### Resolution

Phase 5 introduced the four-layer architecture: Agentry → Runner → Agent → Model. The multi-turn execution loop is now handled by the agent runtime itself (Claude Code), not by Agentry's executor. `ClaudeCodeAgent` delegates to `claude -p` which runs its own agentic loop with tool use, iteration, and reasoning.

The `AgentTask` model includes `max_iterations` and `timeout` fields that are passed to the agent runtime. Token budget management is deferred — the agent runtime manages its own token usage.

`AgentExecutor` and the `LLMClient` layer are now deprecated (retained for backward compatibility but unused in the active code path).

---

## Gap 2: Write-Side Tools

### What exists today

Two tools: `repository:read` (file contents with path traversal protection) and `shell:execute` (read-only command allowlist: git log/diff/show/blame, ls, find, grep, cat, head, tail, wc).

### What is needed

Agents that develop software need to modify the codebase:

| Tool | Description | Safety controls |
|------|-------------|-----------------|
| `file:write` | Create or overwrite a file | Path must be within allowed write paths (safety.filesystem.write) |
| `file:edit` | Apply a targeted string replacement to a file | Same path restrictions. Fails if old_string is not found or not unique. |
| `file:delete` | Remove a file | Path restrictions. Only within write paths. |
| `shell:execute-rw` | Execute arbitrary shell commands | Requires `trust: elevated`. Logged in execution record. Timeout enforced. |
| `git:commit` | Stage files and create a commit | Path restrictions on staged files. Commit message captured in execution record. |
| `git:branch` | Create or switch branches | Branch name validation. Cannot force-push or delete protected branches. |

### Design considerations

- Write tools require explicit declaration in the workflow's tool manifest. An agent that declares only `repository:read` cannot write files, regardless of trust level.
- The safety block's `filesystem.write` patterns control which paths are writable. An agent with `file:write` but `write: ["src/**"]` cannot write to `tests/` or `.github/`.
- `shell:execute-rw` is distinct from `shell:execute`. The read-only variant remains the default. The read-write variant is opt-in and requires elevated trust.
- All write operations are logged in the execution record with before/after snapshots (for file edits) or full content (for file creates).
- The `file:edit` tool uses the same semantics as targeted string replacement: provide `old_string` and `new_string`, fail if `old_string` is ambiguous.

### Relationship to PRD/Backlog

PRD Section 4.1 mentions "file read/write" in the tool manifest. Backlog has `file:write` as a deferred item. `git:commit`, `file:edit`, and `shell:execute-rw` are new.

---

## Gap 3: Task Board

### What exists today

The composition engine executes a static DAG of workflows. Node status (completed, failed, skipped, not_reached) is recorded in the `CompositionRecord`, but there is no mutable task state that agents can query or update during execution.

### What is needed

A `TaskBoard` — a persistent, mutable data structure that tracks work items with dependencies, status, ownership, and metadata. Agents can:

- Query the board: "What tasks are unblocked and unowned?"
- Claim a task: set status to `in_progress`, assign ownership
- Update a task: mark complete, add comments, create sub-tasks
- Create tasks: dynamically add work items discovered during execution

```yaml
# Task schema
task:
  id: string
  subject: string
  description: string
  status: pending | in_progress | completed | failed
  owner: string | null
  blocked_by: [task_id, ...]
  blocks: [task_id, ...]
  metadata: {}
```

### Design considerations

- The task board is a new primitive, not a composition enhancement. Composition orchestrates a fixed DAG of workflows. The task board orchestrates mutable work items that may be created, modified, or cancelled at runtime.
- Storage: JSON file on disk (`.agentry/taskboard.json`) for local execution. CI execution would need an external store or artifact-based persistence.
- Concurrency: multiple agents may query/update the board simultaneously. Use file locking or optimistic concurrency (read-modify-write with version check).
- The task board is exposed to agents as tools: `taskboard:list`, `taskboard:get`, `taskboard:update`, `taskboard:create`. These tools are bound by the environment binder like any other tool.
- A dispatcher workflow can read the board, identify unblocked tasks, and spawn composition nodes dynamically — bridging the task board with the composition engine.

### Relationship to PRD/Backlog

Not mentioned. This is a new concept. The closest PRD analog is the composition graph's per-node status map, but that is read-only and static.

---

## Gap 4: Human Interaction Mid-Execution

### What exists today

Workflows are non-interactive. Inputs are provided at invocation time. There is no mechanism to pause execution, ask the user a question, and resume with their answer.

### What is needed

A `human:ask` tool that pauses agent execution, presents a question to the user (via terminal prompt or webhook), and resumes when the user responds.

```
Agent calls: human:ask(question="This feature touches auth. Split into separate PR?", options=["Yes", "No"])
Terminal shows: [Agentry] Agent 'planner' is asking: This feature touches auth. Split into separate PR? [Yes/No]
User types: Yes
Agent receives: "Yes" as tool result
Agent continues execution
```

### Design considerations

- In local/TTY execution: prompt on stdin, block until response.
- In CI execution: post a comment on the PR/issue, create a `workflow_dispatch` event, or block the job pending manual approval (GitHub Actions environments support this).
- Timeout: if no response within a configurable window, the agent receives a timeout result and must handle it (fall back to a default, abort, etc.).
- This is the mechanism for the "human approval gates" mentioned in the PRD (Section 8.1) and backlog. The gate is implemented as a tool call, not a composition-level primitive.
- The `human:ask` tool is optional. Workflows that don't declare it run fully autonomously.

### Relationship to PRD/Backlog

PRD Section 8.1 describes human approval gates for irreversible actions. Backlog has "Human approval gates — pause composition at irreversible actions, require human confirmation." The proposed `human:ask` tool generalizes this from a composition gate to an agent-level capability.

---

## Gap 5: Dynamic Composition

### What exists today

The composition DAG is declared at parse time. Nodes, edges, and failure policies are fixed. The engine schedules nodes based on the static graph.

### What is needed

Runtime DAG mutation — the ability to add, skip, or reorder nodes based on information discovered during execution. Key scenarios:

1. **Task-driven dispatch**: A dispatcher agent reads the task board, identifies N unblocked tasks, and spawns N composition nodes — one per task. The graph shape depends on the task board state, not a static declaration.

2. **File-conflict-aware batching**: Before dispatching parallel nodes, analyze which files each node will modify. If two nodes touch the same file, serialize them. This requires runtime knowledge (task descriptions, file scope) that isn't available at parse time.

3. **Adaptive retry**: If a node fails because of a merge conflict (not a logic error), automatically rebase and retry — a runtime decision that modifies the execution plan.

### Design considerations

- The static composition engine remains unchanged. Dynamic composition is a separate execution mode, not a modification of the existing engine.
- A `DynamicCompositionEngine` accepts a dispatcher workflow (not a static DAG). The dispatcher workflow reads the task board, decides what to run, and uses a `composition:spawn` tool to launch nodes.
- The dispatcher is itself an agent running in the agentic loop (Gap 1). It makes decisions, spawns work, monitors results, and iterates.
- This converges the composition engine with the task board: the dispatcher is the bridge between mutable task state and workflow execution.

### Relationship to PRD/Backlog

Backlog has "Dynamic composition — runtime-determined DAG shapes." The PRD does not address this. The file-conflict batching and adaptive retry scenarios are new.

---

## Gap 6: Role Protocols

### What exists today

Workflows are generic. A workflow has an identity, inputs, tools, model config, safety, and output. There is no structural distinction between a "researcher" workflow and an "implementer" workflow — they are all just workflows.

### What is needed

Role-specific workflow templates with structured contracts that define how agents hand off work between phases. The development lifecycle has distinct roles:

| Role | Inputs | Outputs | Key tools |
|------|--------|---------|-----------|
| **Researcher** | Topic, codebase | Structured research report | `repository:read`, `shell:execute` |
| **Spec Writer** | Research report, user requirements | Specification with demoable units | `repository:read`, `human:ask`, `file:write` |
| **Planner** | Specification | Task graph (task board entries) | `repository:read`, `taskboard:create` |
| **Dispatcher** | Task board state | Worker assignments | `taskboard:list`, `taskboard:update`, `composition:spawn` |
| **Implementer** | Task description, codebase | Code changes + proof artifacts | `repository:read`, `file:write`, `file:edit`, `shell:execute-rw`, `git:commit` |
| **Validator** | Specification, implementation | Validation report | `repository:read`, `shell:execute` |
| **Reviewer** | Changed files, specification | Review findings | `repository:read` |

### Design considerations

- Roles are not a new primitive — they are workflow definitions that follow a convention. A "researcher" is a workflow whose output schema matches the research report structure. A "planner" is a workflow that creates task board entries.
- The handoff contract is defined by the output schema of one role matching the input contract of the next. This is already how composition data passing works.
- A standard library of role workflows ships with Agentry, alongside the existing `code-review.yaml`, `triage.yaml`, etc. These are opinionated defaults, not framework requirements.
- The development lifecycle is itself a composed workflow: `researcher → spec-writer → planner → dispatcher → [implementer, implementer, ...] → validator → reviewer`.
- System prompt templates for each role encode the protocol (e.g., the implementer's 11-phase execution protocol). These are prompt engineering, not framework features.

### Relationship to PRD/Backlog

Not mentioned. The PRD describes generic workflows. Role protocols are a domain-specific application of workflows + composition + task board.

---

## Implementation Sequence

The gaps have dependencies. Gap 1 is resolved.

```
Gap 1 (Agent Runtime) ✅ ────────────────┐
                                          ├─→ Gap 6 (Role Protocols)
Gap 2 (Write Tools) ─→ Gap 3 (Task Board)┘
                           │
                           ├─→ Gap 5 (Dynamic Composition)
                           │
Gap 4 (Human Interaction) ─┘
```

**Phase 5**: Agent Runtime ✅ COMPLETE
- Introduced `AgentProtocol`, `ClaudeCodeAgent`, `AgentRegistry`
- Runners own agent execution (Runner → Agent → Model)
- `SecurityEnvelope` unified — no more direct executor dependency
- Workflow YAML `agent` block replaces `model` block (with backward compat)
- `DockerRunner` updated to run agents inside containers
- See: `docs/specs/06-spec-agent-runtime/`

**Phase 6**: Write Tools + Task Board + Human Interaction
- Add `file:write`, `file:edit`, `shell:execute-rw`, `git:commit` tools
- Implement `TaskBoard` model and persistence
- Add `taskboard:*` tools
- Add `human:ask` tool with TTY and CI backends
- This enables planning and dispatching workflows
- Note: Write tools are less critical now since Claude Code (the agent runtime) has its own file editing capabilities. The Agentry-level tools are needed for the tool manifest enforcement and audit trail.

**Phase 7**: Dynamic Composition + Role Protocols
- Implement `DynamicCompositionEngine` with dispatcher-driven execution
- Create standard library role workflows (researcher, planner, implementer, validator)
- Create the meta-workflow: `agentry develop` — the full development lifecycle as a composed workflow
- At this point, Agentry can develop itself

---

## Success Criteria

Agentry is self-hosting when:

1. `agentry run workflows/develop.yaml --input spec=docs/specs/05-spec-feature.md` produces a working implementation with tests, using only Agentry workflows — no claude-workflow plugin.
2. The implementer agents can modify files, run tests, fix failures, and commit changes.
3. The dispatcher agent can read a task board, identify parallelizable work, and spawn concurrent workers.
4. A human can intervene mid-pipeline to answer clarifying questions or approve irreversible actions.
5. The full pipeline (research → spec → plan → dispatch → implement → validate) runs end-to-end without manual orchestration between phases.
