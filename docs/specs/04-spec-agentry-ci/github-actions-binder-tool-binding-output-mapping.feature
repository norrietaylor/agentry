# Source: docs/specs/04-spec-agentry-ci/04-spec-agentry-ci.md
# Pattern: API + Error handling
# Recommended test type: Unit

Feature: GitHubActionsBinder -- Tool Binding and Output Mapping

  Scenario: Repository-read tool reads files from GITHUB_WORKSPACE
    Given the environment variable GITHUB_WORKSPACE is set to a temporary directory containing "src/main.py"
    And the GitHubActionsBinder has bound tools for the workflow
    When the "repository:read" tool is invoked with path "src/main.py"
    Then the tool returns the file contents from the GITHUB_WORKSPACE directory

  Scenario: Repository-read tool blocks path traversal attempts
    Given the environment variable GITHUB_WORKSPACE is set to a temporary directory
    And the GitHubActionsBinder has bound tools for the workflow
    When the "repository:read" tool is invoked with path "../../etc/passwd"
    Then the tool raises an error indicating path traversal is not allowed
    And no file outside GITHUB_WORKSPACE is accessed

  Scenario: Shell-execute tool enforces read-only command allowlist
    Given the GitHubActionsBinder has bound tools for the workflow
    When the "shell:execute" tool is invoked with a disallowed command
    Then the tool raises an error indicating the command is not in the allowlist

  Scenario: PR comment tool posts comment via GitHub API
    Given a pull_request event payload with PR number 42
    And the environment variable GITHUB_REPOSITORY is set to "owner/repo"
    And the environment variable GITHUB_TOKEN is set to a valid token
    And the GitHubActionsBinder has bound tools for the workflow
    When the "pr:comment" tool is invoked with body "Review complete: no issues found"
    Then a POST request is made to "/repos/owner/repo/issues/42/comments"
    And the request body contains "Review complete: no issues found"

  Scenario: PR review tool creates review via GitHub API
    Given a pull_request event payload with PR number 42
    And the environment variable GITHUB_REPOSITORY is set to "owner/repo"
    And the GitHubActionsBinder has bound tools for the workflow
    When the "pr:review" tool is invoked with an approval review body
    Then a POST request is made to "/repos/owner/repo/pulls/42/reviews"

  Scenario: Unsupported tool name raises UnsupportedToolError
    Given the GitHubActionsBinder has bound tools for the workflow
    When a tool named "database:query" is requested
    Then an UnsupportedToolError is raised
    And the error message identifies "database:query" as unsupported

  Scenario: Output mapping writes to run output file and posts PR comment
    Given a pull_request event with PR number 42
    And the environment variable GITHUB_WORKSPACE is set to a temporary directory
    And a completed agent run with output data and a run ID
    When the GitHubActionsBinder maps the outputs
    Then a file is created at "$GITHUB_WORKSPACE/.agentry/runs/<run_id>/output.json" containing the output data
    And a PR comment is posted with the agent output content

  Scenario: GitHub API 403 error includes scope remediation guidance
    Given the GitHubActionsBinder is configured for a pull_request event
    And the GitHub API returns a 403 Forbidden response for a comment POST
    When the "pr:comment" tool is invoked
    Then a structured error is raised containing the HTTP status 403
    And the error message includes "GITHUB_TOKEN may lack `pull_requests:write` scope"

  Scenario: GitHub API 404 error reports PR not found
    Given the GitHubActionsBinder is configured for a pull_request event
    And the GitHub API returns a 404 Not Found response
    When the "pr:comment" tool is invoked
    Then a structured error is raised containing the HTTP status 404
    And the error message includes the repository and PR number that was not found

  Scenario: GitHub API network timeout produces structured error
    Given the GitHubActionsBinder is configured for a pull_request event
    And the GitHub API connection times out
    When the "pr:comment" tool is invoked
    Then a structured error is raised indicating a network timeout
    And the error message includes suggested remediation
