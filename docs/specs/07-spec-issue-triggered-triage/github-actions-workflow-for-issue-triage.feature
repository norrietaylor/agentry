# Source: docs/specs/07-spec-issue-triggered-triage/07-spec-issue-triggered-triage.md
# Pattern: CLI/Process
# Recommended test type: Integration

Feature: GitHub Actions Workflow for Issue Triage

  Scenario: Workflow triggers on new issue opened events
    Given the file .github/workflows/agentry-issue-triage.yml exists in the repository
    When a new issue is opened in the repository
    Then the agentry-issue-triage workflow is triggered
    And the workflow runs the triage pipeline against the issue

  Scenario: Workflow follows established CI structure
    Given the agentry-issue-triage.yml workflow file
    When the workflow runs on an issues opened event
    Then it checks out the repository
    And it sets up Python
    And it installs Claude Code
    And it installs agentry
    And it executes the agentry run command for triage

  Scenario: Workflow invokes agentry with correct arguments
    Given the agentry-issue-triage workflow is triggered by an issue with body "App crashes on startup"
    When the agentry run step executes
    Then the command includes "workflows/triage.yaml" as the workflow target
    And the command passes the issue body as --input issue-description
    And the command includes --binder github-actions
    And the command includes --output-format json

  Scenario: Workflow has correct permissions scoped for commenting and labeling
    Given the agentry-issue-triage.yml workflow file
    When the workflow permissions are evaluated
    Then contents permission is set to read
    And issues permission is set to write

  Scenario: Workflow requires necessary secrets
    Given the agentry-issue-triage.yml workflow file
    When the workflow runs
    Then the CLAUDE_CODE_OAUTH_TOKEN secret is available to the run step
    And the GITHUB_TOKEN secret is available for API access

  Scenario: Triage workflow YAML includes issue tool capabilities
    Given the workflows/triage.yaml workflow definition
    When the tools.capabilities list is inspected at runtime
    Then the capabilities include "issue:comment"
    And the capabilities include "issue:label"

  Scenario: Manual local triage run returns valid structured output
    Given the agentry CLI is installed and workflows/triage.yaml exists
    When the user runs "agentry run workflows/triage.yaml --input issue-description='Login page returns 500 after password reset' --input repository-ref=. --output-format json"
    Then the command exits with code 0
    And the JSON output contains a "severity" field with a valid severity value
    And the JSON output contains a "category" field
    And the JSON output contains a "reasoning" field
