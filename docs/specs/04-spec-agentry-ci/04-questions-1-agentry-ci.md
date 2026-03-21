# 04-spec-agentry-ci — Clarifying Questions (Round 1)

Questions about the GitHub Actions binder and CI generation features.

## Trigger Configuration
**Q:** Which GitHub event triggers should `ci generate` support in the initial release?
**A:** Full event coverage: `pull_request`, `push`, `schedule`, and `issues` triggers.

## Output Mapping
**Q:** Which output destinations should the GitHub binder support: PR comments only, or also check annotations and artifact uploads?
**A:** PR comments only for initial release. Check annotations and artifact uploads deferred.

## Tool Binding Scope
**Q:** Which abstract tools should the GitHub binder bind in Phase 4? Just the existing `repository:read` and `shell:execute`, or also write-side tools (`pr:comment`, `issue:create`)?
**A:** Read + write tools: bind `pr:comment` and `pr:review` alongside read tools. Agents need to post results.

## Token Verification
**Q:** Should token scope verification be a preflight check (fail before execution) or a runtime check (fail at tool invocation)?
**A:** Preflight check — fail fast before agent execution starts (consistent with Phase 2 preflight pattern).

## Composition Support
**Q:** Should `ci generate` support composed workflows (multi-job pipelines) or only single-agent workflows initially?
**A:** Single-agent only. One workflow definition → one GitHub Actions job. Composition CI support deferred.
