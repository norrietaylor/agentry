# Source: docs/specs/04-spec-agentry-ci/04-spec-agentry-ci.md
# Pattern: CLI/Process + State
# Recommended test type: Integration

Feature: CI Runtime Shim and End-to-End Integration

  Scenario: Auto-detect GitHub Actions environment selects github-actions binder
    Given the environment variable GITHUB_ACTIONS is set to "true"
    And no explicit --binder flag is provided
    When the user runs "agentry run workflows/code-review.yaml"
    Then the github-actions binder is selected automatically
    And the run proceeds with GitHubActionsBinder resolving inputs

  Scenario: Default environment selects local binder
    Given the environment variable GITHUB_ACTIONS is not set
    And no explicit --binder flag is provided
    When the user runs "agentry run workflows/code-review.yaml"
    Then the local binder is selected automatically

  Scenario: Explicit --binder flag overrides auto-detection
    Given the environment variable GITHUB_ACTIONS is set to "true"
    When the user runs "agentry run --binder local workflows/code-review.yaml"
    Then the local binder is selected despite the GitHub Actions environment

  Scenario: Preflight checks include token scope check for github-actions binder
    Given the environment variable GITHUB_ACTIONS is set to "true"
    And the github-actions binder is active
    When the preflight checks are enumerated
    Then GitHubTokenScopeCheck is in the preflight check list

  Scenario: Preflight checks exclude token scope check for local binder
    Given the local binder is active
    When the preflight checks are enumerated
    Then GitHubTokenScopeCheck is not in the preflight check list

  Scenario: generate_pipeline_config returns structured dict for template rendering
    Given a workflow definition with identity "code-review" and tools "repository:read" and "pr:comment"
    And triggers are configured as "pull_request"
    When GitHubActionsBinder.generate_pipeline_config() is called
    Then the returned dict contains key "name" with a value derived from "code-review"
    And the returned dict contains key "on" with the trigger configuration
    And the returned dict contains key "permissions" with "contents: read" and "pull-requests: write"
    And the returned dict contains key "jobs" with a steps list

  Scenario: End-to-end generation produces valid YAML from standard workflow
    Given a standard library workflow definition at "workflows/code-review.yaml"
    When "agentry ci generate --target github workflows/code-review.yaml" is run
    Then the generated file is valid YAML
    And the YAML "jobs.agentry.steps" array includes a step running "agentry run" with the workflow path
    And the YAML "on" section contains the configured trigger
    And the YAML "permissions" section matches the workflow tool manifest

  Scenario: Entry point is registered in pyproject.toml
    Given the project pyproject.toml file
    When the agentry.binders entry point group is inspected
    Then an entry "github-actions" pointing to "agentry.binders.github_actions:GitHubActionsBinder" is present
