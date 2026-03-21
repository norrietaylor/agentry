# 03-spec-agentry-composition

## Introduction/Overview

Phase 3 adds the multi-agent composition engine to Agentry. It extends the existing `CompositionBlock` model (currently parsed but not executed) into a full DAG execution engine that schedules composition nodes using `asyncio` and `graphlib.TopologicalSorter`, provisions per-node runners via the `RunnerProtocol`, passes data between nodes through file-based output, and enforces three failure policies (abort, skip, retry). The `agentry run` command is extended to detect composition blocks and dispatch through the DAG engine, making composed workflows a first-class execution mode with the same CLI entry point as single-agent workflows.

## Goals

1. **Execute composition DAGs with dependency ordering**: Schedule and execute multi-agent workflows as directed acyclic graphs, respecting `depends_on` edges and running independent nodes concurrently via `asyncio`.
2. **Enforce three failure policies**: Implement `abort` (halt composition on failure), `skip` (propagate failure object to downstream nodes), and `retry` (re-execute with configurable max retries and fallback policy) per the PRD specification.
3. **Isolate composition nodes**: Each node in a composition gets its own runner provisioned via `RunnerProtocol`, enforcing the PRD's zero-trust-between-agents principle. No shared state, no shared filesystem, no implicit communication.
4. **Pass data between nodes via files**: Upstream node output is written to the run directory and the output path is passed as a resolved input to downstream nodes. Data flows only through declared edges.
5. **Integrate composed workflows into the CLI**: `agentry run` detects a non-empty composition block and dispatches through the DAG engine transparently. Single-agent and composed workflows share the same command.

## User Stories

- As a **developer**, I want to run `agentry run workflows/planning-pipeline.yaml` and have the composition engine execute triage, decomposition, and assignment agents in dependency order, so that I get an end-to-end planning result from a single command.
- As a **workflow author**, I want to declare `depends_on` edges and failure policies in my composition block, so that I can control how agents are sequenced and how failures propagate.
- As a **developer**, I want independent composition nodes to run concurrently, so that multi-agent pipelines complete faster when nodes have no data dependencies.
- As a **developer**, I want to see a per-node status map in the execution record, so that I can diagnose which node failed and which nodes were skipped or never reached.
- As a **workflow author**, I want to use `retry` failure policy with a fallback, so that transient LLM failures don't halt my entire pipeline.

## Demoable Units of Work

### Unit 1: Composition Model Extension & DAG Validation

**Purpose:** Extend the existing `CompositionBlock` and `CompositionStep` Pydantic models to support the full PRD composition specification (failure policies, data-passing edges, input/output mappings). Validate the composition graph as a DAG at parse time.

**Functional Requirements:**
- The system shall extend `CompositionStep` with: `id` field (string, unique within composition), `failure` field containing `mode` (enum: `abort` | `skip` | `retry`, default `abort`), `max_retries` (int, default 1, used only when mode is `retry`), `fallback` (enum: `abort` | `skip`, default `abort`, used only when mode is `retry`), and `inputs` mapping (dict of input-name to source expression, e.g. `{"issues": "triage.output"}`).
- The system shall validate at parse time that the composition graph is a DAG (no cycles) using `graphlib.TopologicalSorter`. A cycle shall produce a clear error message identifying the cycle path.
- The system shall validate that all `depends_on` references resolve to existing node `id` values within the same composition. Unknown references shall produce a validation error.
- The system shall validate that all `inputs` source expressions reference valid upstream node IDs and that the referenced nodes are in the `depends_on` list (implicit or explicit).
- The system shall maintain backward compatibility: existing composition blocks with only `name`, `workflow`, and `depends_on` fields must continue to parse. The `name` field is treated as `id` when `id` is not present.

**Proof Artifacts:**
- Test: `tests/unit/test_composition_model.py` passes — demonstrates model parsing with failure policies, input mappings, DAG validation, cycle detection, and backward compatibility.
- CLI: `agentry validate workflows/planning-pipeline.yaml` validates a composed workflow without errors.

---

### Unit 2: DAG Execution Engine

**Purpose:** Implement the async DAG execution engine that schedules composition nodes respecting dependency order, runs independent nodes concurrently, and manages per-node runner lifecycle.

**Functional Requirements:**
- The system shall implement a `CompositionEngine` class in `src/agentry/composition/engine.py` that accepts a validated `CompositionBlock`, a `RunnerDetector`, and an `EnvironmentBinder`.
- The system shall use `asyncio` and `graphlib.TopologicalSorter` to schedule nodes. Nodes whose dependencies are all satisfied are dispatched concurrently. The engine awaits completion of each batch before dispatching the next.
- The system shall provision a separate runner for each composition node via `RunnerDetector.select()` and `runner.provision()`. Each node's runner is torn down in a `finally` block after the node completes (success or failure).
- The system shall execute each node by: (a) loading the node's workflow definition from disk, (b) resolving its inputs via the binder (with upstream outputs injected as additional provided values), (c) running the setup phase (SecurityEnvelope), (d) executing the agent via the runner, (e) validating output through the three-layer pipeline.
- The system shall write each node's output to `<run_dir>/<node_id>/result.json`. The output path is passed to downstream nodes as a resolved input.
- The system shall produce a `CompositionRecord` containing: a per-node status map (`node_id` → `completed` | `failed` | `skipped` | `not_reached`), per-node execution records, the overall composition status (`completed` | `failed` | `partial`), and wall-clock timing for the full composition.
- The system shall save the `CompositionRecord` to `.agentry/runs/<timestamp>/composition-record.json`.

**Proof Artifacts:**
- Test: `tests/unit/test_composition_engine.py` passes — demonstrates DAG scheduling with mock runners: sequential chain (A→B→C), parallel fan-out (A→[B,C]→D), and single-node degenerate case.
- Test: `tests/unit/test_composition_record.py` passes — demonstrates composition record generation with per-node status map.
- File: `.agentry/runs/<timestamp>/composition-record.json` contains per-node status map and timing.

---

### Unit 3: Failure Policies

**Purpose:** Implement the three failure policy modes (abort, skip, retry) that control how composition node failures propagate through the DAG.

**Functional Requirements:**
- The system shall implement `abort` policy: when a node fails with `mode: abort`, the engine halts the entire composition. No downstream nodes execute. All nodes not yet reached are marked `not_reached` in the composition record. The composition status is `failed`.
- The system shall implement `skip` policy: when a node fails with `mode: skip`, the engine produces a structured `NodeFailure` object containing `node_id`, `error`, and `partial_output` (if any). This object is passed to downstream nodes in place of the expected output. Downstream nodes receive it as their input and must handle it (the engine does not validate downstream handling — it passes the failure object as-is).
- The system shall implement `retry` policy: when a node fails with `mode: retry`, the engine re-executes the node up to `max_retries` times. Each retry gets a fresh runner (new provision/execute/teardown cycle). If all retries fail, the engine falls through to the `fallback` policy (`abort` or `skip`). Retry count and per-attempt errors are recorded in the composition record.
- The system shall preserve partial results: if a three-node composition fails at node 2 with `abort` policy, node 1's output remains in the run directory and composition record. The engine does not delete successful outputs.
- The system shall log failure policy decisions: "Node 'decompose' failed. Policy: skip. Propagating failure object to downstream nodes: [assign]."

**Proof Artifacts:**
- Test: `tests/unit/test_failure_policies.py` passes — demonstrates all three policies: abort halts downstream, skip propagates failure object, retry re-executes and falls back.
- Test: `tests/unit/test_partial_results.py` passes — demonstrates that successful node outputs are preserved when a downstream node fails.

---

### Unit 4: File-Based Data Passing Between Nodes

**Purpose:** Implement the data flow mechanism where upstream node output is written to disk and downstream nodes receive it as resolved input via declared `inputs` mappings.

**Functional Requirements:**
- The system shall write each node's validated output to `<run_dir>/<node_id>/result.json` after the output passes the three-layer validation pipeline.
- The system shall resolve downstream node `inputs` mappings by replacing source expressions (e.g. `"triage.output"`) with the absolute path to the upstream node's `result.json` file.
- The system shall inject resolved upstream output paths into the downstream node's `provided_values` before calling `binder.resolve_inputs()`. This makes upstream outputs available as standard resolved inputs.
- The system shall validate that a referenced upstream node completed successfully before passing its output path. If the upstream node failed and its failure policy is `skip`, the engine passes the `NodeFailure` JSON path instead.
- The system shall support the `"<node_id>.output"` syntax for referencing the full output, and `"<node_id>.output.<field>"` syntax for referencing a specific field from the upstream JSON output (field extraction reads the JSON and extracts the named key).

**Proof Artifacts:**
- Test: `tests/unit/test_data_passing.py` passes — demonstrates file-based output passing between nodes: full output reference, field extraction, and failure object propagation.
- Test: `tests/integration/test_composition_pipeline.py` passes — demonstrates a three-node pipeline (A→B→C) where each node receives the previous node's output as input and produces its own output.
- File: `<run_dir>/<node_id>/result.json` exists for each completed node after a composition run.

---

### Unit 5: CLI Integration & Composed Workflow Execution

**Purpose:** Extend `agentry run` to detect composition blocks and dispatch through the DAG engine. Add a composed workflow to the standard library that demonstrates multi-agent execution.

**Functional Requirements:**
- The system shall modify the `agentry run` command to detect when a workflow definition has a non-empty `composition.steps` list. When detected, `run` dispatches through the `CompositionEngine` instead of the single-agent execution path.
- The system shall display composition progress to the terminal: node start/complete/fail events, with the existing TTY-aware output formatting (spinner per active node, status lines for completed nodes).
- The system shall produce a combined execution summary after composition completes: overall status, per-node status, total wall-clock time, and per-node timing.
- The system shall support `--node <node_id>` flag on `agentry run` to execute a single node from a composition in isolation (useful for debugging). The node runs with no upstream data and no downstream propagation.
- The system shall include a standard library composed workflow `workflows/planning-pipeline.yaml` that composes `triage.yaml` → `task-decompose.yaml` (new) → a summary step, demonstrating the full composition execution path.
- The system shall include the `workflows/task-decompose.yaml` workflow definition and its prompt template, following the same patterns as existing standard library workflows.

**Proof Artifacts:**
- Test: `tests/unit/test_cli_composition.py` passes — demonstrates that `agentry run` dispatches composed workflows through the engine and single-agent workflows through the existing path.
- CLI: `agentry run workflows/planning-pipeline.yaml --input issues="bug list"` executes the three-node composition and produces combined output.
- CLI: `agentry run workflows/planning-pipeline.yaml --node triage` executes only the triage node in isolation.
- File: `workflows/planning-pipeline.yaml` exists with a valid composition block.
- File: `workflows/task-decompose.yaml` exists with identity, inputs, tools, model, and output blocks.

---

## Non-Goals (Out of Scope for Phase 3)

- **GitHub Actions binder** — CI pipeline generation, GitHub event payload resolution, PR comment/annotation output mapping. This is 04-spec-agentry-ci.
- **`agentry ci generate`** — CI YAML generation command. Deferred to Phase 4.
- **Cross-repo composition** — All workflow references in a composition must be local file paths. Remote workflow references are a future enhancement.
- **Shared state between nodes** — No shared memory, shared filesystem, or side-channel communication between composition nodes. Data flows only through declared edges.
- **Dynamic composition** — The DAG is static and declared at parse time. Runtime-determined graph shapes (e.g., "run N agents based on input count") are a future enhancement.
- **Human approval gates** — The PRD mentions approval gates for irreversible actions in compositions. This requires UI/interaction design not yet specified. Deferred.
- **Composition nesting** — A composition node cannot itself be a composed workflow. Single-level composition only.

## Design Considerations

- **Composition as extension, not replacement**: Single-agent workflows continue to work exactly as before. The composition engine is only activated when `composition.steps` is non-empty.
- **Progressive disclosure**: The composition block is optional. A workflow author starts with single-agent workflows and adds composition when needed. No new concepts are required for single-agent use.
- **Per-node output directories**: Each node writes to its own subdirectory under the run directory. This prevents output collisions between parallel nodes and makes debugging straightforward.
- **TTY-aware composition output**: Active nodes show spinners; completed nodes show status lines. Failure nodes show the error inline. This extends the existing Phase 1 output formatting.

## Repository Standards

- Follows Phase 1/2 conventions: src layout, Pydantic v2 models, Click CLI, pytest, ruff, mypy strict
- New module: `src/agentry/composition/` (engine, record, data_passing)
- Async tests use `pytest-asyncio` (add to dev dependencies if not present)
- Composed workflow library additions follow existing `workflows/` patterns

## Technical Considerations

- **`graphlib.TopologicalSorter`** (stdlib, Python 3.9+): Provides `static_order()` for validation and `prepare()` / `get_ready()` / `done()` for incremental scheduling. Use the incremental API for async dispatch of ready nodes.
- **`asyncio.gather()`**: Run ready nodes concurrently. Each node is an `async` coroutine that provisions a runner, executes, and tears down. Use `return_exceptions=True` to collect failures without canceling siblings (needed for skip policy).
- **Runner lifecycle per node**: Each node gets `runner = detector.select(workflow.safety)` → `ctx = runner.provision()` → `result = runner.execute()` → `runner.teardown()`. This reuses the Phase 2 infrastructure unchanged.
- **SecurityEnvelope per node**: Each node's execution is wrapped in its own SecurityEnvelope, getting independent preflight checks, setup manifests, and output validation. Composition-level orchestration is above the envelope.
- **File-based data passing**: Node output → `result.json` → downstream reads path. This keeps data flow through the existing output validation pipeline (layer 1/2/3) and makes debugging trivial (inspect the JSON files).
- **`NodeFailure` as a serializable dataclass**: When a skip-policy node fails, the failure object is written as JSON at the same output path. Downstream nodes receive this path — they read the JSON and detect the `"_failure": true` sentinel field.
- **Backward compatibility for `CompositionStep`**: The existing `name` field maps to `id` when `id` is not provided. The `workflow` and `depends_on` fields are unchanged. New fields (`failure`, `inputs`) have defaults.

## Security Considerations

- **Zero trust between nodes**: Each node runs in its own SecurityEnvelope with its own runner. Node A cannot access Node B's workspace, tool bindings, or intermediate state. This is enforced by the per-node runner isolation (separate containers for Docker, separate process contexts for InProcessRunner).
- **Composition record sensitivity**: The composition record includes per-node execution records which may contain code snippets or model outputs. Same sensitivity profile as single-agent execution records.
- **Retry and resource consumption**: Retry policy can multiply resource consumption (each retry provisions a new runner). The `max_retries` field bounds this. The engine logs each retry attempt.
- **Failure object content**: `NodeFailure` objects propagated via skip policy contain error messages and partial output. These may include sensitive information from the failed node's execution. They inherit the same access controls as execution records.

## Success Metrics

- A three-node composition (`workflows/planning-pipeline.yaml`) executes end-to-end locally, with the middle node receiving the first node's output and the third node receiving the second node's output.
- Independent nodes in a fan-out composition execute concurrently (verified by wall-clock timing being less than sequential execution time).
- Abort policy halts the composition on first node failure; skip policy propagates failure objects to downstream nodes; retry policy re-executes up to `max_retries` before falling back.
- Per-node status map in the composition record correctly reflects completed, failed, skipped, and not_reached nodes.
- All existing Phase 1 and Phase 2 tests continue to pass (no regressions).
- `agentry run` transparently handles both single-agent and composed workflows.

## Open Questions

1. **Timeout at composition level**: Should there be a composition-level timeout in addition to per-node timeouts? *Recommendation: defer to a follow-up. Per-node timeouts (from each workflow's safety block) are sufficient for now.*
2. **Parallel node limit**: Should the engine cap the number of concurrently executing nodes? *Recommendation: yes, default to 3 concurrent nodes (same as Phase 2 dispatch batch size). Configurable via composition block or CLI flag.*
3. **Node output format**: Should we require a specific JSON structure for node outputs that are passed downstream, or accept arbitrary JSON? *Recommendation: accept arbitrary JSON. The downstream node's input contract is responsible for declaring what it expects.*

## Phase Roadmap

| Phase | Spec | Focus | Status |
|-------|------|-------|--------|
| 1 | 01-spec-agentry-cli | Core foundation | Complete |
| 2 | 02-spec-agentry-sandbox | Security & isolation | Complete |
| **3** | **This spec** | **Multi-agent composition** | **Current** |
| 4 | 04-spec-agentry-ci | GitHub Actions binder & CI generation | Next |
