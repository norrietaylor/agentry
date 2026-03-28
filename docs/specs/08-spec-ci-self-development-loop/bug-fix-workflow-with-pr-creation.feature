# Source: docs/specs/08-spec-ci-self-development-loop/08-spec-ci-self-development-loop.md
# Pattern: CLI/Process
# Recommended test type: Integration

Feature: Bug-Fix Workflow with PR Creation

  Scenario: Bug-fix workflow uses agent block with iteration support
    Given the bug-fix workflow YAML at "workflows/bug-fix.yaml"
    When agentry validates the workflow definition
    Then the workflow definition uses an "agent:" block instead of a "model:" block
    And the agent block specifies max_iterations of 3

  Scenario: Bug-fix workflow resolves issue description from source mapping
    Given the bug-fix workflow YAML defines input "issue-description" with source "issue.body" and fallback "issue.title"
    When the workflow runs with an issue body "UserProfile crashes when avatar is null"
    Then the agent receives "UserProfile crashes when avatar is null" as the issue description

  Scenario: Bug-fix agent produces diagnosis with structured output
    Given a repository with a known bug in the codebase
    And the bug-fix workflow is configured with output-format json
    When the user runs "agentry --output-format json run workflows/bug-fix.yaml --input issue-description='X returns 500' --input repository-ref=."
    Then the command exits with code 0
    And the JSON output contains a "diagnosis" field
    And the JSON output contains a "root_cause" field
    And the JSON output contains a "suggested_fix" field
    And the JSON output contains a "confidence" field

  Scenario: Bug-fix agent opens a PR referencing the originating issue
    Given the bug-fix workflow runs against issue number 42
    And the agent has diagnosed and committed a fix
    When the agent creates a pull request via pr:create
    Then the PR body contains "Fixes #42"
    And the PR has the "agent-proposed" label applied

  Scenario: Bug-fix agent comments on the original issue after creating a PR
    Given the bug-fix workflow runs against issue number 42
    And the agent has created a fix PR numbered 55
    When the agent posts a follow-up comment on the issue
    Then the comment on issue 42 contains a link to PR 55

  Scenario: Bug-fix workflow includes pr:create and issue:comment capabilities
    Given the bug-fix workflow YAML at "workflows/bug-fix.yaml"
    When agentry validates the workflow definition
    Then the workflow capabilities include "pr:create"
    And the workflow capabilities include "issue:comment"
