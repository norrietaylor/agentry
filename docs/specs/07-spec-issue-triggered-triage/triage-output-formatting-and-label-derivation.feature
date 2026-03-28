# Source: docs/specs/07-spec-issue-triggered-triage/07-spec-issue-triggered-triage.md
# Pattern: API + CLI/Process
# Recommended test type: Integration

Feature: Triage Output Formatting and Label Derivation

  Scenario: map_outputs detects issues event and posts output as issue comment
    Given a GitHubActionsBinder configured with an issues event payload for issue number 15
    And a triage workflow run has produced structured output
    When map_outputs is called with the triage results
    Then a comment is posted to issue number 15 via the GitHub API
    And the comment body is formatted as Markdown

  Scenario: Triage comment renders severity badge category and reasoning
    Given triage output with severity "high", category "bug", affected components "auth, login", recommended assignee "backend-team", and reasoning "Server error on password reset flow"
    When the output formatter renders the triage comment
    Then the Markdown comment contains a severity badge showing "high"
    And the comment contains the category "bug"
    And the comment contains the affected components list "auth, login"
    And the comment contains the recommended assignee "backend-team"
    And the comment contains the reasoning text

  Scenario: Labels are derived from severity and category in triage output
    Given triage output JSON with severity "medium" and category "feature-request"
    And a GitHubActionsBinder configured with an issues event payload
    When map_outputs processes the triage results
    Then the issue:label tool is called with labels "severity:medium" and "category:feature-request"

  Scenario: Label application failure does not fail the workflow run
    Given triage output with severity "high" and category "bug"
    And the GitHub API returns a 403 error for the labels endpoint
    When map_outputs processes the triage results
    Then a warning is logged about the label application failure
    And the triage comment is still posted successfully
    And the workflow run completes without error

  Scenario: Execution record is written to runs directory
    Given a triage workflow run has completed with structured output
    When map_outputs finishes processing
    Then an execution record is written to .agentry/runs/{run_id}/
    And the record contains the triage output data
