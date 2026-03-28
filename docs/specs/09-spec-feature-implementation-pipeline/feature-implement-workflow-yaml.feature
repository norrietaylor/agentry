# Source: docs/specs/09-spec-feature-implementation-pipeline/09-spec-feature-implementation-pipeline.md
# Pattern: CLI/Process + State
# Recommended test type: Integration

Feature: Feature-Implement Workflow YAML

  Scenario: Workflow runs against a small feature issue and produces valid JSON output
    Given a local repository with the agentry CLI installed
    And the file "workflows/feature-implement.yaml" exists with identity "feature-implement"
    When the user runs "agentry run workflows/feature-implement.yaml --input issue-description='Add a --verbose flag to the CLI' --input repository-ref=. --output-format json"
    Then the command exits with code 0
    And stdout contains valid JSON with an "action" field
    And the "action" field is one of "implemented" or "decomposed"

  Scenario: Workflow uses elevated trust and correct model settings
    Given a local repository with the agentry CLI installed
    When the user runs "agentry run workflows/feature-implement.yaml --dry-run"
    Then the output shows the agent runtime is "claude-code"
    And the output shows the model is "claude-sonnet-4-20250514"
    And the output shows the trust level is "elevated"

  Scenario: Workflow accepts issue-description input with fallback
    Given a local repository with the agentry CLI installed
    When the user runs "agentry run workflows/feature-implement.yaml --input repository-ref=." without providing issue-description
    Then the command reports that the required input "issue-description" is missing

  Scenario: System prompt file is present and contains implementation instructions
    Given a local repository with the agentry CLI installed
    When the user runs "agentry run workflows/feature-implement.yaml --input issue-description='Test' --input repository-ref=. --dry-run"
    Then the resolved system prompt references reading issue body and planning-pipeline comments
    And the resolved system prompt references the scope heuristic for single-PR feasibility
    And the resolved system prompt references creating sub-issues for large features

  Scenario: Workflow declares issue:create in its tool manifest
    Given a local repository with the agentry CLI installed
    When the user runs "agentry run workflows/feature-implement.yaml --show-tools"
    Then the tool list includes "repository:read"
    And the tool list includes "shell:execute"
    And the tool list includes "pr:create"
    And the tool list includes "issue:comment"
    And the tool list includes "issue:label"
    And the tool list includes "issue:create"

  Scenario: Output schema includes reasoning field
    Given a local repository with the agentry CLI installed
    And the workflow has completed against a small feature issue
    When the JSON output is parsed
    Then the output contains a "reasoning" field with a non-empty string
