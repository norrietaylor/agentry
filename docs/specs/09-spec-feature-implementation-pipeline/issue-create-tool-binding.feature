# Source: docs/specs/09-spec-feature-implementation-pipeline/09-spec-feature-implementation-pipeline.md
# Pattern: API
# Recommended test type: Integration

Feature: issue:create Tool Binding

  Scenario: GitHubActionsBinder creates an issue via the GitHub API
    Given the GitHubActionsBinder is configured with a valid GITHUB_TOKEN
    And the repository is "owner/repo"
    When the agent invokes issue_create with title "New sub-issue" and body "Description of work" and labels ["category:feature", "agent-decomposed"]
    Then a POST request is sent to "https://api.github.com/repos/owner/repo/issues"
    And the request body contains the title "New sub-issue"
    And the request body contains the body "Description of work"
    And the request body contains labels ["category:feature", "agent-decomposed"]
    And the response contains a "number" field with an integer value
    And the response contains a "url" field with a string value
    And the response contains "status": "created"

  Scenario: GitHubActionsBinder handles missing labels gracefully
    Given the GitHubActionsBinder is configured with a valid GITHUB_TOKEN
    When the agent invokes issue_create with title "Simple issue" and body "No labels needed" and labels set to None
    Then the POST request body does not include a "labels" field
    And the response contains "status": "created"

  Scenario: GitHubActionsBinder returns error details on 403 forbidden
    Given the GitHubActionsBinder is configured with a token that lacks issue write permissions
    When the agent invokes issue_create with title "Unauthorized" and body "Should fail"
    Then the tool returns an error response with status code 403
    And the error message includes a remediation hint about required permissions

  Scenario: GitHubActionsBinder returns error details on 404 not found
    Given the GitHubActionsBinder is configured for a non-existent repository
    When the agent invokes issue_create with title "Missing repo" and body "Should fail"
    Then the tool returns an error response with status code 404
    And the error message includes a remediation hint about repository existence

  Scenario: LocalBinder stub prints metadata and returns placeholder
    Given the LocalBinder is initialized
    When the agent invokes issue_create with title "Local test" and body "Testing locally"
    Then the tool prints the issue title and body to stdout
    And the tool returns a placeholder response with "number": 0 and "status": "created"

  Scenario: issue:create is registered in SUPPORTED_TOOLS for both binders
    Given the GitHubActionsBinder class is loaded
    And the LocalBinder class is loaded
    When the SUPPORTED_TOOLS set is inspected on each binder
    Then both binders include "issue:create" in their SUPPORTED_TOOLS
