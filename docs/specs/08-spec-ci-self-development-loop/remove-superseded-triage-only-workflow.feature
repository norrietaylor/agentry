# Source: docs/specs/08-spec-ci-self-development-loop/08-spec-ci-self-development-loop.md
# Pattern: CLI/Process + State
# Recommended test type: Integration

Feature: Remove Superseded Triage-Only Workflow

  Scenario: Triage-only workflow file no longer exists after cleanup
    Given the planning pipeline workflow has been deployed at ".github/workflows/agentry-planning-pipeline.yml"
    When the cleanup of superseded workflows is complete
    Then the file ".github/workflows/agentry-issue-triage.yml" does not exist in the repository

  Scenario: Planning pipeline workflow notes that it replaces the triage-only workflow
    Given the planning pipeline workflow at ".github/workflows/agentry-planning-pipeline.yml"
    When the workflow file content is read
    Then the top-level comment states that this workflow replaces the triage-only workflow

  Scenario: No remaining references to the deleted triage workflow
    Given the triage-only workflow file has been deleted
    When searching across all workflow files and documentation for "agentry-issue-triage"
    Then zero matches are found in the ".github/" directory
    And zero matches are found in the "docs/" directory
