# Source: docs/specs/04-spec-agentry-ci/04-spec-agentry-ci.md
# Pattern: API + Error handling
# Recommended test type: Unit

Feature: GitHub Token Scope Verification

  Scenario: Token with sufficient scopes passes preflight check
    Given a workflow declaring tools "repository:read" and "pr:comment"
    And GITHUB_TOKEN has "contents:read" and "pull-requests:write" permissions
    When the GitHubTokenScopeCheck runs
    Then the PreflightResult status is "pass"
    And the check name is "GitHubTokenScopeCheck"

  Scenario: Token missing required scope fails with remediation message
    Given a workflow declaring tools "repository:read" and "pr:comment"
    And GITHUB_TOKEN lacks "pull-requests:write" permission
    When the GitHubTokenScopeCheck runs
    Then the PreflightResult status is "fail"
    And the failure message identifies "pull-requests:write" as the missing scope
    And the failure message identifies "pr:comment" as the tool requiring that scope
    And the remediation guidance includes "Add `permissions: pull-requests: write` to your GitHub Actions workflow YAML"

  Scenario: Token missing multiple scopes reports all missing scopes
    Given a workflow declaring tools "pr:comment" and "pr:review"
    And GITHUB_TOKEN lacks "pull-requests:write" permission
    When the GitHubTokenScopeCheck runs
    Then the PreflightResult status is "fail"
    And the failure message lists all tools requiring the missing scope

  Scenario: Scope check is skipped when GITHUB_TOKEN is not set
    Given the environment variable GITHUB_TOKEN is not set
    And the binder is not "github-actions"
    When the GitHubTokenScopeCheck runs
    Then the PreflightResult status is "pass"
    And the check is effectively skipped since it is not relevant outside CI

  Scenario: Tool-to-scope mapping covers all supported tools
    Given a workflow declaring tool "repository:read"
    When the required scopes are computed
    Then "contents:read" is in the required scopes list

  Scenario: PR review tool maps to pull-requests write scope
    Given a workflow declaring tool "pr:review"
    When the required scopes are computed
    Then "pull-requests:write" is in the required scopes list

  Scenario: Scope verification detects 403 from test API call
    Given a workflow declaring tool "pr:comment"
    And the GitHub API returns 403 for the test operation
    When the GitHubTokenScopeCheck runs
    Then the PreflightResult status is "fail"
    And the failure message indicates insufficient token permissions
