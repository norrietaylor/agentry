# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v0.1.0] - 2026-03-21

### Added

#### Phase 1 - Core CLI and Workflow Parsing
- CLI with `validate`, `run`, `setup` commands
- YAML workflow definition parser with Pydantic v2 models
- Workflow blocks: identity, inputs, tools, agent, safety, output, composition
- JSON and text output formats
- Standard library workflows: code-review, triage, bug-fix, task-decompose, planning-pipeline

#### Phase 2 - Security and Sandbox
- SecurityEnvelope with tool manifest enforcement
- DockerRunner for sandboxed execution (CPU/memory limits, filesystem mounts, non-root user)
- InProcessRunner for elevated trust mode
- Ed25519 workflow signing and verification
- Preflight check framework (API key, Docker, filesystem, token scope)
- Three-layer output validation pipeline (schema, tool audit, side-effect)

#### Phase 3 - Composition Engine
- DAG-based multi-agent composition with TopologicalSorter
- Concurrent node execution with asyncio
- Failure policies: abort, skip, retry with configurable fallback
- File-based inter-node data passing
- Single-node isolation debugging (--node flag)

#### Phase 4 - GitHub Actions Binder and CI Generation
- GitHubActionsBinder implementing EnvironmentBinder protocol
- `ci generate` command producing ready-to-commit Actions YAML
- Automatic permission derivation from tool manifest
- Configurable triggers (pull_request, push, schedule, issues)
- Binder auto-detection and registry with entry-point discovery

#### Phase 5 - Agent Runtime
- Four-layer architecture: Agentry → Runner → Agent → Model
- AgentProtocol (PEP-544) with ClaudeCodeAgent implementation
- AgentRegistry for pluggable agent runtime discovery
- Runner-Agent integration (runners own agent execution)
- DockerRunner updated to run agents inside containers
- SecurityEnvelope simplified (unified RunnerProtocol, no executor dependency)
- Workflow `agent` block replacing `model` block (with backward compatibility)
