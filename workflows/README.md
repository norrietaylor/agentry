# Workflows

This directory contains the standard workflow library for the Agentry CLI. Each workflow is a YAML definition that specifies how to invoke an LLM agent for a specific task, including inputs, outputs, tool capabilities, safety constraints, and composition.

## Overview

Three workflows are available:

1. **code-review** — Reviews pull request diffs for quality, correctness, security, and performance issues.
2. **bug-fix** — Diagnoses bugs and identifies root causes with targeted fixes.
3. **triage** — Classifies and triages software issues for severity, category, and routing.

All workflows use Claude as the LLM provider and enforce strict output schemas to ensure consistent, structured results.

---

## code-review Workflow

**File:** `code-review.yaml`

Analyzes pull request diffs for correctness, security, performance, and style issues. Produces a structured report of findings with severity classifications.

### Purpose

The code-review workflow is designed to provide detailed, actionable feedback on proposed code changes. It identifies the most significant issues that should be addressed before merging, prioritizing security vulnerabilities and functional errors over stylistic improvements.

### Inputs

- **diff** (git-diff, required)
  The git diff to review. Defaults to comparing HEAD~1 with HEAD.

- **codebase** (repository-ref, required)
  The repository containing the code being reviewed. Used for context and additional analysis.

- **document_ref** (document-ref, optional)
  Optional reference document (e.g., style guide or architecture document) to inform the review.

### Outputs

Returns a JSON object with the following structure:

```json
{
  "findings": [
    {
      "file": "path/to/file.py",
      "line": 42,
      "severity": "warning",
      "category": "security",
      "description": "SQL injection vulnerability detected in query construction",
      "suggestion": "Use parameterized queries instead of string concatenation"
    }
  ],
  "summary": "The diff introduces one security issue and improves performance in two areas.",
  "confidence": 0.95
}
```

- **findings** (array)
  List of identified issues, each with file path, line number, severity level, category, description, and suggested fix.

- **summary** (string)
  Overall assessment of the diff in 2-3 sentences.

- **confidence** (number, 0.0–1.0)
  Reviewer confidence in the findings. Higher values indicate greater certainty.

### Side Effects

Prints summary comment to stdout.

### Output Paths

- `review.json` — Full structured findings

### Model Configuration

- **Provider:** Anthropic
- **Model:** claude-sonnet-4-20250514
- **Temperature:** 0.2 (deterministic, focused review)
- **Max Tokens:** 8192
- **System Prompt:** prompts/code-review.md
- **Retry:** Up to 2 attempts with exponential backoff

### Safety

- **Timeout:** 300 seconds

### Tool Capabilities

- `repository:read` — Read-only access to the repository for context

### Budget

Maximum 10 findings per review.

### Example Usage

```bash
agentry run workflows/code-review.yaml \
  --input-diff $(git diff HEAD~1) \
  --input-codebase .
```

---

## bug-fix Workflow

**File:** `bug-fix.yaml`

Diagnoses reported bugs, identifies root causes, and suggests targeted fixes with confidence scores.

### Purpose

The bug-fix workflow assists in triaging and fixing software defects. Given a bug report and the repository, it investigates the codebase, identifies the root cause, and provides a specific, localized fix recommendation.

### Inputs

- **issue-description** (string, required)
  A detailed description of the bug or unexpected behavior to investigate.

- **repository-ref** (repository-ref, required)
  The repository to inspect for the root cause.

### Outputs

Returns a JSON object with the following structure:

```json
{
  "diagnosis": "File parsing fails on multiline strings due to improper escape handling",
  "root_cause": "The regex pattern in YAMLParser.parse() doesn't account for escaped newlines",
  "suggested_fix": {
    "file": "src/parser.py",
    "line": 127,
    "change": "Replace regex pattern with (?:\\\\[\\n]|[^\\n])+ to handle escaped newlines"
  },
  "confidence": 0.85
}
```

- **diagnosis** (string)
  Summary of the observed symptoms and affected subsystem.

- **root_cause** (string)
  The underlying cause of the bug.

- **suggested_fix** (object)
  - **file** — Path to the file requiring changes
  - **line** — Line number where the fix should be applied
  - **change** — Description of the code change required

- **confidence** (number, 0.0–1.0)
  Confidence in the diagnosis and suggested fix.

### Side Effects

Prints diagnosis summary to stdout.

### Output Paths

- `bug-fix-result.json` — Full diagnosis and fix recommendation

### Model Configuration

- **Provider:** Anthropic
- **Model:** claude-sonnet-4-20250514
- **Temperature:** 0.2 (deterministic analysis)
- **Max Tokens:** 4096
- **System Prompt:** prompts/bug-fix-system-prompt.md
- **Retry:** Up to 3 attempts with exponential backoff

### Safety

- **Timeout:** 300 seconds

### Tool Capabilities

- `repository:read` — Read-only access to the codebase
- `shell:execute` — Run diagnostic commands (tests, logs, etc.)

### Budget

Maximum 1 finding per diagnosis.

### Example Usage

```bash
agentry run workflows/bug-fix.yaml \
  --input-issue-description "Parser crashes on files with unicode comments" \
  --input-repository-ref .
```

---

## triage Workflow

**File:** `triage.yaml`

Classifies and triages software issues to assign severity, category, affected components, and recommended assignee.

### Purpose

The triage workflow automates issue classification, helping teams prioritize work and route issues to the right team members. It analyzes issue descriptions and codebase context to assign severity levels, categorize issues, and recommend assignment.

### Inputs

- **issue-description** (string, required)
  A description of the issue to triage.

- **repository-ref** (repository-ref, required)
  The repository to inspect for component context.

### Outputs

Returns a JSON object with the following structure:

```json
{
  "severity": "high",
  "category": "performance",
  "affected_components": ["query-engine", "cache-layer"],
  "recommended_assignee": "backend-team",
  "reasoning": "Database queries are being executed in loops, degrading performance. This affects the query-engine component and impacts API response times, warranting high priority."
}
```

- **severity** (string)
  Issue priority level: `critical`, `high`, `medium`, or `low`.

- **category** (string)
  Issue type, e.g., `bug`, `security`, `performance`, `usability`, `feature-request`.

- **affected_components** (array of strings)
  List of modules, services, or subsystems impacted by this issue.

- **recommended_assignee** (string)
  Team or role recommended to handle this issue.

- **reasoning** (string)
  Brief explanation of the severity and category decisions.

### Side Effects

Prints triage summary to stdout.

### Output Paths

- `triage-result.json` — Full triage assessment

### Model Configuration

- **Provider:** Anthropic
- **Model:** claude-sonnet-4-20250514
- **Temperature:** 0.2 (consistent classification)
- **Max Tokens:** 2048
- **System Prompt:** prompts/triage-system-prompt.md
- **Retry:** Up to 3 attempts with exponential backoff

### Safety

- **Timeout:** 120 seconds

### Tool Capabilities

- `repository:read` — Read-only access to the repository for component context

### Example Usage

```bash
agentry run workflows/triage.yaml \
  --input-issue-description "High CPU usage when processing large CSV files" \
  --input-repository-ref .
```

---

## Shared Infrastructure

All workflows share the following characteristics:

### Model Provider

All workflows use **Anthropic's Claude** (claude-sonnet-4-20250514) as the LLM provider with:
- Low temperature (0.2) for deterministic, focused responses
- Retry logic with exponential backoff for resilience
- Configurable max token limits to control output length

### Security Model

Each workflow enforces:
- **Output schema validation** — Responses must conform to the specified JSON schema
- **Output path enforcement** — Results are written to designated paths only
- **Resource limits** — Timeout constraints prevent runaway execution
- **Read-only tool access** — No destructive operations permitted

### Integration Points

Workflows are invoked via the Agentry CLI:

```bash
agentry run workflows/{workflow-name}.yaml [input options]
```

The CLI handles:
- YAML parsing and validation
- Input resolution and binding
- Tool execution and security enforcement
- Output validation and path enforcement

---

## Validation

All workflows are validated against the WorkflowDefinition schema. Validation checks include:

- **Structure:** Valid YAML with required blocks (identity, model, output)
- **Semantic correctness:** Input types are resolvable, tool capabilities are recognized
- **Output schemas:** JSON schemas are well-formed
- **Variable references:** System prompt variables reference valid inputs
- **Semver compliance:** Workflow version follows semantic versioning

To validate a workflow:

```bash
agentry validate workflows/code-review.yaml
```

Validation errors are reported with file paths, field paths, and remediation hints.
