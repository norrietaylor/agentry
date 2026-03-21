# 02-spec-agentry-sandbox

## Introduction/Overview

Phase 2 adds the security and isolation layer to Agentry. It introduces a **RunnerProtocol** — a pluggable abstraction for execution environments that mirrors the relationship between local runners and CI runners (e.g., GitHub Actions). The first implementation is `DockerRunner`, which provisions Docker containers with filesystem isolation, network egress filtering, resource limits, and process isolation. The SecurityEnvelope orchestrates runner provisioning, preflight checks, and safety enforcement. Ed25519 workflow definition signing provides tamper detection for security-critical configuration. This phase transforms Agentry from a tool that trusts agents to one that constrains them, with a clean architectural seam for Phase 3's CI runner backends.

## Goals

1. **Define the RunnerProtocol and implement DockerRunner**: Establish a pluggable execution environment abstraction (`RunnerProtocol`) analogous to the relationship between local runners and CI runners. Implement `DockerRunner` as the first backend with four isolation boundaries — filesystem, network, resource limits, and process isolation.
2. **Enforce the Security Envelope**: Wrap the agent executor with a SecurityEnvelope that provisions the runner, strips excess tool capabilities, and enforces all safety constraints before and during execution.
3. **Implement the mandatory setup phase**: Run environment preparation and safety verification as a mandatory gate before agent execution, producing a setup manifest that records exactly what was prepared.
4. **Add preflight checks**: Verify that credentials are valid and the runner is correctly configured before the agent starts, catching misconfigurations that would otherwise cause late failures.
5. **Sign workflow definitions**: Implement Ed25519 cryptographic signing of the safety block, with verification at setup time, to detect tampering with security-critical configuration.

## User Stories

- As a **developer**, I want agents to run inside isolated runners by default so that a malfunctioning agent cannot access my host filesystem, exfiltrate data, or consume unbounded resources.
- As a **developer**, I want to run `agentry setup workflows/code-review.yaml` to verify my sandbox configuration without starting the agent, so that I can debug environment issues before incurring LLM costs.
- As a **developer**, I want the setup phase to verify my Anthropic API key is valid before the agent starts, so that I don't waste time on a run that will fail at the first LLM call.
- As a **workflow author**, I want the runtime to enforce the network allowlist from my safety block, so that my agent can only reach the LLM API and no other endpoints.
- As a **workflow author**, I want to sign my workflow's safety block so that code reviewers can verify the security configuration hasn't been tampered with after signing.
- As a **team lead**, I want to see a setup manifest for every execution so that I can compare two environments and explain behavioral differences.

## Demoable Units of Work

### Unit 1: Runner Protocol & Docker Runner

**Purpose:** Define a pluggable execution environment abstraction (`RunnerProtocol`) and implement the first backend (`DockerRunner`). The protocol mirrors the local-runner / CI-runner relationship — `DockerRunner` is to local execution what `GitHubActionsRunner` (Phase 3) will be to CI. This architectural seam ensures local workflows and CI workflows share the same safety semantics.

**Functional Requirements:**
- The system shall extend the `SafetyBlock` Pydantic model to include: `trust` field (enum: `sandboxed` | `elevated`, default `sandboxed`), `resources` with `cpu` (float, default 1.0), `memory` (string, default "2GB"), `timeout` (int seconds, default 300), `filesystem.read` (list of path patterns), `filesystem.write` (list of path patterns), `network.allow` (list of domain strings), and `sandbox.base` (string, default `agentry/sandbox:1.0`).
- The system shall define a `RunnerProtocol` (PEP-544 Protocol class) with the following methods: `provision(safety_block, resolved_inputs) -> RunnerContext` (prepare the execution environment), `execute(runner_context, agent_config) -> ExecutionResult` (run the agent inside the environment), `teardown(runner_context)` (clean up all resources), and `check_available() -> RunnerStatus` (probe whether this runner backend is operational). The `RunnerContext` is a dataclass containing the provisioned environment's state (container ID, mount mappings, network ID, etc.).
- The system shall implement `DockerRunner` conforming to `RunnerProtocol`. `DockerRunner.provision()` uses `docker-py` to create a container configured with: the base image from `sandbox.base`, CPU limit from `resources.cpu`, memory limit from `resources.memory`, read-only bind mounts for `filesystem.read` paths, read-write bind mounts for `filesystem.write` paths, and a non-root user (UID 1000).
- The system shall implement `InProcessRunner` conforming to `RunnerProtocol` for `trust: elevated` mode. `InProcessRunner` executes the agent in the current process (Phase 1 behavior) with a warning logged: "Running in elevated trust mode — no runner isolation." Its `provision()` is a no-op, `execute()` delegates to `AgentExecutor`, and `teardown()` is a no-op.
- The system shall implement a `RunnerDetector` that selects the appropriate runner: if `trust: elevated`, use `InProcessRunner`. If `trust: sandboxed`, check Docker availability via `DockerRunner.check_available()`. If Docker is unavailable, refuse to run with: "Docker is required for sandboxed execution. Install Docker or set trust: elevated."
- `DockerRunner` shall mount the resolved codebase path as read-only at `/workspace` inside the container, and the output directory as read-write at `/output`.
- `DockerRunner` shall enforce execution timeout by killing the container if it exceeds `resources.timeout` seconds. The container is killed (SIGKILL), not stopped gracefully — the timeout is a hard limit.
- `DockerRunner` shall clean up containers after execution completes (success or failure). Cleanup removes the container and any associated volumes. Failed cleanup shall log a warning but not raise an exception.
- `DockerRunner` shall run the Agentry runtime shim inside the container: a lightweight Python script that receives the LLM client configuration, tool bindings, and resolved inputs via a mounted JSON file, executes the agent, and writes output to `/output/result.json`.

**Proof Artifacts:**
- Test: `tests/unit/test_runner_protocol.py` passes — demonstrates that `DockerRunner` and `InProcessRunner` both satisfy `RunnerProtocol` via `isinstance()` checks. Tests runner detection logic for sandboxed/elevated/no-docker scenarios.
- Test: `tests/integration/test_docker_runner.py` passes — demonstrates container creation with correct mounts, resource limits, and non-root user. Requires Docker.
- Test: `tests/unit/test_sandbox_config.py` passes — demonstrates SafetyBlock model parsing with all new fields (no Docker required).
- CLI: `agentry run workflows/code-review.yaml --input diff=HEAD~1` executes inside a Docker container (when Docker is available) and produces output.
- CLI: `agentry run workflows/code-review.yaml --input diff=HEAD~1` with `trust: elevated` runs in-process with a warning message.

---

### Unit 2: Network Isolation & DNS Filtering

**Purpose:** Implement network egress filtering so that sandboxed agents can only reach domains declared in the workflow's `network.allow` list. This prevents data exfiltration and limits the blast radius of prompt injection.

**Functional Requirements:**
- The system shall create an isolated Docker network for each sandbox execution using `docker-py`. The network shall use the bridge driver with no default internet connectivity.
- The system shall run a lightweight DNS filtering proxy inside the sandbox network. The proxy shall resolve DNS queries only for domains listed in the workflow's `network.allow` list. Queries for unlisted domains shall return NXDOMAIN.
- The system shall always include the LLM API domain (e.g., `api.anthropic.com`) in the allow list, even if the workflow does not explicitly declare it. This is derived from the model configuration's provider.
- The system shall configure the sandbox container to use the DNS proxy as its sole DNS resolver.
- The system shall log all DNS queries (resolved and blocked) to the execution record for auditability.
- The system shall verify network isolation during the setup phase: attempt to resolve a known-blocked domain from inside the container and confirm it fails. If verification fails, abort the setup phase with a diagnostic.
- The system shall tear down the isolated network after execution completes (success or failure).

**Proof Artifacts:**
- Test: `tests/integration/test_network_isolation.py` passes — demonstrates that a container on the isolated network can resolve `api.anthropic.com` but cannot resolve `example.com` (when not in allowlist). Requires Docker.
- Test: `tests/unit/test_dns_filter.py` passes — demonstrates DNS filtering logic: allowed domains resolve, blocked domains return NXDOMAIN.
- File: `.agentry/runs/<timestamp>/execution-record.json` contains `dns_queries` section with resolved and blocked entries.

---

### Unit 3: Security Envelope & Setup Phase

**Purpose:** Implement the SecurityEnvelope that wraps the agent executor with safety enforcement, and the mandatory setup phase that prepares the environment and produces the setup manifest. This is the orchestration layer that ties sandbox, validation, and preflight together.

**Functional Requirements:**
- The system shall implement a `SecurityEnvelope` class that wraps `AgentExecutor`. The envelope receives the workflow definition and a `RunnerProtocol` instance (selected by `RunnerDetector`). It is responsible for: (a) stripping tool bindings that exceed the workflow's declared tool manifest, (b) provisioning the execution environment via `runner.provision()`, (c) running all preflight checks, (d) executing the agent via `runner.execute()`, (e) passing agent output through the three-layer validation pipeline before emission, and (f) calling `runner.teardown()` in a finally block.
- The system shall implement a `SetupPhase` class that executes all preparation and verification steps in sequence: (1) detect runner via `RunnerDetector`, (2) provision environment via `runner.provision()`, (3) verify network isolation (Docker runner only), (4) run preflight checks (API key validity), (5) compile the output validator, (6) generate the setup manifest.
- The system shall generate a **setup manifest** JSON file containing: workflow definition version, container image used, mounted filesystem paths (read and write), network egress rules, resource limits (CPU, memory, timeout), credential fingerprints (SHA-256 hash of API key, not the key itself), detected sandbox tier, and timestamp.
- The system shall save the setup manifest alongside the execution record at `.agentry/runs/<timestamp>/setup-manifest.json`.
- The system shall implement the `agentry setup <workflow-path>` CLI command that runs the setup phase in isolation (no agent execution). It provisions the sandbox, runs all checks, produces the setup manifest, and exits. This command is for debugging and verification.
- The system shall abort execution if any setup phase check fails. The abort message shall identify which check failed, why, and suggest remediation. No partial execution — if setup fails, the agent never starts.
- The system shall integrate with the existing `agentry run` command: when `trust: sandboxed`, `run` executes the setup phase first, then the agent inside the sandbox. When `trust: elevated`, `run` skips sandbox provisioning but still runs preflight checks and produces a setup manifest.

**Proof Artifacts:**
- Test: `tests/unit/test_security_envelope.py` passes — demonstrates tool stripping (envelope removes tools not in manifest), setup phase sequencing, and abort-on-failure.
- Test: `tests/unit/test_setup_manifest.py` passes — demonstrates manifest generation with all required fields, credential fingerprinting (SHA-256 hash), and manifest diffing.
- CLI: `agentry setup workflows/code-review.yaml` produces setup manifest and exits without running agent.
- CLI: `agentry setup workflows/code-review.yaml` with invalid API key fails with diagnostic: "Preflight check failed: ANTHROPIC_API_KEY is invalid."
- File: `.agentry/runs/<timestamp>/setup-manifest.json` contains all declared fields.

---

### Unit 4: Preflight Checks

**Purpose:** Verify that credentials and environment configuration are valid before the agent starts. Prevents the class of bugs where an agent runs for minutes of LLM processing and then fails because of a misconfiguration.

**Functional Requirements:**
- The system shall implement a `PreflightChecker` class with a pluggable check interface. Each check receives the workflow definition and environment context and returns a `PreflightResult` (pass/fail with diagnostic message).
- The system shall implement an `AnthropicAPIKeyCheck` that verifies the `ANTHROPIC_API_KEY` environment variable: (a) is set (not empty), (b) makes a lightweight API call (e.g., `GET /v1/models` or similar) to confirm the key is valid and has not been revoked.
- The system shall implement a `DockerAvailableCheck` that verifies Docker is running and accessible when `trust: sandboxed` is declared.
- The system shall implement a `FilesystemMountsCheck` that verifies all declared `filesystem.read` and `filesystem.write` paths exist on the host before attempting to mount them into the container.
- The system shall run all preflight checks during the setup phase. If any check fails, the setup phase aborts with a structured error listing all failed checks (not just the first one). This allows the developer to fix multiple issues in one iteration.
- The system shall record preflight results in the setup manifest: each check name, pass/fail status, and diagnostic message.
- The system shall support a `--skip-preflight` flag on `agentry run` and `agentry setup` that bypasses preflight checks with a warning. This is for development/debugging only.

**Proof Artifacts:**
- Test: `tests/unit/test_preflight.py` passes — demonstrates all preflight checks: API key valid/invalid/missing, Docker available/unavailable, filesystem paths exist/missing.
- Test: `tests/unit/test_preflight_all_failures.py` passes — demonstrates that multiple check failures are reported together, not one-at-a-time.
- CLI: `agentry run workflows/code-review.yaml --input diff=HEAD~1` with unset ANTHROPIC_API_KEY fails before any LLM call with "Preflight failed: ANTHROPIC_API_KEY is not set."
- CLI: `agentry run workflows/code-review.yaml --skip-preflight` bypasses checks with warning.

---

### Unit 5: Workflow Definition Signing

**Purpose:** Implement Ed25519 cryptographic signing of the workflow safety block so that tampering with security-critical configuration is detectable at setup time.

**Functional Requirements:**
- The system shall implement `agentry sign <workflow-path>` CLI command that signs the safety block and output.side_effects section of a workflow definition using Ed25519. The signature is appended to the workflow YAML as a `signature` block containing: `algorithm` ("ed25519"), `signed_blocks` (list of block names that were signed), `signature` (hex-encoded signature), and `timestamp` (ISO 8601).
- The system shall use the `cryptography` library for Ed25519 key generation, signing, and verification. Private keys are read from a configurable path (default: `~/.agentry/signing-key.pem`). Public keys are read from the workflow's repository (default: `.agentry/public-key.pem`).
- The system shall implement `agentry keygen` CLI command that generates an Ed25519 keypair, saving the private key to `~/.agentry/signing-key.pem` and the public key to `.agentry/public-key.pem` with instructions to commit the public key to the repository.
- The system shall sign only the `safety` block and `output.side_effects` block — not the entire workflow. This allows developers to modify prompts, model configuration, and input contracts without invalidating the signature.
- The system shall serialize the signed blocks deterministically (sorted keys, consistent YAML output) before signing, so that semantically equivalent blocks produce the same signature.
- The system shall verify the signature during the setup phase when a `signature` block is present in the workflow definition and a public key is available. Verification failure aborts setup with: "Safety block signature invalid. The safety block was modified since it was signed on {timestamp}."
- The system shall skip signature verification when no `signature` block is present (signing is opt-in). The `agentry validate --security-audit` command shall warn when a workflow lacks a signature.
- The system shall implement the `agentry validate --security-audit <path1> <path2>` flag that produces a diff of all security-relevant fields between two versions of a workflow definition (safety block, trust level, network allowlist, side_effects, output paths).

**Proof Artifacts:**
- Test: `tests/unit/test_signing.py` passes — demonstrates keygen, sign, verify cycle. Demonstrates verification failure when safety block is modified after signing.
- Test: `tests/unit/test_security_audit.py` passes — demonstrates diff of security-relevant fields between two workflow versions.
- CLI: `agentry keygen` generates keypair at expected paths.
- CLI: `agentry sign workflows/code-review.yaml` appends signature block to workflow.
- CLI: `agentry validate workflows/code-review.yaml` with signed workflow verifies signature and reports result.
- CLI: `agentry validate --security-audit workflows/v1.yaml workflows/v2.yaml` shows diff of security changes.

---

## Non-Goals (Out of Scope for Phase 2)

- **Bubblewrap (Tier 2) sandbox** — Only Docker (Tier 1) and trusted (Tier 3) are supported. Bubblewrap is Linux-only and adds significant complexity.
- **Dependency layer caching** — The `sandbox.dependencies` block is parsed but not executed. Teams use custom base images for now; automated dependency layer building is deferred.
- **GitHub token scope verification** — Preflight checks verify Anthropic API key only. GitHub token verification requires the GitHub Actions binder (Phase 3).
- **Composition/DAG execution** — Still single-agent only. Composition is Phase 3.
- **CI pipeline generation** — Still local execution only. `GitHubActionsRunner` implementing `RunnerProtocol` is Phase 3.
- **Container image publishing** — The base sandbox Dockerfile is included but not published to a registry. Publishing is an operational concern outside the spec.
- **Sigstore keyless signing** — Ed25519 with local keys only. Sigstore integration is a future enhancement.
- **iptables-based egress filtering** — DNS filtering provides domain-level egress control without requiring host-level iptables permissions.

## Design Considerations

- **Sandbox as default**: When Docker is available and `trust` is not explicitly set, agents run sandboxed. The safe path is the zero-configuration path.
- **Fail closed**: Any ambiguity in security configuration (e.g., a mount path that doesn't exist, a network domain that can't be resolved) halts the setup phase. The system does not proceed with best guesses.
- **Setup manifest as diagnostic tool**: The manifest is designed to be diffed between environments. Two manifests from different machines should explain behavioral differences.
- **Warning escalation**: `trust: elevated` workflows produce a warning on every invocation, not just the first. The warning cannot be silenced — elevated trust is intentionally noisy.

## Repository Standards

- Follows Phase 1 conventions: src layout, Pydantic v2 models, Click CLI, pytest, ruff, mypy strict
- Integration tests requiring Docker use `@pytest.mark.docker` marker and are skipped when Docker is unavailable
- New modules follow existing patterns: `src/agentry/runners/` (protocol + backends), `src/agentry/security/` (envelope, signing, preflight)

## Technical Considerations

- **docker-py 7.1.0+**: Use for all container lifecycle operations. Bind mounts via `Mounts` parameter, resource limits via `host_config`.
- **DNS proxy**: Lightweight Python-based DNS proxy using `dnslib`. Runs as a sidecar container or as a process inside the sandbox network. Must start before the agent container.
- **Deterministic YAML serialization**: For signing, use `yaml.dump(data, default_flow_style=False, sort_keys=True)` to ensure consistent output.
- **Credential fingerprinting**: SHA-256 hash of the API key value, stored as hex string in the setup manifest. Never store the actual key.
- **Container cleanup**: Use `try/finally` patterns to ensure containers and networks are removed even on exceptions. Register an `atexit` handler as a fallback.
- **Phase 1 integration**: The SecurityEnvelope wraps the existing `AgentExecutor`. The `agentry run` command's execution path becomes: parse → setup phase (SecurityEnvelope + Runner) → execute (via runner) → validate output → emit. `InProcessRunner` preserves Phase 1 behavior; `DockerRunner` adds isolation.
- **Runner ↔ CI symmetry**: The `RunnerProtocol` is deliberately analogous to CI runner infrastructure. `DockerRunner` is to local execution what a GitHub Actions runner is to CI — both provision an isolated environment from the same workflow definition. Phase 3's `GitHubActionsRunner` will implement the same protocol, generating Actions YAML that mirrors the local Docker configuration. This ensures workflows behave identically locally and in CI.
- **Docker AI Sandboxes**: Docker Desktop offers [AI Sandboxes](https://docs.docker.com/ai/sandboxes/) that run entire agent processes inside microVMs. These operate at a different level — they sandbox the agent itself, while Agentry's `RunnerProtocol` sandboxes individual workflow executions with per-workflow policies. A future `DockerSandboxRunner` could integrate with their API if/when one ships, as another `RunnerProtocol` backend.

## Security Considerations

- **Private signing keys** must never be committed to repositories. The `agentry keygen` command saves private keys to `~/.agentry/` (user home directory), not the project directory.
- **Container escape**: Docker provides strong isolation but is not a security boundary against a determined attacker with root access. The sandbox protects against agent misbehavior, not host compromise.
- **DNS proxy reliability**: If the DNS proxy crashes, the agent container loses all network access (fail-closed behavior). This is the correct failure mode.
- **Setup manifest sensitivity**: The manifest contains credential fingerprints (hashes) but never actual credentials. It is safe to include in CI artifacts and execution records.
- **Image provenance**: Custom base images are accepted without verification in Phase 2. Image signing/verification is a future enhancement.

## Success Metrics

- All standard library workflows (`code-review.yaml`, `bug-fix.yaml`, `triage.yaml`) execute successfully inside Docker containers with sandbox isolation.
- The setup phase catches and reports: invalid API keys, missing Docker, non-existent mount paths — all before the agent starts.
- Network isolation prevents agents from reaching domains not in the allowlist (verified by integration tests).
- Setup manifests are generated for every execution and contain all declared fields.
- Workflow signing round-trips correctly: sign → modify non-safety fields → verify succeeds. Sign → modify safety block → verify fails.
- `agentry validate --security-audit` correctly identifies changes to trust level, network allowlist, and side effects between workflow versions.
- All existing Phase 1 tests continue to pass (no regressions).

## Open Questions

1. **DNS proxy implementation**: Should the DNS proxy run as a sidecar container (simpler isolation) or as a process inside the agent's container (simpler networking)? *Recommendation: sidecar container for stronger isolation.*
2. **Base image contents**: Should the base sandbox image include the `cryptography` library for signing verification inside the container? *Recommendation: yes, include it — the runtime shim needs it for setup manifest generation.*
3. **Container logging**: Should container stdout/stderr be captured in the execution record? *Recommendation: yes, capture and include. Useful for debugging agent failures inside the sandbox.*

## Phase Roadmap

| Phase | Spec | Focus | Status |
|-------|------|-------|--------|
| 1 | 01-spec-agentry-cli | Core foundation | Complete |
| **2** | **This spec** | **Security & isolation** | **Current** |
| 3 | 03-spec-agentry-composition | Multi-agent & CI | Next |
