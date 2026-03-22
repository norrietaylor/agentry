# Agentry E2E Manual Test Plan

End-to-end manual test plan covering all four phases of the current implementation.
Each test case includes the command to run, expected outcome, and what it validates.

**Prerequisites:**
- Python 3.11+ with `uv` installed
- `uv run` prefix for all commands (no global install required)
- Docker installed and running (for sandbox tests)
- `ANTHROPIC_API_KEY` set in environment (for live agent tests)
- A local git repository to use as a target (the agentry repo itself works)

---

## Phase 1: Core CLI & Workflow Parsing

### 1.1 Version and Help

```bash
uv run agentry --version
```
**Expected:** Prints `agentry, version 0.1.0`

```bash
uv run agentry --help
```
**Expected:** Shows top-level help with commands: `ci`, `keygen`, `registry`, `run`, `setup`, `sign`, `validate`

```bash
uv run agentry run --help
```
**Expected:** Shows `--input`, `--target`, `--skip-preflight`, `--node`, `--binder` options

### 1.2 Workflow Validation — Valid Workflows

```bash
uv run agentry validate workflows/code-review.yaml
uv run agentry validate workflows/triage.yaml
uv run agentry validate workflows/bug-fix.yaml
uv run agentry validate workflows/task-decompose.yaml
uv run agentry validate workflows/planning-pipeline.yaml
```
**Expected:** Each prints `Validation successful: workflows/<name>.yaml`

### 1.3 Workflow Validation — Invalid Input

```bash
uv run agentry validate nonexistent.yaml
```
**Expected:** `Error: workflow file not found: nonexistent.yaml`, exit code 1

```bash
echo "not: valid: yaml: [" > /tmp/bad.yaml && uv run agentry validate /tmp/bad.yaml
```
**Expected:** Validation error, exit code 1

### 1.4 JSON Output Format

```bash
uv run agentry --output-format json validate workflows/code-review.yaml
```
**Expected:** JSON output `{"status": "valid", "path": "workflows/code-review.yaml"}`

### 1.5 Composed Workflow Validation

```bash
uv run agentry validate workflows/planning-pipeline.yaml
```
**Expected:** Validates successfully (includes composition block with 3 steps, DAG is valid)

---

## Phase 2: Security & Sandbox

### 2.1 Keygen

```bash
uv run agentry keygen
```
**Expected:** Creates `~/.agentry/signing-key.pem` (private) and `.agentry/public-key.pem` (public). Prints paths and next-step guidance.

**Verify:**
```bash
ls -la ~/.agentry/signing-key.pem .agentry/public-key.pem
```

### 2.2 Workflow Signing

```bash
uv run agentry sign workflows/code-review.yaml --output /tmp/signed-review.yaml
```
**Expected:** Creates `/tmp/signed-review.yaml` with a `signature` block appended. Prints `Workflow signed: /tmp/signed-review.yaml`.

**Verify:**
```bash
grep -A4 'signature:' /tmp/signed-review.yaml
```
**Expected:** Shows `algorithm: ed25519`, `signed_blocks`, `signature` (hex), `timestamp` (ISO 8601).

### 2.3 Security Audit — Single File

```bash
uv run agentry validate --security-audit workflows/code-review.yaml
```
**Expected:** Reports whether the workflow is signed or unsigned. No errors.

### 2.4 Security Audit — Diff Between Versions

```bash
cp workflows/code-review.yaml /tmp/review-v1.yaml
# Manually edit /tmp/review-v1.yaml to change trust level or add network config
uv run agentry validate --security-audit /tmp/review-v1.yaml workflows/code-review.yaml
```
**Expected:** Diff of security-relevant fields between the two versions.

### 2.5 Setup Phase

```bash
uv run agentry setup workflows/code-review.yaml --skip-preflight
```
**Expected:** `Setup complete: workflows/code-review.yaml` with manifest path. Creates `.agentry/runs/<timestamp>/setup-manifest.json`.

```bash
uv run agentry setup workflows/code-review.yaml
```
**Expected (no ANTHROPIC_API_KEY):** Preflight check fails with `Preflight check failed: anthropic_api_key: ...` and remediation message.

**Expected (with ANTHROPIC_API_KEY set):** Setup completes. Preflight checks report PASS for API key, Docker, and filesystem mounts.

### 2.6 Setup Phase — JSON Output

```bash
uv run agentry --output-format json setup workflows/code-review.yaml --skip-preflight
```
**Expected:** JSON with `status: ok`, `manifest_path`, and `preflight_results` array.

---

## Phase 3: Composition Engine

### 3.1 Run Single-Agent Workflow (Stub)

```bash
uv run agentry run workflows/triage.yaml --input issue-description="Login fails on Safari" --input repository-ref=. --skip-preflight
```
**Expected:** Runs the triage workflow (or emits stub output if executor not fully wired to LLM). No crash. Produces output to `.agentry/runs/<timestamp>/`.

### 3.2 Run Composed Workflow (Stub)

```bash
uv run agentry run workflows/planning-pipeline.yaml --input issue-description="API latency spike" --input repository-ref=. --skip-preflight
```
**Expected:** Detects composition block (3 steps: triage → task-decompose → summary). Dispatches through CompositionEngine. Prints per-node status (start/complete/fail). Produces composition record.

### 3.3 Composed Workflow — Single Node Isolation

```bash
uv run agentry run workflows/planning-pipeline.yaml --node triage --input issue-description="Test" --input repository-ref=. --skip-preflight
```
**Expected:** Executes only the `triage` node in isolation. No upstream/downstream propagation.

### 3.4 Node Flag on Non-Composed Workflow

```bash
uv run agentry run workflows/code-review.yaml --node something --input diff=HEAD~1 --input codebase=. --skip-preflight
```
**Expected:** `Error: --node flag is only valid for composition workflows`, exit code 1.

### 3.5 Invalid Node ID

```bash
uv run agentry run workflows/planning-pipeline.yaml --node nonexistent --input issue-description="Test" --input repository-ref=. --skip-preflight
```
**Expected:** Error listing available node IDs: `triage, task-decompose, summary`.

### 3.6 JSON Output for Composition

```bash
uv run agentry --output-format json run workflows/planning-pipeline.yaml --input issue-description="Test" --input repository-ref=. --skip-preflight
```
**Expected:** JSON composition record with per-node status map, timing, and overall status.

---

## Phase 4: GitHub Actions Binder & CI Generation

### 4.1 CI Generate — Basic

```bash
uv run agentry ci generate --target github workflows/code-review.yaml --dry-run
```
**Expected:** Prints valid GitHub Actions YAML to stdout with:
- `name: "Agentry: code-review"` (or similar)
- `on: pull_request` trigger
- `permissions: contents: read`
- `jobs.agentry.runs-on: ubuntu-latest`
- Steps: checkout, setup-python, install agentry, run agentry
- `env` with `ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}`

### 4.2 CI Generate — Multiple Triggers

```bash
uv run agentry ci generate --target github --triggers pull_request,push,schedule --schedule "0 2 * * 1" workflows/code-review.yaml --dry-run
```
**Expected:** YAML with `on:` block containing `pull_request`, `push`, and `schedule` (with cron expression).

### 4.3 CI Generate — Issue Trigger

```bash
uv run agentry ci generate --target github --triggers issues workflows/triage.yaml --dry-run
```
**Expected:** YAML with `on: issues` trigger.

### 4.4 CI Generate — Write to File

```bash
uv run agentry ci generate --target github workflows/code-review.yaml --output-dir /tmp/gh-workflows/
```
**Expected:** Creates `/tmp/gh-workflows/agentry-code-review.yaml`. Prints the output path.

**Verify:**
```bash
cat /tmp/gh-workflows/agentry-code-review.yaml
```

### 4.5 CI Generate — Permission Derivation

```bash
uv run agentry ci generate --target github workflows/code-review.yaml --dry-run 2>&1 | grep -A5 'permissions'
```
**Expected:** `contents: read` only (code-review uses `repository:read` but no write tools).

For a workflow with `pr:comment` tools (if one exists or create a temporary one):
**Expected:** `contents: read` AND `pull-requests: write`.

### 4.6 CI Generate — Composed Workflow Rejection

```bash
uv run agentry ci generate --target github workflows/planning-pipeline.yaml --dry-run
```
**Expected:** Error: `Composed workflow CI generation is not yet supported. Generate CI config for each component workflow individually.`

### 4.7 CI Generate — Unsupported Target

```bash
uv run agentry ci generate --target gitlab workflows/code-review.yaml --dry-run
```
**Expected:** Error about unsupported target (only `github` is supported).

### 4.8 CI Generate — Schedule Without Cron

```bash
uv run agentry ci generate --target github --triggers schedule workflows/code-review.yaml --dry-run
```
**Expected:** Error requiring `--schedule` when `schedule` trigger is specified.

### 4.9 Binder Auto-Detection

```bash
GITHUB_ACTIONS=true uv run agentry run --help
```
**Expected:** `--binder` flag visible in help.

```bash
uv run agentry run workflows/code-review.yaml --binder local --input diff=HEAD~1 --input codebase=. --skip-preflight
```
**Expected:** Explicitly selects local binder. Runs normally.

### 4.10 Binder Registry

```bash
uv run python -c "from agentry.binders.registry import discover_binders; print(list(discover_binders().keys()))"
```
**Expected:** Output includes both `"local"` and `"github-actions"`.

### 4.11 GitHub Actions Binder — Protocol Conformance

```bash
uv run python -c "
from agentry.binders.protocol import EnvironmentBinder
from agentry.binders.github_actions import GitHubActionsBinder
print(isinstance(GitHubActionsBinder.__new__(GitHubActionsBinder), EnvironmentBinder))
"
```
**Expected:** `True`

---

## Phase 5: Agent Runtime

### 5A.1 Agent Registry Discovery

```bash
uv run python -c "
from agentry.agents.registry import AgentRegistry
reg = AgentRegistry.default()
print(list(reg.list_runtimes()))
"
```
**Expected:** Output includes `"claude-code"`.

### 5A.2 ClaudeCodeAgent Availability

```bash
uv run python -c "
from agentry.agents.claude_code import ClaudeCodeAgent
agent = ClaudeCodeAgent()
status = agent.check_available()
print(f'available={status.available}, message={status.message}')
"
```
**Expected (claude installed):** `available=True, message=...`
**Expected (claude not installed):** `available=False, message=claude binary not found on PATH`

### 5A.3 Workflow Validation with Agent Block

```bash
uv run agentry validate workflows/code-review.yaml
```
**Expected:** Validates successfully with the `agent` block (runtime: claude-code).

### 5A.4 Workflow Validation — Unknown Runtime

```bash
cat > /tmp/bad-runtime.yaml << 'EOF'
identity:
  name: test
  version: 1.0.0
  description: test
agent:
  runtime: nonexistent-agent
  model: test
  system_prompt: test
inputs: {}
tools:
  capabilities: []
safety:
  resources:
    timeout: 60
output:
  schema:
    type: object
    properties:
      result:
        type: string
EOF
uv run agentry validate /tmp/bad-runtime.yaml
```
**Expected:** Warning or error about unknown runtime `nonexistent-agent`.

### 5A.5 Model Block Backward Compatibility

```bash
cat > /tmp/model-compat.yaml << 'EOF'
identity:
  name: compat-test
  version: 1.0.0
  description: test backward compat
model:
  provider: anthropic
  model_id: claude-sonnet-4-20250514
  temperature: 0.2
  max_tokens: 4096
  system_prompt: test
inputs: {}
tools:
  capabilities: []
safety:
  resources:
    timeout: 60
output:
  schema:
    type: object
    properties:
      result:
        type: string
EOF
uv run agentry validate /tmp/model-compat.yaml
```
**Expected:** Validates successfully — `model` block auto-converts to `agent` block internally.

### 5A.6 AgentProtocol Conformance

```bash
uv run python -c "
from agentry.agents.protocol import AgentProtocol
from agentry.agents.claude_code import ClaudeCodeAgent
print(isinstance(ClaudeCodeAgent(), AgentProtocol))
"
```
**Expected:** `True`

### 5A.7 RunnerDetector with Agent Resolution

```bash
uv run python -c "
from agentry.agents.registry import AgentRegistry
from agentry.runners.detector import RunnerDetector
from agentry.models.safety import SafetyBlock
reg = AgentRegistry.default()
detector = RunnerDetector(agent_registry=reg, agent_name='claude-code')
runner = detector.get_runner(SafetyBlock())
print(type(runner).__name__)
"
```
**Expected:** `InProcessRunner` (elevated trust default).

### 5A.8 SecurityEnvelope — No Executor Parameter

```bash
uv run python -c "
import inspect
from agentry.security.envelope import SecurityEnvelope
sig = inspect.signature(SecurityEnvelope.__init__)
params = list(sig.parameters.keys())
print('executor' not in params)
"
```
**Expected:** `True` — envelope no longer accepts an executor.

### 5A.9 Run Workflow with Agent Runtime

```bash
uv run agentry run workflows/triage.yaml \
  --input issue-description="Login fails on Safari" \
  --input repository-ref=. \
  --skip-preflight
```
**Expected:** Runs using ClaudeCodeAgent (Claude Code CLI). Produces output to `.agentry/runs/<timestamp>/`.

---

## Cross-Phase Integration

### 5.1 Full Workflow Lifecycle (Local)

This test validates the complete local lifecycle: validate → setup → run.

```bash
# Step 1: Validate
uv run agentry validate workflows/triage.yaml

# Step 2: Setup (skip preflight for speed)
uv run agentry setup workflows/triage.yaml --skip-preflight

# Step 3: Run
uv run agentry run workflows/triage.yaml \
  --input issue-description="Database connection timeout in production" \
  --input repository-ref=. \
  --skip-preflight
```
**Expected:** Each step succeeds. Run produces output in `.agentry/runs/<timestamp>/`.

### 5.2 Full Workflow Lifecycle (CI Generation)

This test validates: validate → generate CI → inspect generated YAML.

```bash
# Step 1: Validate
uv run agentry validate workflows/code-review.yaml

# Step 2: Generate CI
uv run agentry ci generate --target github workflows/code-review.yaml --output-dir /tmp/ci-test/

# Step 3: Verify generated YAML is valid
cat /tmp/ci-test/agentry-code-review.yaml

# Step 4: Validate the workflow referenced in the generated YAML still parses
uv run agentry validate workflows/code-review.yaml
```
**Expected:** Complete chain succeeds. Generated YAML references the correct workflow path.

### 5.3 Composed Workflow → Individual CI Generation

```bash
# The composed workflow can't generate CI directly
uv run agentry ci generate --target github workflows/planning-pipeline.yaml --dry-run 2>&1 || true

# But each component can
uv run agentry ci generate --target github workflows/triage.yaml --dry-run
uv run agentry ci generate --target github workflows/task-decompose.yaml --dry-run
```
**Expected:** Composed workflow is rejected. Individual workflows each produce valid YAML.

### 5.4 Sign → Validate with Audit

```bash
# Generate keys if not already present
uv run agentry keygen 2>/dev/null || true

# Sign a workflow
uv run agentry sign workflows/triage.yaml --output /tmp/triage-signed.yaml

# Audit the signed version
uv run agentry validate --security-audit /tmp/triage-signed.yaml

# Compare original vs signed
uv run agentry validate --security-audit workflows/triage.yaml /tmp/triage-signed.yaml
```
**Expected:** Signed version has a signature block. Audit shows signing status. Diff shows the signature was added.

---

## Automated Test Suite Verification

### 6.1 Unit Tests

```bash
uv run pytest tests/unit/ -v --tb=short 2>&1 | tail -20
```
**Expected:** All tests pass. Count should be ~1400+.

### 6.2 Integration Tests

```bash
uv run pytest tests/integration/ -v --tb=short 2>&1 | tail -20
```
**Expected:** All integration tests pass.

### 6.3 Full Suite

```bash
uv run pytest tests/ -v --tb=short 2>&1 | tail -5
```
**Expected:** ~1600+ passed, 0 failures (count increased with Phase 5 agent runtime tests).

### 6.4 Lint

```bash
uv run ruff check src/agentry/
```
**Expected:** Clean or only pre-existing style warnings (no errors).

### 6.5 Type Check

```bash
uv run mypy src/agentry/ --ignore-missing-imports 2>&1 | tail -5
```
**Expected:** Passes or shows only the 9 known strict errors in `github_actions.py`.

---

## Error Handling & Edge Cases

### 7.1 Missing Input

```bash
uv run agentry run workflows/code-review.yaml --skip-preflight
```
**Expected:** Error about missing required input `diff`.

### 7.2 Malformed Input Flag

```bash
uv run agentry run workflows/triage.yaml --input badformat --skip-preflight
```
**Expected:** `Error: --input value must be KEY=VALUE, got: 'badformat'`

### 7.3 Non-Existent Workflow

```bash
uv run agentry run nonexistent.yaml --skip-preflight
```
**Expected:** `Error: workflow file not found: nonexistent.yaml`

### 7.4 Registry Stub

```bash
uv run agentry registry
```
**Expected:** `Not yet implemented`

### 7.5 Ctrl+C Handling

```bash
# Start a long-running workflow and press Ctrl+C
uv run agentry run workflows/planning-pipeline.yaml \
  --input issue-description="Test interrupt" \
  --input repository-ref=. \
  --skip-preflight
# Press Ctrl+C during execution
```
**Expected:** `Interrupted. Partial results:` message. Exit code 130. No crash or hang.

---

## Test Result Tracking

| # | Test | Phase | Result | Notes |
|---|------|-------|--------|-------|
| 1.1 | Version and help | 1 | | |
| 1.2 | Valid workflow validation | 1 | | |
| 1.3 | Invalid input validation | 1 | | |
| 1.4 | JSON output format | 1 | | |
| 1.5 | Composed workflow validation | 1 | | |
| 2.1 | Keygen | 2 | | |
| 2.2 | Workflow signing | 2 | | |
| 2.3 | Security audit (single) | 2 | | |
| 2.4 | Security audit (diff) | 2 | | |
| 2.5 | Setup phase | 2 | | |
| 2.6 | Setup JSON output | 2 | | |
| 3.1 | Single-agent run | 3 | | |
| 3.2 | Composed workflow run | 3 | | |
| 3.3 | Single node isolation | 3 | | |
| 3.4 | Node flag on non-composed | 3 | | |
| 3.5 | Invalid node ID | 3 | | |
| 3.6 | Composition JSON output | 3 | | |
| 4.1 | CI generate basic | 4 | | |
| 4.2 | CI generate multi-trigger | 4 | | |
| 4.3 | CI generate issue trigger | 4 | | |
| 4.4 | CI generate file output | 4 | | |
| 4.5 | Permission derivation | 4 | | |
| 4.6 | Composed workflow rejection | 4 | | |
| 4.7 | Unsupported target | 4 | | |
| 4.8 | Schedule without cron | 4 | | |
| 4.9 | Binder auto-detection | 4 | | |
| 4.10 | Binder registry | 4 | | |
| 4.11 | Protocol conformance | 4 | | |
| 5A.1 | Agent registry discovery | 5 | | |
| 5A.2 | ClaudeCodeAgent availability | 5 | | |
| 5A.3 | Validate with agent block | 5 | | |
| 5A.4 | Unknown runtime validation | 5 | | |
| 5A.5 | Model block backward compat | 5 | | |
| 5A.6 | AgentProtocol conformance | 5 | | |
| 5A.7 | RunnerDetector agent resolution | 5 | | |
| 5A.8 | Envelope no executor | 5 | | |
| 5A.9 | Run with agent runtime | 5 | | |
| 5.1 | Full local lifecycle | X | | |
| 5.2 | Full CI lifecycle | X | | |
| 5.3 | Composed → individual CI | X | | |
| 5.4 | Sign → audit chain | X | | |
| 6.1 | Unit tests | All | | |
| 6.2 | Integration tests | All | | |
| 6.3 | Full suite | All | | |
| 6.4 | Lint | All | | |
| 6.5 | Type check | All | | |
| 7.1 | Missing input | Err | | |
| 7.2 | Malformed input flag | Err | | |
| 7.3 | Non-existent workflow | Err | | |
| 7.4 | Registry stub | Err | | |
| 7.5 | Ctrl+C handling | Err | | |
