# Source: docs/specs/09-spec-feature-implementation-pipeline/09-spec-feature-implementation-pipeline.md
# Pattern: CLI/Process + State
# Recommended test type: Integration

Feature: Label-Triggered Feature CI Workflow

  Scenario: CI workflow triggers when category:feature label is applied to an issue
    Given the repository has the file ".github/workflows/agentry-feature-implement.yml"
    And an issue exists with no labels
    When the label "category:feature" is applied to the issue
    Then the agentry-feature-implement workflow is triggered
    And the workflow runs the agentry CLI with the feature-implement workflow YAML

  Scenario: CI workflow does not trigger for unrelated labels
    Given the repository has the file ".github/workflows/agentry-feature-implement.yml"
    And an issue exists with no labels
    When the label "priority:high" is applied to the issue
    Then the agentry-feature-implement workflow is not triggered

  Scenario: CI workflow has correct permissions for feature implementation
    Given the repository has the file ".github/workflows/agentry-feature-implement.yml"
    When the workflow YAML is parsed
    Then the permissions include "contents: write"
    And the permissions include "issues: write"
    And the permissions include "pull-requests: write"

  Scenario: CI workflow passes required secrets to the agentry command
    Given the repository has the file ".github/workflows/agentry-feature-implement.yml"
    And the repository has secrets CLAUDE_CODE_OAUTH_TOKEN and GITHUB_TOKEN configured
    When the agentry-feature-implement workflow runs
    Then the agentry CLI process receives the CLAUDE_CODE_OAUTH_TOKEN secret
    And the agentry CLI process receives the GITHUB_TOKEN secret

  Scenario: CI workflow uses github-actions binder and JSON output format
    Given the repository has the file ".github/workflows/agentry-feature-implement.yml"
    When the workflow YAML is parsed for the agentry run command
    Then the command includes "--binder github-actions"
    And the command includes "--output-format json"
