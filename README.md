<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/agentry-logo-dark.svg" width="128">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/agentry-logo-light.svg" width="128">
    <img alt="Agentry" src="docs/assets/agentry-logo-dark.svg" width="128">
  </picture>
</p>

<h1 align="center">Agentry</h1>

<p align="center">Portable agentic workflow orchestration.<br>Define AI agent workflows once, run them identically on your laptop and in CI.</p>

Agentry treats agentic workflows as declarative, versionable definitions. A workflow specifies what an agent needs (inputs), what it can do (tools), which agent runtime executes the work (Claude Code, with more runtimes planned), what constraints it operates under (safety), and what it produces (output schema). The same definition runs locally via `agentry run` and generates GitHub Actions pipelines via `agentry ci generate`.

## Install

```bash
pip install agentry
```

Requires Python 3.10+. [Claude Code](https://docs.anthropic.com/en/docs/claude-code) must be installed and authenticated (`claude` on PATH). Docker is optional (required for sandboxed execution).

## Quick Start

### 1. Validate a workflow

```bash
agentry validate workflows/code-review.yaml
```

### 2. Run a workflow locally

```bash
agentry run workflows/triage.yaml \
  --input issue-description="Login fails on Safari" \
  --input repository-ref=.
```

Agentry resolves inputs (including git refs like `HEAD~1` for diff inputs), selects the appropriate runner, launches the agent runtime, enforces the tool manifest, and validates the output against the declared schema. Execution records are written to `.agentry/runs/` for auditability.

### Self-development

Agentry reviews its own PRs. The `.github/workflows/agentry-code-review.yml` workflow runs `agentry run workflows/code-review.yaml` on every pull request, posting findings as PR comments. The bug-fix workflow can create branches and open PRs with proposed fixes — all requiring human review before merge.

### 3. Generate a GitHub Actions pipeline

```bash
agentry ci generate --target github workflows/code-review.yaml
```

This produces `.github/workflows/agentry-code-review.yaml` — a ready-to-commit Actions workflow that runs your agent on pull requests.

## Workflow Definition

Workflows are YAML files with seven blocks:

```yaml
identity:
  name: code-review
  version: 1.0.0
  description: Reviews PR diffs for security and style issues.

inputs:
  diff:
    type: git-diff
    required: true
  codebase:
    type: repository-ref
    required: true

tools:
  capabilities:
    - repository:read

agent:
  runtime: claude-code
  model: claude-sonnet-4-20250514
  system_prompt: prompts/code-review.md

safety:
  resources:
    timeout: 300

output:
  schema:
    type: object
    required: [findings, summary, confidence]
    properties:
      findings:
        type: array
      summary:
        type: string
      confidence:
        type: number

composition:
  steps: []
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `agentry validate <workflow>` | Validate a workflow definition |
| `agentry run <workflow>` | Execute a workflow locally |
| `agentry setup <workflow>` | Run setup phase without executing the agent |
| `agentry ci generate --target github <workflow>` | Generate GitHub Actions YAML |
| `agentry keygen` | Generate Ed25519 signing keypair |
| `agentry sign <workflow>` | Sign a workflow's safety and output blocks |

### Key flags

```
agentry run <workflow>
  --input KEY=VALUE        Pass inputs (repeatable)
  --target PATH            Repository to run against (default: cwd)
  --binder NAME            Override binder selection (local, github-actions)
  --skip-preflight         Skip preflight checks
  --node NODE_ID           Run a single composition node in isolation

agentry ci generate --target github <workflow>
  --triggers TYPE,...      Event triggers (pull_request, push, schedule, issues)
  --schedule CRON          Cron expression (required with schedule trigger)
  --output-dir PATH        Output directory (default: .github/workflows/)
  --dry-run                Print YAML to stdout without writing
```

## Multi-Agent Composition

Compose multiple agents into a DAG pipeline:

```yaml
composition:
  steps:
    - name: triage
      workflow: triage.yaml
      depends_on: []
    - name: decompose
      workflow: task-decompose.yaml
      depends_on: [triage]
      inputs:
        triage_result: triage.output
      failure:
        mode: retry
        max_retries: 2
        fallback: skip
```

Independent nodes run concurrently. Failure policies control propagation: `abort` (halt), `skip` (pass failure object downstream), or `retry` (re-execute with fallback).

```bash
# Run the full pipeline
agentry run workflows/planning-pipeline.yaml \
  --input issue-description="API latency spike" \
  --input repository-ref=.

# Debug a single node
agentry run workflows/planning-pipeline.yaml --node triage
```

## Security

Agentry enforces least-privilege execution:

- **Trust levels**: `sandboxed` (Docker isolation) or `elevated` (host process)
- **Filesystem controls**: read/write path allowlists
- **Network isolation**: DNS-based egress filtering with domain allowlists
- **Tool manifest**: agents only access declared capabilities
- **Preflight checks**: API key, Docker, filesystem, and token scope verification
- **Workflow signing**: Ed25519 signatures over safety blocks detect tampering

```bash
# Generate a signing keypair
agentry keygen

# Sign a workflow
agentry sign workflows/code-review.yaml

# Audit security changes between versions
agentry validate --security-audit v1.yaml v2.yaml
```

## CI Generation

Generate GitHub Actions pipelines from workflow definitions:

```bash
# Basic — triggers on pull requests
agentry ci generate --target github workflows/code-review.yaml

# Multiple triggers with schedule
agentry ci generate --target github \
  --triggers pull_request,schedule \
  --schedule "0 2 * * 1" \
  workflows/code-review.yaml

# Preview without writing
agentry ci generate --target github --dry-run workflows/code-review.yaml
```

The generated YAML declares minimal token permissions derived from the workflow's tool manifest. The runtime auto-detects the GitHub Actions environment and selects the correct binder.

## Standard Library Workflows

| Workflow | Description |
|----------|-------------|
| `workflows/code-review.yaml` | PR diff review for security, performance, and style |
| `workflows/triage.yaml` | Issue classification and routing |
| `workflows/bug-fix.yaml` | Bug diagnosis and fix suggestion (creates PRs via `pr:create`) |
| `workflows/task-decompose.yaml` | Issue decomposition into implementation tasks |
| `workflows/planning-pipeline.yaml` | Composed pipeline: triage → decompose → summarize |

All workflows execute end-to-end via `agentry run`, producing structured JSON output. The `--input diff=HEAD~1` syntax automatically resolves git refs to diff content.

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/

# Lint
ruff check src/agentry/

# Type check
mypy src/agentry/
```

## Architecture

Agentry separates concerns into five layers:

1. **Definition** — Workflow YAML parsed into Pydantic models
2. **Safety** — SecurityEnvelope enforces trust level, preflight checks, signing
3. **Resolution** — EnvironmentBinder translates abstract inputs/tools to concrete implementations (LocalBinder, GitHubActionsBinder)
4. **Execution** — RunnerProtocol provisions isolated environments (DockerRunner, InProcessRunner)
5. **Agent** — AgentProtocol delegates to a coding agent runtime (ClaudeCodeAgent)

```
Agentry (orchestration)
  └→ Runner (execution environment: docker-sandbox, in-process)
       └→ Agent (coding agent runtime: Claude Code)
            └→ Model (LLM: Claude)
```

The binder system is pluggable via Python entry points (`agentry.binders` group). Adding a new CI target means implementing the `EnvironmentBinder` protocol. Adding a new agent runtime means implementing the `AgentProtocol` — the workflow definition only changes the `agent.runtime` field.

## Slides

An interactive slide deck covering the architecture, security model, and demo is available at [docs/demo/slides.html](docs/demo/slides.html). Works on desktop (keyboard/click navigation) and mobile (swipe).

## License

Apache License 2.0. See [LICENSE](LICENSE).
