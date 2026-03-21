# Source: docs/specs/01-spec-agentry-cli/01-spec-agentry-cli.md
# Pattern: CLI/Process + Error handling
# Recommended test type: Integration

Feature: Output Validation Pipeline

  Scenario: Layer 1 validates agent output against declared JSON Schema
    Given a workflow with an output schema requiring fields "findings", "summary", and "confidence"
    And the agent produces output matching that schema
    When the output validation pipeline runs
    Then Layer 1 (Schema Validation) passes
    And the validation result shows layer 1 passed

  Scenario: Layer 1 rejects schema-invalid output with structured error
    Given a workflow with an output schema requiring a "findings" array
    And the agent produces output with "findings" as a string instead of an array
    When the output validation pipeline runs
    Then Layer 1 (Schema Validation) fails
    And the error indicates the schema path, failed keyword, and a human-readable message
    And processing halts without running Layer 2 or Layer 3

  Scenario: Layer 2 blocks undeclared side effects
    Given a workflow with an empty side_effects allowlist
    And the agent attempted a tool invocation that produces an external state change
    When the output validation pipeline runs Layer 2
    Then the undeclared side effect is blocked
    And the error reports which side effect was attempted and that it is not in the allowlist

  Scenario: Layer 2 allows declared side effects
    Given a workflow with "file:write" in the side_effects allowlist
    And the agent attempted a file:write tool invocation
    When the output validation pipeline runs Layer 2
    Then Layer 2 (Side-Effect Allowlist) passes

  Scenario: Layer 3 blocks writes to undeclared output paths
    Given a workflow with output_paths declaring only ".agentry/runs/"
    And the agent attempts to write to "/tmp/unauthorized-path"
    When the output validation pipeline runs Layer 3
    Then the write is blocked
    And the error reports the undeclared path and lists the allowed output paths

  Scenario: Layer 3 allows writes to declared output paths
    Given a workflow with output_paths declaring ".agentry/runs/"
    And the agent writes output to ".agentry/runs/<timestamp>/output.json"
    When the output validation pipeline runs Layer 3
    Then Layer 3 (Output Path Enforcement) passes

  Scenario: Layers execute in sequence and halt on first failure
    Given a workflow with strict output schema and side-effect allowlist
    And the agent produces output that fails schema validation
    When the output validation pipeline runs
    Then only Layer 1 is evaluated
    And the validation result contains layer_results with Layer 1 failed and Layers 2 and 3 not executed

  Scenario: Validation result is included in execution record
    Given a completed workflow execution with all three validation layers passing
    When the execution record is written
    Then the file contains a validation_result with validation_status and layer_results for all three layers
    And each layer_result shows passed as true

  Scenario: Output truncated when findings exceed max_findings budget
    Given a workflow with output.budget.max_findings set to 10
    And the agent produces 15 findings in its output
    When the output validation pipeline runs
    Then the output is truncated to 10 findings
    And the output includes a note indicating that 5 findings were truncated

  Scenario: Validated output emits human-readable in TTY and JSON when piped
    Given a successful workflow execution with validated output
    When the output is emitted to an interactive terminal
    Then the output is displayed in human-readable format with color coding
    When the output is piped to another process
    Then the output is valid JSON matching the workflow's output schema
