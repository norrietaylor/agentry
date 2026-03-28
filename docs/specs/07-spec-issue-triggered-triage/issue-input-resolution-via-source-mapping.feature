# Source: docs/specs/07-spec-issue-triggered-triage/07-spec-issue-triggered-triage.md
# Pattern: CLI/Process + API
# Recommended test type: Integration

Feature: Issue Input Resolution via Source Mapping

  Scenario: issue-description input resolves from issue body via source mapping
    Given an issues event payload with issue body "Login page returns 500 after password reset"
    And the triage workflow YAML defines issue-description input with source "issue.body"
    When the GitHubActionsBinder resolves inputs for the workflow
    Then the issue-description input resolves to "Login page returns 500 after password reset"

  Scenario: Fallback to issue title when issue body is null
    Given an issues event payload with a null issue body and title "Bug: login broken"
    And the triage workflow YAML defines issue-description input with source "issue.body"
    When the GitHubActionsBinder resolves inputs for the workflow
    Then the issue-description input resolves to "Bug: login broken"
    And a warning is logged indicating fallback to issue title

  Scenario: Fallback to issue title when issue body is empty string
    Given an issues event payload with an empty string issue body and title "Feature request: dark mode"
    And the triage workflow YAML defines issue-description input with source "issue.body"
    When the GitHubActionsBinder resolves inputs for the workflow
    Then the issue-description input resolves to "Feature request: dark mode"
    And a warning is logged indicating fallback to issue title

  Scenario: repository-ref input continues resolving to GITHUB_WORKSPACE
    Given an issues event payload
    And the GITHUB_WORKSPACE environment variable is set to "/home/runner/work/repo"
    When the GitHubActionsBinder resolves the repository-ref input
    Then the repository-ref input resolves to "/home/runner/work/repo"

  Scenario: CLI --input override takes precedence over source mapping
    Given an issues event payload with issue body "Original issue description"
    And the triage workflow defines issue-description input with source "issue.body"
    When agentry run is invoked with --input issue-description="Override description"
    Then the issue-description input resolves to "Override description"
    And the event payload value is not used

  Scenario: Local CLI run produces valid triage JSON output
    Given the agentry CLI is installed and the triage workflow exists
    When the user runs "agentry run workflows/triage.yaml --input issue-description='test issue' --input repository-ref=. --output-format json"
    Then the command exits with code 0
    And the JSON output contains fields "severity", "category", "affected_components", "recommended_assignee", and "reasoning"
