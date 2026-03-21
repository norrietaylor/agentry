# 03-spec-agentry-composition — Clarifying Questions (Round 1)

## Scope
**Q:** Phase 3 covers three major areas. How should we scope this?
**A:** Split into two specs. 03-spec: Composition DAG engine. 04-spec: GitHub Actions binder + CI generation.

## Concurrency
**Q:** asyncio or subprocess-based parallelism?
**A:** asyncio + graphlib.TopologicalSorter for DAG scheduling.

## Failure Policies
**Q:** Which failure policies?
**A:** All three: abort, skip, retry — as specified in PRD.

## Isolation
**Q:** Per-node or shared runner?
**A:** Per-node runner. Each composition node gets its own provisioned runner.

## Data Flow
**Q:** How should data flow between composition nodes?
**A:** File-based passing. Each node writes output to run directory; downstream nodes receive upstream output path as resolved input.

## CLI Integration
**Q:** Include `agentry run` integration for composed workflows?
**A:** Yes, full CLI integration. Single entry point for both simple and composed workflows.
